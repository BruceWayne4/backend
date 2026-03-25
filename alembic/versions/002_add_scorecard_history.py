"""add scorecard_history to gantt_snapshots

Revision ID: 002
Revises: 001
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gantt_snapshots",
        sa.Column("scorecard_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gantt_snapshots", "scorecard_history")
