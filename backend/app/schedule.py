"""Nightly lint.

ponytail: a daemon thread, not a cron container or a broker. The app is one process on
one Railway service, so a thread that wakes every fifteen minutes and asks "has this
bundle been linted today?" is the whole scheduler. The answer comes from the run history,
so a restart at 02:59 does not cause a double lint, and a server that was down all night
still lints when it comes back.

Each bundle picks its own hour (Settings); `LINT_HOUR` is only the default for bundles
whose owner has never chosen. Hours are UTC — the server has no idea where anyone is,
and the UI converts for the reader.
"""

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import runs
from app.db import LINT_OFF
from app.ingest import LINT, enqueue

log = logging.getLogger(__name__)

CHECK_EVERY = 900  # 15 minutes; the hour granularity does not need finer


def default_hour() -> int:
    """The hour a bundle uses until someone chooses otherwise. Anything odd means off."""
    try:
        hour = int(os.environ.get("LINT_HOUR", "3"))
    except ValueError:
        return LINT_OFF
    return hour if 0 <= hour <= 23 else LINT_OFF


def hour_for(home: Path) -> int:
    """This bundle's lint hour, or LINT_OFF for never."""
    chosen = runs.lint_hour(home)
    return default_hour() if chosen is None else chosen


def bundles() -> list[Path]:
    root = Path(os.environ.get("WIKI_ROOT", "/data"))
    if not root.is_dir():
        return []
    tenants = (t for t in root.iterdir() if t.is_dir() and not t.name.startswith("."))
    return [b for t in tenants for b in t.iterdir() if b.is_dir()]


def due(home: Path, today: str) -> bool:
    """Once per calendar day, judged by the last lint that actually finished.

    A run that failed or was interrupted did not lint anything, so it does not count as
    today's pass — otherwise a lint killed by a deploy at 02:59 would skip the night.

    ponytail: a lint that keeps failing therefore retries on each tick of its hour, so
    four times a night at worst. That is the right trade while failures are rare; add a
    backoff if they ever stop being.
    """
    last = runs.last_lint(home)
    if last is None or last.error:
        return True
    # runs.utc, not astimezone: a naive value is already UTC, and astimezone would read it
    # as local time — which shifts the date, and so the answer, for anyone not on UTC
    return runs.utc(last.started_at).strftime("%Y-%m-%d") != today


def next_run(home: Path) -> str:
    """When this bundle gets its next automatic lint, ISO-8601 UTC. Empty when off.

    The UI renders it in the reader's own timezone.
    """
    hour = hour_for(home)
    if not 0 <= hour <= 23:
        return ""
    moment = datetime.now(UTC)
    when = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    # today's slot is no good if it has passed, or if this bundle was already linted today
    if when <= moment or not due(home, when.strftime("%Y-%m-%d")):
        when += timedelta(days=1)
    return when.isoformat(timespec="minutes")


def sweep() -> None:
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    for home in bundles():
        if not (home / "CLAUDE.md").is_file():
            continue
        if hour_for(home) != now.hour or not due(home, today):
            continue
        log.info("nightly lint: %s/%s", home.parent.name, home.name)
        enqueue(home, LINT)  # the bundle's worker runs it after whatever is already queued


def run_forever() -> None:
    while True:
        try:
            sweep()
        except Exception:
            log.exception("nightly lint sweep failed")
        time.sleep(CHECK_EVERY)


def start() -> None:
    """Always runs: a bundle can switch its own lint on even when the default is off."""
    threading.Thread(target=run_forever, daemon=True, name="nightly-lint").start()
    hour = default_hour()
    log.info(
        "nightly lint running, default %s",
        f"{hour:02d}:00 UTC" if hour != LINT_OFF else "off",
    )
