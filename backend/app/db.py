import os
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class IngestRun(Base):
    """One attempt at folding a source into the wiki.

    The wiki itself stays on disk — this is telemetry *about* those files: how long a run
    took, how much it wrote, and how it ended. Keeping it in Postgres rather than in memory
    is what lets a duration survive the restart that killed the run it describes.
    """

    __tablename__ = "ingest_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128))
    bundle: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    turns: Mapped[int | None] = mapped_column(Integer, default=None)
    chars: Mapped[int | None] = mapped_column(Integer, default=None)

    model: Mapped[str] = mapped_column(String(64))
    # empty means it worked; a restart mid-run leaves "interrupted" (see sweep_interrupted)
    error: Mapped[str] = mapped_column(Text, default="")
    # the last thing the agent did, overwritten as it works. A lint reads a hundred pages
    # and writes nothing for minutes; without this the UI can only show a spinner.
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_ingest_run_source", "tenant", "bundle", "source"),)


class BundleSetting(Base):
    """Per-bundle knobs. Today that is only when the nightly lint runs.

    A row exists only once someone has chosen; until then the bundle follows the
    server's `LINT_HOUR`. That is why the hour is not defaulted here — "unset" and
    "set to 3" have to stay distinguishable.
    """

    __tablename__ = "bundle_setting"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128))
    bundle: Mapped[str] = mapped_column(String(64))
    # 0-23 in UTC, or LINT_OFF. UTC because the server has no idea where anyone is.
    lint_hour: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("tenant", "bundle", name="uq_bundle_setting"),)


class SourceMove(Base):
    """A source that changed path, waiting for a lint to repoint the pages citing it.

    The alternative is making the agent rediscover it by comparing every citation against
    every file — an O(pages x sources) read to recover something the server knew exactly,
    at the moment it happened, for the cost of one row.
    """

    __tablename__ = "source_move"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128))
    bundle: Mapped[str] = mapped_column(String(64))
    old_path: Mapped[str] = mapped_column(Text)
    new_path: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # set when a lint has finished cleanly, so an interrupted one does not lose the hint
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_source_move_pending", "tenant", "bundle", "settled_at"),)


LINT_OFF = -1


@lru_cache
def engine():
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def session() -> Session:
    return Session(engine())


def now() -> datetime:
    return datetime.now(UTC)
