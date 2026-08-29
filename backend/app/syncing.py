"""One sync of one connection: pull, diff, write, commit, ingest.

The plumbing under every connector. A connector returns files; this decides what changed
against what the connection wrote last time (`ConnectorItem`, one row per file, keyed by
the source's own id), writes only that, commits it as the connection's change, and queues
the changed sources for ingest — the same road an upload takes.

**Mirror semantics.** The folder a connection writes — `raw/connectors/<kind>/<name>/` —
is the connection's.
A file in it that someone edits, deletes or moves out of band (the web app, the desktop
client, whose raw/ syncs both ways) is put back at the next sync; the person's version is
in the history, like a wiki page the agent revised. One rule, no tombstones, and the
mirror on every synced machine converges on what the source holds.

ponytail: each changed file is queued as its own ingest, as an upload is. A first sync of
five hundred files is five hundred agent runs in a row. The changed-file list is right
here when a batching window is wanted; it is the cost decision the dev log already names.
"""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, select

from app import grants, history, vault
from app.connectors import ConnectorError, registry
from app.db import Connection, ConnectorItem, Grant, session
from app.files import prune_empty, raw_path
from app.ingest import enqueue, pages_citing

log = logging.getLogger(__name__)

# a connection syncs once at a time; a second ask while one runs is a no-op
_active: set[str] = set()
_guard = threading.Lock()


def active(connection_id: str) -> bool:
    with _guard:
        return connection_id in _active


def due(row: Connection, moment: datetime) -> bool:
    """Enabled, and its interval has passed since the last attempt — failed attempts
    included, so a source that is down is retried at its own pace, not every sweep."""
    if not row.enabled:
        return False
    if row.synced_at is None:
        return True
    last = row.synced_at if row.synced_at.tzinfo else row.synced_at.replace(tzinfo=UTC)
    return moment - last >= timedelta(minutes=row.every)


def folder(row: Connection) -> str:
    """Where this connection's files live, relative to the bundle: under its kind, then
    the name the connector gave it."""
    return f"raw/connectors/{row.kind}/{raw_path(row.name)}"


def run(home: Path, connection_id: str) -> str:
    """Sync now, in this thread. The summary written to the row — "+3 ~1 -0" — or the
    error, which is also written to the row. Never raises: the row is the report."""
    with _guard:
        if connection_id in _active:
            return "already syncing"
        _active.add(connection_id)
    try:
        return _run(home, connection_id)
    finally:
        with _guard:
            _active.discard(connection_id)


def _run(home: Path, connection_id: str) -> str:
    with session() as s:
        row = s.get(Connection, connection_id)
        if row is None:
            return "gone"
        connector = registry().get(row.kind)
        started = datetime.now(UTC)
        try:
            if connector is None:
                raise ConnectorError(f"no connector of kind {row.kind} is installed")
            config = vault.unseal(row.config, connector)
            grant: Grant | None = s.get(Grant, row.grant_id) if row.grant_id else None
            if row.grant_id and grant is None:
                raise ConnectorError("the sign-in this connection used is gone — pick another")
            pull = connector.pull(config, json.loads(row.cursor or "{}"), grants.unpack(grant))
            summary = apply(home, s, row, pull)
            row.cursor = json.dumps(pull.cursor)
            row.error = ""
            row.summary = summary
        except ConnectorError as e:
            row.error = str(e)
            summary = row.error
        except Exception as e:  # a plugin's bug is the connection's error, not the server's
            log.exception("sync of %s (%s) failed", row.name, row.kind)
            row.error = f"{type(e).__name__}: {e}"
            summary = row.error
        row.synced_at = started
        s.commit()
        return summary


def apply(home: Path, s, row: Connection, pull) -> str:  # type: ignore[no-untyped-def]
    """Write what changed, drop what is gone, commit, queue. The summary line."""
    known = {
        item.remote: item
        for item in s.scalars(select(ConnectorItem).where(ConnectorItem.connection_id == row.id))
    }
    base = folder(row)
    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    seen: set[str] = set()

    for item in pull.items:
        rel = f"{base}/{raw_path(item.path)}"
        digest = sha256(item.content).hexdigest()
        target = home / rel
        old = known.get(item.id)
        seen.add(item.id)
        if old and old.path == rel and old.digest == digest and target.is_file():
            if sha256(target.read_bytes()).hexdigest() == digest:
                continue  # in step
        if old and old.path != rel and (home / old.path).is_file():
            (home / old.path).unlink()  # renamed at the source: a move, not a copy
            prune_empty((home / old.path).parent, home / "raw")
            removed.append(old.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
        if old:
            old.path, old.digest, old.synced_at = rel, digest, datetime.now(UTC)
            changed.append(rel)
        else:
            s.add(
                ConnectorItem(
                    tenant=row.tenant,
                    bundle=row.bundle,
                    connection_id=row.id,
                    remote=item.id,
                    path=rel,
                    digest=digest,
                    synced_at=datetime.now(UTC),
                )
            )
            added.append(rel)

    gone = (
        [rid for rid in known if rid not in seen]
        if pull.complete
        else [rid for rid in pull.removed if rid in known]
    )
    for rid in gone:
        old = known[rid]
        if (home / old.path).is_file():
            (home / old.path).unlink()
            prune_empty((home / old.path).parent, home / "raw")
        removed.append(old.path)
        s.delete(old)
    s.flush()

    touched = added + changed + removed
    if touched:
        message = f"sync {row.name}: +{len(added)} ~{len(changed)} -{len(removed)}"
        try:
            history.record(home, message, *touched)
        except Exception:
            log.exception("could not record %s in %s", message, home)
        for rel in added + changed:
            enqueue(home, rel)
        # a source that is gone retires the pages resting on it, as a delete does
        for rel in removed:
            if pages_citing(home, rel):
                enqueue(home, rel)
    return f"+{len(added)} ~{len(changed)} -{len(removed)}"


def disconnect(home: Path, row: Connection) -> list[str]:
    """Remove everything a connection wrote, and its rows. The paths that went, for the
    caller to commit and retire. The connection's folder is the connection's: leaving
    the files would leave sources nothing keeps current."""
    with session() as s:
        items = s.scalars(select(ConnectorItem).where(ConnectorItem.connection_id == row.id)).all()
        gone = []
        for item in items:
            if (home / item.path).is_file():
                (home / item.path).unlink()
                prune_empty((home / item.path).parent, home / "raw")
            gone.append(item.path)
        s.execute(delete(ConnectorItem).where(ConnectorItem.connection_id == row.id))
        s.execute(delete(Connection).where(Connection.id == row.id))
        s.commit()
    return gone
