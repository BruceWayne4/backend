"""Add pl_sheets_url to companies table

Revision ID: 011_company_pl_sheets_url
Revises: 010_pl_snapshots
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = '011_company_pl_sheets_url'
down_revision = '010_pl_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'companies',
        sa.Column('pl_sheets_url', sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('companies', 'pl_sheets_url')
