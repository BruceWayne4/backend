"""
P&L Snapshot ORM model.

Stores per-company P&L Excel uploads as parsed JSONB snapshots.
Each upload produces one row: months (array of 18 monthly P&L objects)
+ summary (aggregate KPIs computed at parse time).
"""

import uuid
from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class PLSnapshot(Base):
    __tablename__ = "pl_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    upload_date: Mapped[date] = mapped_column(Date, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # "Base" / "Best" / "Worst" — detected from Summary!B2 when possible
    scenario: Mapped[str | None] = mapped_column(String(50), nullable=True, default="Base")
    # Array of monthly dicts — see pl_parser.py for schema
    months: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Aggregate KPIs computed at parse time
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
