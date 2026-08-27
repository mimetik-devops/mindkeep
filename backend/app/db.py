import os
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import (
    Boolean,
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
    # the commit the bundle was at when the agent started reading — the state it saw, so
    # a later re-ingest of the same source can be handed what changed since
    based_on: Mapped[str] = mapped_column(String(40), default="")
    # the commit holding what this run wrote (history.py); "" when it wrote nothing
    commit: Mapped[str] = mapped_column(String(40), default="")
    # set when the run was reverted — it no longer counts as having ingested its source
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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


class Team(Base):
    """People who share bundles. See teams.py for why this is Mindkeep's, not the provider's.

    A personal team's id is the hash that names its owner's directory, so the tenant
    directory and the team are the same thing under two names.
    """

    __tablename__ = "team"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    personal: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    """One person in one team, with the app's own role: owner, admin or member.

    `name` and `email` are a snapshot of what the person's token said, refreshed each
    time they list their teams — there is no user table to look them up in, and a
    members list that showed only subjects would be useless.
    """

    __tablename__ = "membership"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(String(32))
    sub: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("team_id", "sub", name="uq_membership"),
        Index("ix_membership_sub", "sub"),
    )


class Invite(Base):
    """A link into a team: one use, a week, a role. Spent when `accepted_by` is set."""

    __tablename__ = "invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    team_id: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[str | None] = mapped_column(String(128), default=None)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_invite_team", "team_id"),)


class Device(Base):
    """A machine signed in with a long-lived token — the desktop client, the tray app.

    The token is `id.digest`; this row is what makes it revocable. See devices.py.
    """

    __tablename__ = "device"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sub: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_device_sub", "sub"),)


LINT_OFF = -1


@lru_cache
def engine():
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def session() -> Session:
    return Session(engine())


def now() -> datetime:
    return datetime.now(UTC)
