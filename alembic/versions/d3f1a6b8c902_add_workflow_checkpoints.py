"""add durable workflow checkpoints and leases

Revision ID: d3f1a6b8c902
Revises: 8e4c1d9a2b70
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3f1a6b8c902"
down_revision: str | None = "8e4c1d9a2b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoints",
        sa.Column("planning_run_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_workflow_checkpoints_attempt_count"),
        sa.CheckConstraint("cycle >= 1", name="ck_workflow_checkpoints_cycle"),
        sa.CheckConstraint("event_sequence >= 0", name="ck_workflow_checkpoints_event_sequence"),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="ck_workflow_checkpoints_lease_complete",
        ),
        sa.ForeignKeyConstraint(["planning_run_id"], ["planning_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("planning_run_id"),
        sa.UniqueConstraint("lease_token", name="uq_workflow_checkpoints_lease_token"),
    )
    op.create_index(
        "ix_workflow_checkpoints_queue",
        "workflow_checkpoints",
        ["completed", "available_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_checkpoints_queue", table_name="workflow_checkpoints")
    op.drop_table("workflow_checkpoints")
