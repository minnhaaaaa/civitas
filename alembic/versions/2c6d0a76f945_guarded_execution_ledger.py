"""bind approvals and add immutable execution/provider ledgers

Revision ID: 2c6d0a76f945
Revises: 8e4c1d9a2b70
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2c6d0a76f945"
down_revision: str | None = "8e4c1d9a2b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_audits", sa.Column("approval_receipt_id", sa.String(length=64), nullable=True)
    )
    op.create_foreign_key(
        "fk_execution_audits_approval_receipt",
        "execution_audits",
        "approval_receipts",
        ["approval_receipt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "execution_audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.CheckConstraint("sequence > 0", name="ck_execution_events_sequence_positive"),
        sa.ForeignKeyConstraint(["execution_id"], ["execution_audits.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_execution_events_sequence"),
    )
    op.create_index(
        "ix_execution_audit_events_execution_id", "execution_audit_events", ["execution_id"]
    )
    op.create_table(
        "provider_writes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'succeeded', 'failed', 'compensation_required', 'compensated')",
            name="ck_provider_writes_state",
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["execution_audits.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_provider_writes_idempotency"
        ),
    )
    op.create_index("ix_provider_writes_execution_id", "provider_writes", ["execution_id"])


def downgrade() -> None:
    op.drop_index("ix_provider_writes_execution_id", table_name="provider_writes")
    op.drop_table("provider_writes")
    op.drop_index("ix_execution_audit_events_execution_id", table_name="execution_audit_events")
    op.drop_table("execution_audit_events")
    op.drop_constraint(
        "fk_execution_audits_approval_receipt", "execution_audits", type_="foreignkey"
    )
    op.drop_column("execution_audits", "approval_receipt_id")
