"""connections: a connector configured on a bundle, and the files it wrote

Revision ID: a2c4e6f8b0d1
Revises: f1b3d5e7a9c2
Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2c4e6f8b0d1"
down_revision: str | Sequence[str] | None = "f1b3d5e7a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "connection",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant", sa.String(128), nullable=False),
        sa.Column("bundle", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column("every", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant", "bundle", "name", name="uq_connection_name"),
    )
    op.create_index("ix_connection_bundle", "connection", ["tenant", "bundle"])
    op.create_table(
        "connector_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant", sa.String(128), nullable=False),
        sa.Column("bundle", sa.String(64), nullable=False),
        sa.Column("connection_id", sa.String(32), nullable=False),
        sa.Column("remote", sa.String(1024), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connection_id", "remote", name="uq_connector_item"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("connector_item")
    op.drop_index("ix_connection_bundle", table_name="connection")
    op.drop_table("connection")
