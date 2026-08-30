"""each pass picks its pace: every x hours, days or weeks

Revision ID: d6f8a0b2c4d8
Revises: c4d6e8f0a2b4
Create Date: 2026-08-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f8a0b2c4d8"
down_revision: str | Sequence[str] | None = "c4d6e8f0a2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("bundle_setting") as batch:
        batch.add_column(sa.Column("lint_every", sa.String(8), nullable=True))
        batch.add_column(sa.Column("dream_every", sa.String(8), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("bundle_setting") as batch:
        batch.drop_column("dream_every")
        batch.drop_column("lint_every")
