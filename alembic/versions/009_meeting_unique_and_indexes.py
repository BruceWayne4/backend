"""Add unique constraint and indexes to meetings table

Revision ID: 009_meeting_unique_and_indexes
Revises: 008_gtm_tables
Create Date: 2026-03-31

Fixes:
- Issue 4: UniqueConstraint(company_id, granola_note_id) to prevent duplicate
  Granola note imports under concurrent syncs.
- Issue 9 (partial): DB indexes on frequently-queried meeting columns so that
  per-company and per-note lookups don't require full table scans.
"""
from alembic import op
import sqlalchemy as sa

revision = '009_meeting_unique_and_indexes'
down_revision = '008_gtm_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique constraint: one row per (company, granola_note_id)
    # Use a partial index so rows where granola_note_id IS NULL are excluded
    # (DOCX-uploaded meetings have NULL granola_note_id and must not conflict).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_meeting_granola_note
        ON meetings (company_id, granola_note_id)
        WHERE granola_note_id IS NOT NULL
        """
    )

    # Plain indexes for high-frequency WHERE/ORDER-BY columns
    op.create_index(
        "ix_meetings_company_id",
        "meetings",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_meetings_granola_note_id",
        "meetings",
        ["granola_note_id"],
        unique=False,
    )
    op.create_index(
        "ix_meetings_granola_updated_at",
        "meetings",
        ["granola_updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_granola_updated_at", table_name="meetings")
    op.drop_index("ix_meetings_granola_note_id", table_name="meetings")
    op.drop_index("ix_meetings_company_id", table_name="meetings")
    op.execute("DROP INDEX IF EXISTS uq_meeting_granola_note")
