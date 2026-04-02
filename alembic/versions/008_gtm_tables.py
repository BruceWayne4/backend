"""Create gtm_playbooks and gtm_plans tables

Revision ID: 008
Revises: 007
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '008_gtm_tables'
down_revision = '007_granola_updated_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── gtm_playbooks ─────────────────────────────────────────────────────────
    op.create_table(
        'gtm_playbooks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column(
            'uploaded_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )

    # ── gtm_plans ─────────────────────────────────────────────────────────────
    op.create_table(
        'gtm_plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('playbook_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('meetings_used_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('meetings_date_range_start', sa.Date(), nullable=True),
        sa.Column('meetings_date_range_end', sa.Date(), nullable=True),
        sa.Column('generation_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('gtm_stage', sa.String(50), nullable=True),
        sa.Column('target_customer', postgresql.JSONB(), nullable=True),
        sa.Column('current_gtm_approach', postgresql.JSONB(), nullable=True),
        sa.Column('recommended_actions', postgresql.JSONB(), nullable=True),
        sa.Column('open_loops', postgresql.JSONB(), nullable=True),
        sa.Column('bottlenecks', postgresql.JSONB(), nullable=True),
        sa.Column('focus_this_week', sa.Text(), nullable=True),
        sa.Column('sentiment_trend', sa.String(50), nullable=True),
        sa.Column('raw_claude_response', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['playbook_id'], ['gtm_playbooks.id'], ondelete='SET NULL'
        ),
    )

    op.create_index('ix_gtm_plans_company_id', 'gtm_plans', ['company_id'])
    op.create_index('ix_gtm_plans_company_created', 'gtm_plans', ['company_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_gtm_plans_company_created', table_name='gtm_plans')
    op.drop_index('ix_gtm_plans_company_id', table_name='gtm_plans')
    op.drop_table('gtm_plans')
    op.drop_table('gtm_playbooks')
