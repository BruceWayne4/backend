"""Initial schema — companies and gantt_snapshots

Revision ID: 001
Revises:
Create Date: 2026-03-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ENUM types ───────────────────────────────────────────────────────────
    # Use DO $$ blocks so re-runs are idempotent (asyncpg-safe).
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE company_type_enum AS ENUM ('B2B', 'B2C', 'B2B2C');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE company_status_enum AS ENUM ('Active', 'Watch', 'At-Risk');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # ── companies ────────────────────────────────────────────────────────────
    # Use postgresql.ENUM(create_type=False) so SQLAlchemy's before_create
    # event does NOT fire a second CREATE TYPE statement.
    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM("B2B", "B2C", "B2B2C", name="company_type_enum", create_type=False),
            nullable=True,
        ),
        sa.Column("stage", sa.String(100), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("investment_date", sa.Date, nullable=True),
        sa.Column("investment_amount_inr", sa.Numeric(20, 2), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("Active", "Watch", "At-Risk", name="company_status_enum", create_type=False),
            nullable=False,
            server_default="Active",
        ),
        sa.Column("sheets_url", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── gantt_snapshots ───────────────────────────────────────────────────────
    op.create_table(
        "gantt_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("upload_date", sa.Date, nullable=False),
        sa.Column("tasks", postgresql.JSONB, nullable=True),
        sa.Column("shipping_velocity", sa.Float, nullable=True),
        sa.Column("execution_speed", sa.Float, nullable=True),
        sa.Column("planning_depth", sa.Float, nullable=True),
        sa.Column("planning_quality_score", sa.Float, nullable=True),
        sa.Column("task_count", sa.Integer, nullable=True),
        sa.Column("gantt_diff", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── index on gantt_snapshots(company_id, upload_date DESC) ───────────────
    op.create_index(
        "ix_gantt_snapshots_company_upload",
        "gantt_snapshots",
        ["company_id", sa.text("upload_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_gantt_snapshots_company_upload", table_name="gantt_snapshots")
    op.drop_table("gantt_snapshots")
    op.drop_table("companies")

    op.execute("DROP TYPE IF EXISTS company_type_enum")
    op.execute("DROP TYPE IF EXISTS company_status_enum")
