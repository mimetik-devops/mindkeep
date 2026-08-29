"""Connections: a connector, configured, on a bundle.

A *connector* is code — the `url` built-in, a Notion plugin (`app/connectors/`). A
*connection* is one of them set up on one bundle with its own settings, secrets and
schedule: "the team wiki in Notion", "the pricing sheet at this URL". Its files land under
`raw/connectors/<folder>/` and are kept in step by `syncing.py`.

Managing connections is the `bundles` permission — they hold credentials and decide what
flows into the wiki; owners and admins. Asking for a sync now is `write`.
"""

import logging
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import grants, syncing, vault
from app.auth import CurrentUser
from app.connectors import ConnectorError, registry
from app.db import Connection, Grant, session
from app.files import Bundle, Manager, Writer, record
from app.ingest import enqueue, pages_citing

log = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}")

EVERY_MIN, EVERY_MAX, EVERY_DEFAULT = 15, 7 * 24 * 60, 60  # minutes


class NewConnection(BaseModel):
    kind: str
    config: dict[str, str]
    every: int = EVERY_DEFAULT
    grant: str | None = None  # one of the caller's, for a connector that needs one


class ConnectionPatch(BaseModel):
    config: dict[str, str] | None = None
    every: int | None = None
    enabled: bool | None = None
    grant: str | None = None


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
            "available": c.auth != "oauth2" or grants.configured(c),
            "folder": f"raw/connectors/{c.folder or c.kind}",
            "tick": c.tick,
            "fields": [_field(f) for f in c.fields],
            "grant_fields": [_field(f) for f in c.grant_fields],
        }
        for c in sorted(registry().values(), key=lambda c: c.title)
    ]


def _field(f: Any) -> dict[str, object]:
    return {
        "name": f.name,
        "label": f.label,
        "secret": f.secret,
        "help": f.help,
        "required": f.required,
        "multiline": f.multiline,
        "options": [list(o) for o in f.options],
        "rows": [_field(r) for r in f.rows],
        "browse": f.browse,
    }


def as_dict(row: Connection, grant: Grant | None = None) -> dict[str, object]:
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
        # the sign-in it syncs with: its id and label, or none for a kind that needs none;
        # `gone` when the person revoked it
        "grant": {"id": row.grant_id, "label": grant.label} if grant else None,
        "grant_gone": bool(row.grant_id) and grant is None,
    }


def _grant_for(s: Any, connector: Any, user: str, grant_id: str | None) -> Grant | None:
    """The grant a connection may use: the caller's own, of the connector's kind, and
    only when the connector wants one."""
    if connector.auth == "none":
        if grant_id:
            raise HTTPException(400, f"{connector.title} needs no sign-in")
        return None
    if not grant_id:
        raise HTTPException(400, f"{connector.title} needs a sign-in — add one in your account")
    row: Grant | None = s.get(Grant, grant_id)
    if row is None or row.sub != user or row.kind != connector.kind:
        raise HTTPException(404, "no such sign-in")
    return row


def _checked_with(s: Any, connector: Any, config: dict[str, str], grant: Grant | None) -> None:
    """The scope, tried with the grant as a sync would use it — renewed if need be."""
    for f in connector.fields:
        if f.required and not config.get(f.name, "").strip():
            raise HTTPException(400, f"{f.label} is required")
    try:
        connector.check(config, grants.fresh(s, grant))
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e


def _where(home: Path) -> tuple[str, str]:
    return home.parent.name, home.name


def _every(every: int) -> int:
    if not EVERY_MIN <= every <= EVERY_MAX:
        raise HTTPException(400, f"sync every {EVERY_MIN} minutes to {EVERY_MAX // 1440} days")
    return every


def _get(s: Any, home: Path, connection_id: str) -> Connection:
    tenant, bundle = _where(home)
    row: Connection | None = s.get(Connection, connection_id)
    if row is None or (row.tenant, row.bundle) != (tenant, bundle):
        raise HTTPException(404, "no such connection")
    return row


@router.get("/connectors")
def list_connectors(_: CurrentUser) -> list[dict[str, object]]:
    return catalog()


class BrowseAsk(BaseModel):
    field: str
    at: str = ""
    grant: str | None = None


@router.post("/bundles/{name}/connectors/{kind}/browse")
def browse(
    home: Bundle, kind: str, user: CurrentUser, _: Manager, ask: BrowseAsk
) -> dict[str, object]:
    """What a browsable field offers one level down from `at` — the connector asks the
    provider with the caller's own sign-in, renewed if need be. A POST, because the
    sign-in and the place are a body, not an address."""
    connector = registry().get(kind)
    if connector is None:
        raise HTTPException(404, f"no connector of kind {kind}")
    with session() as s:
        grant = _grant_for(s, connector, user, ask.grant)
        try:
            choices = connector.browse(ask.field, ask.at, grants.fresh(s, grant))
        except ConnectorError as e:
            raise HTTPException(400, str(e)) from e
    return {
        "at": ask.at,
        "choices": [{"value": c.value, "label": c.label, "opens": c.opens} for c in choices],
    }


@router.get("/bundles/{name}/connections")
def list_connections(home: Bundle) -> list[dict[str, object]]:
    tenant, bundle = _where(home)
    with session() as s:
        rows = s.scalars(
            select(Connection)
            .where(Connection.tenant == tenant, Connection.bundle == bundle)
            .order_by(Connection.created_at)
        ).all()
        return [as_dict(r, s.get(Grant, r.grant_id) if r.grant_id else None) for r in rows]


@router.post("/bundles/{name}/connections", status_code=201)
def add_connection(
    home: Bundle, user: CurrentUser, _: Manager, new: NewConnection
) -> dict[str, object]:
    """Set a connection up: the scope is tried first (the connector's `check`), the
    connector names it, then the row is made and its first sync started. Nobody types a
    name — the connector knows what the thing is called — and a bundle has one connection
    of a kind: its form holds the plural, each item with its own settings."""
    connector = registry().get(new.kind)
    if connector is None:
        raise HTTPException(404, f"no connector of kind {new.kind}")
    tenant, bundle = _where(home)
    given = {f.name: new.config.get(f.name, "").strip() for f in connector.fields}
    with session() as s:
        grant = _grant_for(s, connector, user, new.grant)
        _checked_with(s, connector, given, grant)
        try:
            name = connector.name(given)[:80]
        except ConnectorError as e:
            raise HTTPException(400, str(e)) from e
        taken = s.scalar(
            select(Connection).where(
                Connection.tenant == tenant,
                Connection.bundle == bundle,
                Connection.kind == new.kind,
            )
        )
        if taken:
            raise HTTPException(409, f"{connector.title} is already connected — edit it")
        row = Connection(
            id=secrets.token_hex(16),
            tenant=tenant,
            bundle=bundle,
            kind=new.kind,
            name=name,
            config=vault.seal(given, connector),
            grant_id=grant.id if grant else None,
            cursor="{}",
            every=connector.tick or _every(new.every),
            enabled=True,
            created_by=user,
            created_at=datetime.now(UTC),
        )
        s.add(row)
        s.commit()
        out = as_dict(row, grant)
        background(syncing.run, home, row.id)
        return out


@router.put("/bundles/{name}/connections/{connection_id}")
def update_connection(
    home: Bundle, connection_id: str, user: CurrentUser, _: Manager, patch: ConnectionPatch
) -> dict[str, object]:
    """Settings, secrets and the sign-in change, and the name follows the settings. A
    secret sent back as the marker is kept as it was; a changed config is tried before it
    is saved. A new sign-in has to be the caller's own."""
    with session() as s:
        row = _get(s, home, connection_id)
        connector = registry().get(row.kind)
        if connector is None:
            raise HTTPException(409, f"no connector of kind {row.kind} is installed")
        if patch.grant is not None:
            row.grant_id = _grant_for(s, connector, user, patch.grant).id  # type: ignore[union-attr]
        grant: Grant | None = s.get(Grant, row.grant_id) if row.grant_id else None
        if patch.config is not None:
            given = {k: v.strip() for k, v in patch.config.items()}
            merged = vault.merge(given, row.config, connector)
            _checked_with(s, connector, merged, grant)
            row.config = vault.seal(merged, connector)
            row.name = connector.name(merged)[:80]
        if patch.every is not None and not connector.tick:
            row.every = _every(patch.every)
        if patch.enabled is not None:
            row.enabled = patch.enabled
        s.commit()
        return as_dict(row, grant)


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
