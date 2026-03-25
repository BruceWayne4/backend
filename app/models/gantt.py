import uuid
from datetime import date, datetime
from sqlalchemy import Integer, Float, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class GanttSnapshot(Base):
    __tablename__ = "gantt_snapshots"

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
    tasks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    shipping_velocity: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    planning_depth: Mapped[float | None] = mapped_column(Float, nullable=True)
    planning_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    task_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gantt_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 8-week velocity history seeded from Gantt_Scorecard on first pull
    # Format: [{"date": "YYYY-MM-DD", "velocity": float}, ...]
    scorecard_history: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
