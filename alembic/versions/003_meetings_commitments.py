"""add meetings and commitments tables

Revision ID: 003_meetings_commitments
Revises: 002
Create Date: 2026-03-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003_meetings_commitments'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    # Create meetings table
    op.create_table(
        'meetings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('meeting_date', sa.Date(), nullable=False),
        sa.Column('raw_notes', sa.Text(), nullable=False),
        sa.Column('ai_summary', postgresql.JSONB(), nullable=True),
        sa.Column('decisions', postgresql.JSONB(), nullable=True),
        sa.Column('risks', postgresql.JSONB(), nullable=True),
        sa.Column('gap_assessment', postgresql.JSONB(), nullable=True),
        sa.Column('alignment_points', postgresql.JSONB(), nullable=True),
        sa.Column('gantt_status', sa.String(50), nullable=True),
        sa.Column('gantt_notes', sa.Text(), nullable=True),
        sa.Column('commitments', postgresql.JSONB(), nullable=True),
        sa.Column('vc_recommendations', postgresql.JSONB(), nullable=True),
        sa.Column('initiatives', postgresql.JSONB(), nullable=True),
        sa.Column('financials_mentioned', postgresql.JSONB(), nullable=True),
        sa.Column('sentiment', sa.Integer(), nullable=True),
        sa.Column('sentiment_reason', sa.Text(), nullable=True),
        sa.Column('docx_filename', sa.String(255), nullable=True),
        sa.Column('uploaded_by', sa.String(100), nullable=True),
        sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('company_id', 'meeting_date', name='unique_company_meeting')
    )
    
    # Create index on company_id and meeting_date
    op.create_index('idx_meetings_company_date', 'meetings', ['company_id', 'meeting_date'])
    
    # Create commitments table
    op.create_table(
        'commitments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('meeting_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('person', sa.String(100), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='open'),
        sa.Column('days_overdue', sa.Integer(), nullable=True),
        sa.Column('origin_meeting_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_in_meeting_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['origin_meeting_id'], ['meetings.id']),
        sa.ForeignKeyConstraint(['resolved_in_meeting_id'], ['meetings.id']),
        sa.CheckConstraint("status IN ('open', 'due-soon', 'overdue', 'resolved')", name='check_status')
    )
    
    # Create indexes on commitments
    op.create_index('idx_commitments_company', 'commitments', ['company_id'])
    op.create_index('idx_commitments_status', 'commitments', ['status'])
    op.create_index('idx_commitments_due_date', 'commitments', ['due_date'])


def downgrade():
    op.drop_index('idx_commitments_due_date', table_name='commitments')
    op.drop_index('idx_commitments_status', table_name='commitments')
    op.drop_index('idx_commitments_company', table_name='commitments')
    op.drop_table('commitments')
    
    op.drop_index('idx_meetings_company_date', table_name='meetings')
    op.drop_table('meetings')
