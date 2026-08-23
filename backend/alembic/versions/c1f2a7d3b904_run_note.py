"""what a run is doing right now

Revision ID: c1f2a7d3b904
Revises: 20b29af8df98
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1f2a7d3b904"
down_revision: str | Sequence[str] | None = "20b29af8df98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ingest_run", sa.Column("note", sa.Text(), nullable=False, server_default="")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ingest_run", "note")
