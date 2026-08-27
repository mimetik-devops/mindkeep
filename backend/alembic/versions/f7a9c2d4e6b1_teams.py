"""teams, memberships and invites

Revision ID: f7a9c2d4e6b1
Revises: e5c3f81a2b46
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a9c2d4e6b1"
down_revision: str | Sequence[str] | None = "e5c3f81a2b46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "team",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("personal", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("sub", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "sub", name="uq_membership"),
    )
    op.create_index("ix_membership_sub", "membership", ["sub"], unique=False)
    op.create_table(
        "invite",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by", sa.String(length=128), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_invite_token"),
    )
    op.create_index("ix_invite_team", "invite", ["team_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_invite_team", table_name="invite")
    op.drop_table("invite")
    op.drop_index("ix_membership_sub", table_name="membership")
    op.drop_table("membership")
    op.drop_table("team")
