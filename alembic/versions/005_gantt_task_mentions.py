"""add gantt_task_mentions to meetings

Revision ID: 005_gantt_task_mentions
Revises: 004_granola_fields
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_gantt_task_mentions'
down_revision = '004_granola_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'meetings',
        sa.Column('gantt_task_mentions', postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column('meetings', 'gantt_task_mentions')
