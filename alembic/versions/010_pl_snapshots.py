"""Add pl_snapshots table

Revision ID: 010_pl_snapshots
Revises: 009_meeting_unique_and_indexes
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '010_pl_snapshots'
down_revision = '009_meeting_unique_and_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'pl_snapshots',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'company_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('companies.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('upload_date', sa.Date(), nullable=False),
        sa.Column('filename', sa.String(512), nullable=True),
        sa.Column('scenario', sa.String(50), nullable=True),
        sa.Column('months', postgresql.JSONB(), nullable=True),
        sa.Column('summary', postgresql.JSONB(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    op.create_index('idx_pl_snapshots_company_id', 'pl_snapshots', ['company_id'])
    op.create_index(
        'idx_pl_snapshots_upload_date',
        'pl_snapshots',
        ['upload_date'],
        postgresql_ops={'upload_date': 'DESC'},
    )


def downgrade() -> None:
    op.drop_index('idx_pl_snapshots_upload_date', table_name='pl_snapshots')
    op.drop_index('idx_pl_snapshots_company_id', table_name='pl_snapshots')
    op.drop_table('pl_snapshots')
