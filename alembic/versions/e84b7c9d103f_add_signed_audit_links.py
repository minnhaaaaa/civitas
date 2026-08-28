"""add signed organization-scoped audit links

Revision ID: e84b7c9d103f
Revises: c72e4a8b901d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e84b7c9d103f"
down_revision: str | None = "c72e4a8b901d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_links",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("reference_hash", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("planning_run_id", sa.String(length=64), nullable=False),
        sa.Column("selected_plan_id", sa.String(length=64), nullable=False),
        sa.Column("maximum_event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name="ck_audit_links_expiry"),
        sa.CheckConstraint("maximum_event_sequence >= 0", name="ck_audit_links_cursor"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id"], ["planning_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["selected_plan_id"], ["candidate_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_hash"),
    )
    op.create_index(
        "ix_audit_links_org_run", "audit_links", ["organization_id", "planning_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_links_org_run", table_name="audit_links")
    op.drop_table("audit_links")
