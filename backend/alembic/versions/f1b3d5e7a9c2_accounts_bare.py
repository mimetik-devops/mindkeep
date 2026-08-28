"""accounts, to the bone: no admin flag, no last_seen

Revision ID: f1b3d5e7a9c2
Revises: e7a9c1d3f5b7
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1b3d5e7a9c2"
down_revision: str | Sequence[str] | None = "e7a9c1d3f5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("account") as batch:
        batch.drop_column("admin")
        batch.drop_column("last_seen")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("account") as batch:
        batch.add_column(sa.Column("admin", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True))
