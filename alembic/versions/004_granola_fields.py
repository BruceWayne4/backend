"""Add Granola integration fields to meetings

Revision ID: 004_granola_fields
Revises: 003_meetings_commitments
Create Date: 2026-03-27 15:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004_granola_fields'
down_revision = '003_meetings_commitments'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add Granola integration fields to meetings table."""
    # Add new columns
    op.add_column('meetings', sa.Column('granola_note_id', sa.String(255), nullable=True))
    op.add_column('meetings', sa.Column('sync_source', sa.String(50), nullable=True))
    
    # Add index for faster lookups
    op.create_index('idx_meetings_granola_note_id', 'meetings', ['granola_note_id'])
    op.create_index('idx_meetings_sync_source', 'meetings', ['sync_source'])
    
    # Add unique constraint to prevent duplicate imports
    op.create_unique_constraint(
        'unique_granola_note_per_company',
        'meetings',
        ['company_id', 'granola_note_id']
    )


def downgrade() -> None:
    """Remove Granola integration fields from meetings table."""
    # Drop constraint
    op.drop_constraint('unique_granola_note_per_company', 'meetings', type_='unique')
    
    # Drop indexes
    op.drop_index('idx_meetings_sync_source', 'meetings')
    op.drop_index('idx_meetings_granola_note_id', 'meetings')
    
    # Drop columns
    op.drop_column('meetings', 'sync_source')
    op.drop_column('meetings', 'granola_note_id')
