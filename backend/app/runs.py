"""Ingest run history: the operational record of what the agent did to each source.

The wiki stays on disk; this is metadata *about* those files. Keeping it in Postgres
rather than in memory is what lets a duration outlive the restart that killed the run,
and what lets an interrupted run be recognised as interrupted rather than as "never tried".
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, update

from app.db import BundleSetting, Connection, ConnectorItem, IngestRun, SourceMove, now, session

# every table keyed by (tenant, bundle): re-keyed together when a bundle moves, is renamed
# or deleted, and when a legacy tenant is re-keyed
KEYED = (IngestRun, BundleSetting, SourceMove, Connection, ConnectorItem)

log = logging.getLogger(__name__)

INTERRUPTED = "Interrupted — the server restarted while this was running."


def utc(moment: datetime) -> datetime:
    """SQLite drops the timezone on the way out; the value was always UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _where(home: Path) -> tuple[str, str]:
    """A bundle directory is `<root>/<tenant>/<bundle>`."""
    return home.parent.name, home.name


def rekey_legacy_tenants(tenant_id: Callable[[str], str]) -> int:
    """Rows keyed by a subject rather than its hash — from before tenants were hashed —
    re-keyed at startup. Judged by the value alone, so it does not matter whether the
    directory was renamed, moved, or recreated since; once, and then never again."""
    with session() as s:
        keys = {
            t
            for table in (IngestRun, BundleSetting, SourceMove)
            for t in s.scalars(select(table.tenant).distinct()).all()
        }
    moved = 0
    for old in keys:
        if re.fullmatch(r"[0-9a-f]{32}", old):
            continue
        rename_tenant(old, tenant_id(old))
        moved += 1
        log.info("re-keyed rows of tenant %s to %s", old, tenant_id(old))
    return moved


def rename_tenant(old: str, new: str) -> None:
    """Every row a tenant owns, re-keyed. Once, when its directory moves."""
    with session() as s:
        for table in KEYED:
            s.execute(update(table).where(table.tenant == old).values(tenant=new))
        s.commit()


def move_bundle(old: str, new: str, bundle: str) -> None:
    """Every row of one bundle, re-keyed to another team. Once, when the bundle moves."""
    with session() as s:
        for table in KEYED:
            s.execute(
                update(table).where(table.tenant == old, table.bundle == bundle).values(tenant=new)
            )
        s.commit()


def rename_bundle(tenant: str, old: str, new: str) -> None:
    """Every row of one bundle, under its new name. Once, when the bundle is renamed."""
    with session() as s:
        for table in KEYED:
            s.execute(
                update(table).where(table.tenant == tenant, table.bundle == old).values(bundle=new)
            )
        s.commit()


def forget_bundle(tenant: str, bundle: str) -> None:
    """Every row of one bundle, gone. Once, when the bundle is deleted."""
    with session() as s:
        for table in KEYED:
            s.execute(delete(table).where(table.tenant == tenant, table.bundle == bundle))
        s.commit()


def forget_tenant(tenant: str) -> None:
    """Every row a tenant owns, gone. Once, when the team is deleted."""
    with session() as s:
        for table in KEYED:
            s.execute(delete(table).where(table.tenant == tenant))
        s.commit()


def start(home: Path, source: str, model: str) -> int:
    tenant, bundle = _where(home)
    with session() as s:
        run = IngestRun(tenant=tenant, bundle=bundle, source=source, started_at=now(), model=model)
        s.add(run)
        s.commit()
        return run.id


def progress(home: Path, run_id: int, note: str = "", turns: int | None = None) -> None:
    """Record what the run is doing, while it is still doing it.

    One small UPDATE per tool call. That is a few writes a minute against a row nobody
    else is reading — cheap enough not to need a cache, and it survives the restart that
    an in-memory progress dict would not.
    """
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is None:
            return
        if note:
            run.note = note[:300]
        if turns is not None:
            run.turns = turns
        s.commit()


def finish(
    home: Path, run_id: int, turns: int, chars: int, error: str = "", commit: str = ""
) -> None:
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is None:
            return
        run.finished_at = now()
        run.seconds = int((run.finished_at - utc(run.started_at)).total_seconds())
        run.turns, run.chars, run.error, run.commit = turns, chars, error, commit
        s.commit()


def get(home: Path, run_id: int) -> IngestRun | None:
    """One run — of this bundle, or None. A run id from another bundle is not a fact."""
    tenant, bundle = _where(home)
    with session() as s:
        run = s.get(IngestRun, run_id)
        return run if run and (run.tenant, run.bundle) == (tenant, bundle) else None


def set_base(run_id: int, sha: str) -> None:
    """The commit the agent is about to read from."""
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is not None:
            run.based_on = sha
            s.commit()


def recent(home: Path, limit: int = 500) -> list[IngestRun]:
    """This bundle's runs, newest first."""
    tenant, bundle = _where(home)
    with session() as s:
        return list(
            s.scalars(
                select(IngestRun)
                .where(IngestRun.tenant == tenant, IngestRun.bundle == bundle)
                .order_by(IngestRun.started_at.desc())
                .limit(limit)
            ).all()
        )


def last_read(home: Path, source: str) -> IngestRun | None:
    """The latest run that read this source and finished cleanly — and was not undone,
    since an undone run's reading no longer stands in the wiki."""
    tenant, bundle = _where(home)
    with session() as s:
        return s.scalar(
            select(IngestRun)
            .where(
                IngestRun.tenant == tenant,
                IngestRun.bundle == bundle,
                IngestRun.source == source,
                IngestRun.finished_at.is_not(None),
                IngestRun.error == "",
                IngestRun.undone_at.is_(None),
            )
            .order_by(IngestRun.started_at.desc())
        )


def read_before(home: Path, run: IngestRun) -> IngestRun | None:
    """The clean, un-undone run of the same source before this one — the state the wiki
    reflected before this run, and what an undo of it goes back to."""
    tenant, bundle = _where(home)
    with session() as s:
        return s.scalar(
            select(IngestRun)
            .where(
                IngestRun.tenant == tenant,
                IngestRun.bundle == bundle,
                IngestRun.source == run.source,
                IngestRun.id != run.id,
                IngestRun.started_at < run.started_at,
                IngestRun.finished_at.is_not(None),
                IngestRun.error == "",
                IngestRun.undone_at.is_(None),
            )
            .order_by(IngestRun.started_at.desc())
        )


def mark_redone(run_id: int) -> None:
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is not None:
            run.undone_at = None
            s.commit()


def mark_undone(run_id: int) -> None:
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is not None:
            run.undone_at = now()
            s.commit()


def latest(home: Path) -> dict[str, IngestRun]:
    """The most recent run per source in this bundle."""
    tenant, bundle = _where(home)
    with session() as s:
        rows = s.scalars(
            select(IngestRun)
            .where(IngestRun.tenant == tenant, IngestRun.bundle == bundle)
            .order_by(IngestRun.started_at)
        ).all()
    return {r.source: r for r in rows}  # later rows overwrite earlier ones


def running_sources(home: Path) -> set[str]:
    """Sources with a run still open — used to refuse a second lint on top of one."""
    tenant, bundle = _where(home)
    with session() as s:
        return set(
            s.scalars(
                select(IngestRun.source).where(
                    IngestRun.tenant == tenant,
                    IngestRun.bundle == bundle,
                    IngestRun.finished_at.is_(None),
                )
            ).all()
        )


def last_pass(home: Path, source: str) -> IngestRun | None:
    """The most recent run of one overnight pass — `(lint)` or `(dream)` — or None.
    Drives each pass's own once-a-day check."""
    tenant, bundle = _where(home)
    with session() as s:
        return s.scalars(
            select(IngestRun)
            .where(
                IngestRun.tenant == tenant,
                IngestRun.bundle == bundle,
                IngestRun.source == source,
            )
            .order_by(IngestRun.started_at.desc())
            .limit(1)
        ).first()


def sweep_interrupted() -> list[tuple[str, str, str]]:
    """Close out runs that were in flight when the process died, and say which they were.

    Called at startup. Without it a killed run stays open forever and every later read
    reports it as still running — the restart-amnesia problem in reverse.

    Returns (tenant, bundle, source) for each, so the caller can put them back in the
    queue: whatever stopped the process is not the source's fault, and a source uploaded
    but never ingested has nothing in the UI to retry it.
    """
    with session() as s:
        stranded = s.scalars(select(IngestRun).where(IngestRun.finished_at.is_(None))).all()
        found = [(r.tenant, r.bundle, r.source) for r in stranded]
        if found:
            s.execute(
                update(IngestRun)
                .where(IngestRun.finished_at.is_(None))
                .values(finished_at=now(), error=INTERRUPTED)
            )
            s.commit()
        return found


# which BundleSetting column holds each pass's hour
_HOURS = {"lint": BundleSetting.lint_hour, "dream": BundleSetting.dream_hour}


def pass_hour(home: Path, kind: str) -> int | None:
    """The hour this bundle has chosen for one pass, or None to follow the server."""
    tenant, bundle = _where(home)
    with session() as s:
        return s.scalars(
            select(_HOURS[kind]).where(
                BundleSetting.tenant == tenant, BundleSetting.bundle == bundle
            )
        ).first()


def set_pass_hour(home: Path, kind: str, hour: int) -> None:
    """Choose when one pass runs on this bundle. `LINT_OFF` switches it off."""
    tenant, bundle = _where(home)
    with session() as s:
        row = s.scalars(
            select(BundleSetting).where(
                BundleSetting.tenant == tenant, BundleSetting.bundle == bundle
            )
        ).first()
        if row is None:
            row = BundleSetting(tenant=tenant, bundle=bundle)
            s.add(row)
        setattr(row, _HOURS[kind].key, hour)
        s.commit()


def record_move(home: Path, old_path: str, new_path: str) -> None:
    """Remember that a source changed path, so the next lint is told rather than left to guess.

    Chains are collapsed: moving a.md to b.md and then to c.md leaves one row, a.md to
    c.md, because that is the only fact a page citing a.md needs. A file moved back where
    it started leaves no row at all — nothing to repoint.
    """
    tenant, bundle = _where(home)
    with session() as s:
        chain = s.scalars(
            select(SourceMove).where(
                SourceMove.tenant == tenant,
                SourceMove.bundle == bundle,
                SourceMove.settled_at.is_(None),
                SourceMove.new_path == old_path,
            )
        ).first()
        if chain is None:
            s.add(
                SourceMove(
                    tenant=tenant,
                    bundle=bundle,
                    old_path=old_path,
                    new_path=new_path,
                    at=now(),
                )
            )
        elif chain.old_path == new_path:
            s.delete(chain)
        else:
            chain.new_path, chain.at = new_path, now()
        s.commit()


def forget_moves(home: Path, path: str) -> None:
    """Drop pending moves that ended at `path` — it has just been deleted, so they are moot."""
    tenant, bundle = _where(home)
    with session() as s:
        for row in s.scalars(
            select(SourceMove).where(
                SourceMove.tenant == tenant,
                SourceMove.bundle == bundle,
                SourceMove.settled_at.is_(None),
                SourceMove.new_path == path,
            )
        ).all():
            s.delete(row)
        s.commit()


def pending_moves(home: Path) -> list[tuple[int, str, str]]:
    """(id, old, new) for every move no lint has dealt with yet."""
    tenant, bundle = _where(home)
    with session() as s:
        rows = s.scalars(
            select(SourceMove)
            .where(
                SourceMove.tenant == tenant,
                SourceMove.bundle == bundle,
                SourceMove.settled_at.is_(None),
            )
            .order_by(SourceMove.at)
        ).all()
        return [(r.id, r.old_path, r.new_path) for r in rows]


def settle_moves(ids: list[int]) -> None:
    """Called only after a lint finishes cleanly — an interrupted one must not lose these."""
    if not ids:
        return
    with session() as s:
        s.execute(update(SourceMove).where(SourceMove.id.in_(ids)).values(settled_at=now()))
        s.commit()


def attempted_sources(home: Path) -> set[str]:
    """Sources with any run at all — finished, failed, undone, still open. The complement
    is what a restart loses: uploaded, queued in memory, never started."""
    tenant, bundle = _where(home)
    with session() as s:
        return set(
            s.scalars(
                select(IngestRun.source)
                .where(IngestRun.tenant == tenant, IngestRun.bundle == bundle)
                .distinct()
            )
        )


def failed_sources(home: Path) -> list[tuple[str, str]]:
    """(source, error) for every source whose latest run ended in an error — what a
    retry button is for. Maintenance runs are not sources."""
    from app.ingest import MAINTENANCE

    tenant, bundle = _where(home)
    latest: dict[str, IngestRun] = {}
    with session() as s:
        for r in s.scalars(
            select(IngestRun)
            .where(IngestRun.tenant == tenant, IngestRun.bundle == bundle)
            .order_by(IngestRun.started_at.desc(), IngestRun.id.desc())
        ):
            if r.source not in MAINTENANCE and r.source not in latest:
                latest[r.source] = r
    return [(src, r.error) for src, r in latest.items() if r.finished_at and r.error]


def ingested_sources(home: Path) -> set[str]:
    """Sources that have at least one run that finished without an error.

    This is what "ingested" means. It used to be derived from whether any page cited the
    file, which is a proxy that goes wrong in both directions: a source the agent read
    and correctly decided not to write about looked unread, and a moved source looked
    like it had never been touched at all.
    """
    tenant, bundle = _where(home)
    with session() as s:
        return set(
            s.scalars(
                select(IngestRun.source).where(
                    IngestRun.tenant == tenant,
                    IngestRun.bundle == bundle,
                    IngestRun.finished_at.is_not(None),
                    IngestRun.error == "",
                    IngestRun.undone_at.is_(None),  # taken back is not ingested
                )
            ).all()
        )


def rename_source(home: Path, old_path: str, new_path: str) -> None:
    """Point a source's run history at its new path, so a move does not orphan it."""
    tenant, bundle = _where(home)
    with session() as s:
        s.execute(
            update(IngestRun)
            .where(
                IngestRun.tenant == tenant,
                IngestRun.bundle == bundle,
                IngestRun.source == old_path,
            )
            .values(source=new_path)
        )
        s.commit()
