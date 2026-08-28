"""add durable workflow limits and facade metadata

Revision ID: f7b2c4d6e801
Revises: d3f1a6b8c902
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7b2c4d6e801"
down_revision: str | None = "d3f1a6b8c902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_checkpoints",
        sa.Column("workflow_limits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "workflow_checkpoints",
        sa.Column("procurement_goal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "workflow_checkpoints", sa.Column("policy_version", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflow_checkpoints", "policy_version")
    op.drop_column("workflow_checkpoints", "procurement_goal")
    op.drop_column("workflow_checkpoints", "workflow_limits")
