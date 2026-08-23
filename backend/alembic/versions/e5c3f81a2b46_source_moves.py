"""sources that moved since the last lint

Revision ID: e5c3f81a2b46
Revises: d4e8b21c6a17
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c3f81a2b46"
down_revision: str | Sequence[str] | None = "d4e8b21c6a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "source_move",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant", sa.String(length=128), nullable=False),
        sa.Column("bundle", sa.String(length=64), nullable=False),
        sa.Column("old_path", sa.Text(), nullable=False),
        sa.Column("new_path", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_move_pending", "source_move", ["tenant", "bundle", "settled_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_source_move_pending", table_name="source_move")
    op.drop_table("source_move")
