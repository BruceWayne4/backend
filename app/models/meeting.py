import uuid
from datetime import date, datetime
from sqlalchemy import String, Text, Date, DateTime, Integer, func, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    meeting_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_notes: Mapped[str] = mapped_column(Text, nullable=False)
    
    # AI Extracted Fields (JSONB)
    ai_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decisions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gap_assessment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    alignment_points: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gantt_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gantt_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    gantt_task_mentions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    commitments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    vc_recommendations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    initiatives: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    financials_mentioned: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sentiment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Metadata
    granola_note_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granola_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Granola note updated_at — used as updated_after cursor for incremental syncs"
    )
    sync_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    docx_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("company_id", "granola_note_id", name="uq_meeting_granola_note"),
        Index("ix_meetings_company_id", "company_id"),
        Index("ix_meetings_granola_note_id", "granola_note_id"),
        Index("ix_meetings_granola_updated_at", "granola_updated_at"),
    )
