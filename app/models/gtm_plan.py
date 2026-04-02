import uuid
from datetime import date, datetime
from sqlalchemy import String, Text, Integer, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class GTMPlan(Base):
    __tablename__ = "gtm_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    playbook_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gtm_playbooks.id", ondelete="SET NULL"),
        nullable=True,
    )
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meetings_used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meetings_date_range_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    meetings_date_range_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    # generation_status: pending | generating | done | failed
    generation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured output fields
    gtm_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_customer: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_gtm_approach: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommended_actions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    open_loops: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    bottlenecks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    focus_this_week: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment_trend: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Raw Claude output (debug)
    raw_claude_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
