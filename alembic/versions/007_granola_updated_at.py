"""Add granola_updated_at to meetings

Revision ID: 007
Revises: 006
Create Date: 2026-03-27

Stores the Granola note's own updated_at timestamp on each Meeting row.
Used as an incremental sync cursor:
  - Individual company sync: MAX(granola_updated_at) per company → updated_after
  - Bulk sync: a separate last_bulk_sync_at config value → updated_after for list_all_notes()
"""
from alembic import op
import sqlalchemy as sa

revision = '007_granola_updated_at'
down_revision = '006_gantt_task_suggestions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column(
            'granola_updated_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='Granola note updated_at — used as updated_after cursor for incremental syncs',
        ),
    )
    # Index for fast MAX() queries per company
    op.create_index(
        'ix_meetings_company_granola_updated_at',
        'meetings',
        ['company_id', 'granola_updated_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_meetings_company_granola_updated_at', table_name='meetings')
    op.drop_column('meetings', 'granola_updated_at')
