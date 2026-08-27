"""add persisted approval challenge and receipt ledger

Revision ID: 8e4c1d9a2b70
Revises: 5a9ee65c1497
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8e4c1d9a2b70"
down_revision: str | None = "5a9ee65c1497"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_challenges",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column("planning_run_id", sa.String(length=64), nullable=False),
        sa.Column("selected_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("approved_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=128), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name="ck_approval_challenges_expiry"),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'invalidated', 'expired')",
            name="ck_approval_challenges_state",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["planning_run_id"], ["planning_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_challenges_org_run",
        "approval_challenges",
        ["organization_id", "planning_run_id"],
    )
    op.create_table(
        "approval_receipts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("challenge_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column("planning_run_id", sa.String(length=64), nullable=False),
        sa.Column("selected_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("approved_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_idempotency_key", sa.String(length=255), nullable=True),
        sa.CheckConstraint("expires_at > approved_at", name="ck_approval_receipts_expiry"),
        sa.ForeignKeyConstraint(["challenge_id"], ["approval_challenges.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["planning_run_id"], ["planning_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", name="uq_approval_receipts_challenge"),
    )
    op.create_index(
        "ix_approval_receipts_org_run", "approval_receipts", ["organization_id", "planning_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_approval_receipts_org_run", table_name="approval_receipts")
    op.drop_table("approval_receipts")
    op.drop_index("ix_approval_challenges_org_run", table_name="approval_challenges")
    op.drop_table("approval_challenges")
