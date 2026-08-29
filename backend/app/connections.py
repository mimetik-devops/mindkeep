"""Connections: a connector, configured, on a bundle.

A *connector* is code — the `url` built-in, a Notion plugin (`app/connectors/`). A
*connection* is one of them set up on one bundle with its own settings, secrets and
schedule: "the team wiki in Notion", "the pricing sheet at this URL". Its files land under
`raw/connectors/<name>/` and are kept in step by `syncing.py`.

Managing connections is the `bundles` permission — they hold credentials and decide what
flows into the wiki; owners and admins. Asking for a sync now is `write`.
"""

import logging
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import syncing, vault
from app.auth import CurrentUser
from app.connectors import ConnectorError, registry
from app.db import Connection, session
from app.files import Bundle, Manager, Writer, record
from app.ingest import enqueue, pages_citing

log = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}")

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
EVERY_MIN, EVERY_MAX, EVERY_DEFAULT = 15, 7 * 24 * 60, 60  # minutes


class NewConnection(BaseModel):
    kind: str
    name: str
    config: dict[str, str]
    every: int = EVERY_DEFAULT


class ConnectionPatch(BaseModel):
    config: dict[str, str] | None = None
    every: int | None = None
    enabled: bool | None = None


def background(fn: Callable[..., Any], *args: Any) -> None:
    """A sync runs off the request. Tests replace this to run inline."""
    threading.Thread(target=fn, args=args, daemon=True, name="sync").start()


def catalog() -> list[dict[str, object]]:
    """Every connector installed, for the picker. `available` is false for a kind whose
    auth the plumbing cannot do yet — listed so a person knows it exists, not offered."""
    return [
        {
            "kind": c.kind,
            "title": c.title,
            "blurb": c.blurb,
            "auth": c.auth,
            "available": c.auth != "oauth2",
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "secret": f.secret,
                    "help": f.help,
                    "required": f.required,
                }
                for f in c.fields
            ],
        }
        for c in sorted(registry().values(), key=lambda c: c.title)
    ]


def as_dict(row: Connection) -> dict[str, object]:
    connector = registry().get(row.kind)
    return {
        "id": row.id,
        "kind": row.kind,
        "name": row.name,
        "folder": syncing.folder(row),
        "config": vault.redact(row.config, connector) if connector else {},
        "every": row.every,
        "enabled": row.enabled,
        "syncing": syncing.active(row.id),
        "synced_at": row.synced_at.isoformat() if row.synced_at else "",
        "error": row.error,
        "summary": row.summary,
        "installed": connector is not None,
    }


def _where(home: Path) -> tuple[str, str]:
    return home.parent.name, home.name


def _every(every: int) -> int:
    if not EVERY_MIN <= every <= EVERY_MAX:
        raise HTTPException(400, f"sync every {EVERY_MIN} minutes to {EVERY_MAX // 1440} days")
    return every


def _checked(connector: Any, config: dict[str, str]) -> None:
    for f in connector.fields:
        if f.required and not config.get(f.name, "").strip():
            raise HTTPException(400, f"{f.label} is required")
    try:
        connector.check(config)
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e


def _get(s: Any, home: Path, connection_id: str) -> Connection:
    tenant, bundle = _where(home)
    row: Connection | None = s.get(Connection, connection_id)
    if row is None or (row.tenant, row.bundle) != (tenant, bundle):
        raise HTTPException(404, "no such connection")
    return row


@router.get("/connectors")
def list_connectors(_: CurrentUser) -> list[dict[str, object]]:
    return catalog()


@router.get("/bundles/{name}/connections")
def list_connections(home: Bundle) -> list[dict[str, object]]:
    tenant, bundle = _where(home)
    with session() as s:
        rows = s.scalars(
            select(Connection)
            .where(Connection.tenant == tenant, Connection.bundle == bundle)
            .order_by(Connection.created_at)
        ).all()
        return [as_dict(r) for r in rows]


@router.post("/bundles/{name}/connections", status_code=201)
def add_connection(
    home: Bundle, user: CurrentUser, _: Manager, new: NewConnection
) -> dict[str, object]:
    """Set a connection up: the credentials are tried first (the connector's `check`),
    then the row is made and its first sync started."""
    connector = registry().get(new.kind)
    if connector is None:
        raise HTTPException(404, f"no connector of kind {new.kind}")
    if connector.auth == "oauth2":
        raise HTTPException(400, f"{connector.title} needs a sign-in Mindkeep cannot do yet")
    name = new.name.strip()
    if not NAME.match(name):
        raise HTTPException(400, "a name: letters, digits, spaces, dots, dashes; 80 at most")
    tenant, bundle = _where(home)
    given = {f.name: new.config.get(f.name, "").strip() for f in connector.fields}
    _checked(connector, given)
    with session() as s:
        taken = s.scalar(
            select(Connection).where(
                Connection.tenant == tenant,
                Connection.bundle == bundle,
                Connection.name == name,
            )
        )
        if taken:
            raise HTTPException(409, "this bundle already has a connection by that name")
        row = Connection(
            id=secrets.token_hex(16),
            tenant=tenant,
            bundle=bundle,
            kind=new.kind,
            name=name,
            config=vault.seal(given, connector),
            cursor="{}",
            every=_every(new.every),
            enabled=True,
            created_by=user,
            created_at=datetime.now(UTC),
        )
        s.add(row)
        s.commit()
        out = as_dict(row)
        background(syncing.run, home, row.id)
        return out


@router.put("/bundles/{name}/connections/{connection_id}")
def update_connection(
    home: Bundle, connection_id: str, _: Manager, patch: ConnectionPatch
) -> dict[str, object]:
    """Settings and secrets change; the name does not — it is the folder. A secret sent
    back as the marker is kept as it was; a changed config is tried before it is saved."""
    with session() as s:
        row = _get(s, home, connection_id)
        connector = registry().get(row.kind)
        if connector is None:
            raise HTTPException(409, f"no connector of kind {row.kind} is installed")
        if patch.config is not None:
            given = {k: v.strip() for k, v in patch.config.items()}
            merged = vault.merge(given, row.config, connector)
            _checked(connector, merged)
            row.config = vault.seal(merged, connector)
        if patch.every is not None:
            row.every = _every(patch.every)
        if patch.enabled is not None:
            row.enabled = patch.enabled
        s.commit()
        return as_dict(row)


@router.post("/bundles/{name}/connections/{connection_id}/sync", status_code=202)
def sync_now(home: Bundle, connection_id: str, _: Writer) -> dict[str, object]:
    with session() as s:
        row = _get(s, home, connection_id)
        if registry().get(row.kind) is None:
            raise HTTPException(409, f"no connector of kind {row.kind} is installed")
        if syncing.active(row.id):
            raise HTTPException(409, "already syncing")
    background(syncing.run, home, connection_id)
    return {"syncing": connection_id}


@router.delete("/bundles/{name}/connections/{connection_id}")
def remove_connection(home: Bundle, connection_id: str, _: Manager) -> dict[str, object]:
    """The connection and everything it wrote. Its files were mirrors of the source;
    the pages resting on them are retired, as after any deleted source."""
    with session() as s:
        row = _get(s, home, connection_id)
        if syncing.active(row.id):
            raise HTTPException(409, "syncing — remove it when that has finished")
        name = row.name
        s.expunge(row)
    gone = syncing.disconnect(home, row)
    if gone:
        record(home, f"disconnect {name}", *gone)
        for rel in gone:
            if pages_citing(home, rel):
                enqueue(home, rel)
    return {"deleted": connection_id, "removed": len(gone)}
