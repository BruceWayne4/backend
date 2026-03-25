import uuid
from datetime import date, datetime
from sqlalchemy import String, Text, Date, DateTime, Integer, func, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Commitment(Base):
    __tablename__ = "commitments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    
    # Core Fields
    person: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # founder-initiated or aviral-pushed
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="open", server_default="open"
    )  # open, due-soon, overdue, resolved
    
    # Tracking
    days_overdue: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=True
    )
    resolved_in_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
    
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'due-soon', 'overdue', 'resolved')",
            name="check_status"
        ),
    )
    
    @property
    def computed_days_overdue(self) -> int | None:
        """Compute days overdue based on due_date vs today."""
        if self.due_date and self.status != "resolved":
            today = date.today()
            delta = (today - self.due_date).days
            return delta if delta > 0 else None
        return None
