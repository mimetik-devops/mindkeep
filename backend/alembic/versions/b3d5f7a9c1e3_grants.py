"""grants: a person's standing with a provider, and the connection that uses one

Revision ID: b3d5f7a9c1e3
Revises: a2c4e6f8b0d1
Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d5f7a9c1e3"
down_revision: str | Sequence[str] | None = "a2c4e6f8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "grant",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("sub", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_grant_sub", "grant", ["sub"])
    with op.batch_alter_table("connection") as batch:
        batch.add_column(sa.Column("grant_id", sa.String(32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("connection") as batch:
        batch.drop_column("grant_id")
    op.drop_index("ix_grant_sub", table_name="grant")
    op.drop_table("grant")
