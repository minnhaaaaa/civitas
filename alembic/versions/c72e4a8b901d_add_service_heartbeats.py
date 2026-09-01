"""add operational service heartbeats

Revision ID: c72e4a8b901d
Revises: a61d9c3e7f20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c72e4a8b901d"
down_revision: str | None = "a61d9c3e7f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_heartbeats",
        sa.Column("service_id", sa.String(length=128), nullable=False),
        sa.Column("service_kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.CheckConstraint("service_kind IN ('mcp-server', 'worker')", name="ck_heartbeat_kind"),
        sa.CheckConstraint(
            "state IN ('starting', 'running', 'stopping')", name="ck_heartbeat_state"
        ),
        sa.PrimaryKeyConstraint("service_id"),
    )
    op.create_index(
        "ix_service_heartbeats_kind_seen",
        "service_heartbeats",
        ["service_kind", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_heartbeats_kind_seen", table_name="service_heartbeats")
    op.drop_table("service_heartbeats")
