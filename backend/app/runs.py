"""Ingest run history: the operational record of what the agent did to each source.

The wiki stays on disk; this is metadata *about* those files. Keeping it in Postgres
rather than in memory is what lets a duration outlive the restart that killed the run,
and what lets an interrupted run be recognised as interrupted rather than as "never tried".
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, update

from app.db import BundleSetting, IngestRun, SourceMove, now, session

log = logging.getLogger(__name__)

INTERRUPTED = "Interrupted — the server restarted while this was running."


def utc(moment: datetime) -> datetime:
    """SQLite drops the timezone on the way out; the value was always UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _where(home: Path) -> tuple[str, str]:
    """A bundle directory is `<root>/<tenant>/<bundle>`."""
    return home.parent.name, home.name


def rename_tenant(old: str, new: str) -> None:
    """Every row a tenant owns, re-keyed. Once, when its directory moves."""
    with session() as s:
        for table in (IngestRun, BundleSetting, SourceMove):
            s.execute(update(table).where(table.tenant == old).values(tenant=new))
        s.commit()


def move_bundle(old: str, new: str, bundle: str) -> None:
    """Every row of one bundle, re-keyed to another team. Once, when the bundle moves."""
    with session() as s:
        for table in (IngestRun, BundleSetting, SourceMove):
            s.execute(
                update(table).where(table.tenant == old, table.bundle == bundle).values(tenant=new)
            )
        s.commit()


def rename_bundle(tenant: str, old: str, new: str) -> None:
    """Every row of one bundle, under its new name. Once, when the bundle is renamed."""
    with session() as s:
        for table in (IngestRun, BundleSetting, SourceMove):
            s.execute(
                update(table).where(table.tenant == tenant, table.bundle == old).values(bundle=new)
            )
        s.commit()


def forget_tenant(tenant: str) -> None:
    """Every row a tenant owns, gone. Once, when the team is deleted."""
    with session() as s:
        for table in (IngestRun, BundleSetting, SourceMove):
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


def finish(home: Path, run_id: int, turns: int, chars: int, error: str = "") -> None:
    with session() as s:
        run = s.get(IngestRun, run_id)
        if run is None:
            return
        run.finished_at = now()
        run.seconds = int((run.finished_at - utc(run.started_at)).total_seconds())
        run.turns, run.chars, run.error = turns, chars, error
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


def last_lint(home: Path):
    """The most recent lint of this bundle, or None. Drives the once-a-day check."""
    from app.ingest import LINT

    tenant, bundle = _where(home)
    with session() as s:
        return s.scalars(
            select(IngestRun)
            .where(
                IngestRun.tenant == tenant,
                IngestRun.bundle == bundle,
                IngestRun.source == LINT,
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


def lint_hour(home: Path) -> int | None:
    """The hour this bundle has chosen, or None to follow the server's LINT_HOUR."""
    tenant, bundle = _where(home)
    with session() as s:
        return s.scalars(
            select(BundleSetting.lint_hour).where(
                BundleSetting.tenant == tenant, BundleSetting.bundle == bundle
            )
        ).first()


def set_lint_hour(home: Path, hour: int) -> None:
    """Choose when this bundle is linted. `LINT_OFF` switches the nightly pass off."""
    tenant, bundle = _where(home)
    with session() as s:
        row = s.scalars(
            select(BundleSetting).where(
                BundleSetting.tenant == tenant, BundleSetting.bundle == bundle
            )
        ).first()
        if row is None:
            s.add(BundleSetting(tenant=tenant, bundle=bundle, lint_hour=hour))
        else:
            row.lint_hour = hour
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
