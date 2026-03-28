"""create gantt_task_suggestions table

Revision ID: 006_gantt_task_suggestions
Revises: 005_gantt_task_mentions
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '006_gantt_task_suggestions'
down_revision = '005_gantt_task_mentions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'gantt_task_suggestions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('meeting_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task', sa.Text(), nullable=False),
        sa.Column('project', sa.Text(), nullable=True),
        sa.Column('division', sa.Text(), nullable=True),
        sa.Column('resource', sa.Text(), nullable=True),
        sa.Column('suggested_start_date', sa.Date(), nullable=True),
        sa.Column('suggested_end_date', sa.Date(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sheet_row_number', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "status IN ('pending', 'pushed', 'dismissed')",
            name='check_gts_status'
        ),
    )
    op.create_index('idx_gts_company', 'gantt_task_suggestions', ['company_id'])
    op.create_index('idx_gts_status',  'gantt_task_suggestions', ['status'])
    op.create_index('idx_gts_meeting', 'gantt_task_suggestions', ['meeting_id'])


def downgrade():
    op.drop_index('idx_gts_meeting', table_name='gantt_task_suggestions')
    op.drop_index('idx_gts_status',  table_name='gantt_task_suggestions')
    op.drop_index('idx_gts_company', table_name='gantt_task_suggestions')
    op.drop_table('gantt_task_suggestions')
