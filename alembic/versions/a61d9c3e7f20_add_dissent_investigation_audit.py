"""add durable clean-room dissent phase audit

Revision ID: a61d9c3e7f20
Revises: 2c6d0a76f945
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a61d9c3e7f20"
down_revision: str | None = "2c6d0a76f945"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_evidence_identity", "evidence", type_="unique")
    op.create_unique_constraint(
        "uq_evidence_identity",
        "evidence",
        ["planning_run_id", "source_id", "raw_response_sha256", "observation_version"],
    )
    op.create_table(
        "dissent_investigations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("planning_run_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_key", sa.String(length=128), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "phase IN ('plan_recorded', 'fresh_retrieval_complete', "
            "'comparison_complete', 'failed')",
            name="ck_dissent_investigations_phase",
        ),
        sa.ForeignKeyConstraint(["planning_run_id"], ["planning_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planning_run_id", "cycle_key", "phase", name="uq_dissent_phase_audit"),
    )
    op.create_index(
        "ix_dissent_investigations_planning_run_id",
        "dissent_investigations",
        ["planning_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dissent_investigations_planning_run_id",
        table_name="dissent_investigations",
    )
    op.drop_table("dissent_investigations")
    op.drop_constraint("uq_evidence_identity", "evidence", type_="unique")
    op.create_unique_constraint(
        "uq_evidence_identity",
        "evidence",
        ["source_id", "raw_response_sha256", "observation_version"],
    )
