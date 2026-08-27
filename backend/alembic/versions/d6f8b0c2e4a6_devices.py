"""devices: one revocable token per machine

Revision ID: d6f8b0c2e4a6
Revises: c5e7a9b1d3f5
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f8b0c2e4a6"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("sub", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_sub", "device", ["sub"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_device_sub", table_name="device")
    op.drop_table("device")
