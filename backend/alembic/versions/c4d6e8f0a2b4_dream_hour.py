"""the nightly pass splits in two: lint keeps the hour it had, dreaming gets its own

Revision ID: c4d6e8f0a2b4
Revises: b3d5f7a9c1e3
Create Date: 2026-08-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d6e8f0a2b4"
down_revision: str | Sequence[str] | None = "b3d5f7a9c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("bundle_setting") as batch:
        # unset means "follow the server default", so both hours have to be nullable —
        # lint_hour predates that and was not
        batch.alter_column("lint_hour", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("dream_hour", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("bundle_setting") as batch:
        batch.drop_column("dream_hour")
        batch.alter_column("lint_hour", existing_type=sa.Integer(), nullable=False)
