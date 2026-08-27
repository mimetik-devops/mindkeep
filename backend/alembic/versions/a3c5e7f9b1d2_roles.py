"""member becomes contributor; viewer arrives

Revision ID: a3c5e7f9b1d2
Revises: f7a9c2d4e6b1
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c5e7f9b1d2"
down_revision: str | Sequence[str] | None = "f7a9c2d4e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A member could write; that role is now called contributor. Data only."""
    for table in ("membership", "invite"):
        op.execute(sa.text(f"UPDATE {table} SET role = 'contributor' WHERE role = 'member'"))


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("membership", "invite"):
        op.execute(
            sa.text(f"UPDATE {table} SET role = 'member' WHERE role IN ('contributor', 'viewer')")
        )
