"""The overnight passes — the lint and the dream — and the connections' syncs.

ponytail: a daemon thread, not a cron container or a broker. The app is one process on
one Railway service, so a thread that wakes every fifteen minutes and asks "has this
bundle had this pass today?" is the whole scheduler. The answer comes from the run
history, so a restart at 02:59 does not cause a double pass, and a server that was down
all night still runs them when it comes back.

Two passes, two clocks. The **lint** is janitorial: broken source links fixed, drift
reported. The **dream** is the wiki read against itself: contradictions, entities with
no page, the questions that would connect thin areas. Each bundle picks an hour for
each (Settings); `LINT_HOUR` and `DREAM_HOUR` are the defaults for bundles whose owner
has never chosen. Hours are UTC — the server has no idea where anyone is, and the UI
converts for the reader. The two share the bundle's one worker, so a pass queued while
the other runs simply waits its turn.
"""

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app import runs
from app.db import LINT_OFF, Connection, session
from app.ingest import DREAM, LINT, enqueue

log = logging.getLogger(__name__)

CHECK_EVERY = 900  # 15 minutes; the hour granularity does not need finer

# each pass: the run-table source it appears as, and the env var with its default hour
PASSES = {"lint": (LINT, "LINT_HOUR", "3"), "dream": (DREAM, "DREAM_HOUR", "4")}


def default_hour(kind: str) -> int:
    """The hour a bundle uses until someone chooses otherwise. Anything odd means off."""
    _, var, fallback = PASSES[kind]
    try:
        hour = int(os.environ.get(var, fallback))
    except ValueError:
        return LINT_OFF
    return hour if 0 <= hour <= 23 else LINT_OFF


def hour_for(home: Path, kind: str) -> int:
    """This bundle's hour for one pass, or LINT_OFF for never."""
    chosen = runs.pass_hour(home, kind)
    return default_hour(kind) if chosen is None else chosen


def bundles() -> list[Path]:
    root = Path(os.environ.get("WIKI_ROOT", "/data"))
    if not root.is_dir():
        return []
    tenants = (t for t in root.iterdir() if t.is_dir() and not t.name.startswith("."))
    return [b for t in tenants for b in t.iterdir() if b.is_dir()]


def due(home: Path, today: str, kind: str) -> bool:
    """Once per calendar day per pass, judged by the last one that actually finished.

    A run that failed or was interrupted did not do the work, so it does not count as
    today's pass — otherwise one killed by a deploy at 02:59 would skip the night.

    ponytail: a pass that keeps failing therefore retries on each tick of its hour, so
    four times a night at worst. That is the right trade while failures are rare; add a
    backoff if they ever stop being.
    """
    source, _, _ = PASSES[kind]
    last = runs.last_pass(home, source)
    if last is None or last.error:
        return True
    # runs.utc, not astimezone: a naive value is already UTC, and astimezone would read it
    # as local time — which shifts the date, and so the answer, for anyone not on UTC
    return runs.utc(last.started_at).strftime("%Y-%m-%d") != today


def next_run(home: Path, kind: str) -> str:
    """When this bundle gets its next automatic pass of one kind, ISO-8601 UTC. Empty
    when off. The UI renders it in the reader's own timezone."""
    hour = hour_for(home, kind)
    if not 0 <= hour <= 23:
        return ""
    moment = datetime.now(UTC)
    when = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    # today's slot is no good if it has passed, or if this pass already ran today
    if when <= moment or not due(home, when.strftime("%Y-%m-%d"), kind):
        when += timedelta(days=1)
    return when.isoformat(timespec="minutes")


def sweep() -> None:
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    for home in bundles():
        if not (home / "CLAUDE.md").is_file():
            continue
        for kind, (source, _, _) in PASSES.items():
            if hour_for(home, kind) != now.hour or not due(home, today, kind):
                continue
            log.info("nightly %s: %s/%s", kind, home.parent.name, home.name)
            # the bundle's worker runs it after whatever is already queued
            enqueue(home, source)
    sync_due(now)


def sync_due(now: datetime) -> None:
    """Every connection whose interval has passed, each in its own thread — a pull is
    network time, and one slow source must not hold the others. From the rows, not the
    disk: a connection whose bundle is gone from the volume is skipped, not synced into
    a path that would recreate it."""
    from app import syncing  # local: files imports this module, and syncing imports files

    root = Path(os.environ.get("WIKI_ROOT", "/data"))
    with session() as s:
        rows = s.scalars(select(Connection).where(Connection.enabled)).all()
        wanted = [(r.id, r.name, root / r.tenant / r.bundle) for r in rows if syncing.due(r, now)]
    for connection_id, name, home in wanted:
        if not (home / "CLAUDE.md").is_file() or syncing.active(connection_id):
            continue
        log.info("sync: %s in %s/%s", name, home.parent.name, home.name)
        threading.Thread(
            target=syncing.run, args=(home, connection_id), daemon=True, name="sync"
        ).start()


def run_forever() -> None:
    while True:
        try:
            sweep()
        except Exception:
            log.exception("scheduler sweep failed")
        time.sleep(CHECK_EVERY)


def start() -> None:
    """Always runs: a bundle can switch its own passes on even when the defaults are off."""
    threading.Thread(target=run_forever, daemon=True, name="overnight").start()
    for kind in PASSES:
        hour = default_hour(kind)
        log.info(
            "nightly %s running, default %s",
            kind,
            f"{hour:02d}:00 UTC" if hour != LINT_OFF else "off",
        )
