"""Grants: a person's standing with a provider — a token today, an OAuth sign-in to come.

A grant is the person's, not a bundle's: made once in their account settings, usable by
any connection they set up, in any team. A connection made with it keeps syncing with it
into a bundle other people read — that is the point, and it is the person's choice: they
put their credential to work for that bundle. Deleting a grant is always allowed (the
credential is theirs to revoke); connections that used it keep their rows and report
"the sign-in this connection used is gone" at their next sync, rather than lose a
bundle's files because one person left.

User-facing, a grant is a "sign-in". In code it is a grant, because `accounts.py` is
already signing people in to Mindkeep itself.
"""

import json
import secrets as random
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app import vault
from app.auth import CurrentUser
from app.connectors import ConnectorError, registry
from app.connectors.base import Grant as GrantOf
from app.db import Connection, Grant, session

router = APIRouter()


class NewGrant(BaseModel):
    kind: str
    secrets: dict[str, str]


def as_dict(row: Grant, uses: int) -> dict[str, object]:
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label,
        "created_at": row.created_at.isoformat(),
        "error": row.error,
        "uses": uses,
    }


def mine(sub: str) -> list[Grant]:
    with session() as s:
        rows = s.scalars(select(Grant).where(Grant.sub == sub).order_by(Grant.created_at)).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def held(sub: str, grant_id: str) -> Grant | None:
    """A grant of this person's, or None — someone else's is not there."""
    with session() as s:
        row = s.get(Grant, grant_id)
        if row is None or row.sub != sub:
            return None
        s.expunge(row)
        return row


def unpack(row: Grant | None) -> GrantOf | None:
    """The grant as a connector receives it: secrets in the clear, for this call only."""
    if row is None:
        return None
    return GrantOf(kind=row.kind, label=row.label, secrets=vault.unseal_all(row.secret))


def uses_of(s: Any, grant_ids: list[str]) -> dict[str, int]:
    if not grant_ids:
        return {}
    rows = s.execute(
        select(Connection.grant_id, func.count())
        .where(Connection.grant_id.in_(grant_ids))
        .group_by(Connection.grant_id)
    ).all()
    return {str(g): int(n) for g, n in rows}


@router.get("/grants")
def list_grants(user: CurrentUser) -> list[dict[str, object]]:
    """Your sign-ins, with how many connections use each."""
    rows = mine(user)
    with session() as s:
        uses = uses_of(s, [r.id for r in rows])
    return [as_dict(r, uses.get(r.id, 0)) for r in rows]


@router.post("/grants", status_code=201)
def add_grant(user: CurrentUser, new: NewGrant) -> dict[str, object]:
    """A token, tried before it is kept: the connector says what to call it."""
    connector = registry().get(new.kind)
    if connector is None:
        raise HTTPException(404, f"no connector of kind {new.kind}")
    if connector.auth != "token":
        raise HTTPException(
            400,
            "no sign-in needed"
            if connector.auth == "none"
            else f"{connector.title} signs in through the provider, which Mindkeep cannot do yet",
        )
    given = {f.name: new.secrets.get(f.name, "").strip() for f in connector.grant_fields}
    for f in connector.grant_fields:
        if f.required and not given[f.name]:
            raise HTTPException(400, f"{f.label} is required")
    try:
        label = connector.check_grant(given)
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e
    row = Grant(
        id=random.token_hex(16),
        sub=user,
        kind=new.kind,
        label=label,
        secret=vault.seal_all(given),
        created_at=datetime.now(UTC),
    )
    with session() as s:
        s.add(row)
        s.commit()
        return as_dict(row, 0)


@router.delete("/grants/{grant_id}")
def remove_grant(user: CurrentUser, grant_id: str) -> dict[str, object]:
    """Gone at once. Connections that used it stay, and say so at their next sync."""
    with session() as s:
        row = s.get(Grant, grant_id)
        if row is None or row.sub != user:
            raise HTTPException(404, "no such sign-in")
        uses = uses_of(s, [grant_id]).get(grant_id, 0)
        s.delete(row)
        s.commit()
    return {"deleted": grant_id, "orphaned": uses}


def redacted(row: Grant) -> dict[str, str]:
    """What a browser may see of a grant's secrets: only that they are set."""
    return dict.fromkeys(json.loads(row.secret or "{}"), vault.REDACTED)
