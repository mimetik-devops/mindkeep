"""per-bundle lint schedule

Revision ID: d4e8b21c6a17
Revises: c1f2a7d3b904
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8b21c6a17"
down_revision: str | Sequence[str] | None = "c1f2a7d3b904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bundle_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant", sa.String(length=128), nullable=False),
        sa.Column("bundle", sa.String(length=64), nullable=False),
        sa.Column("lint_hour", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant", "bundle", name="uq_bundle_setting"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("bundle_setting")
