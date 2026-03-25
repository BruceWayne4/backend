import uuid
from datetime import date, datetime
from sqlalchemy import String, Numeric, Date, DateTime, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
import enum


class CompanyType(str, enum.Enum):
    B2B = "B2B"
    B2C = "B2C"
    B2B2C = "B2B2C"


class CompanyStatus(str, enum.Enum):
    Active = "Active"
    Watch = "Watch"
    AtRisk = "At-Risk"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[CompanyType] = mapped_column(
        SAEnum(CompanyType, name="company_type_enum"), nullable=True
    )
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    investment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    investment_amount_inr: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    status: Mapped[CompanyStatus] = mapped_column(
        SAEnum(CompanyStatus, name="company_status_enum"),
        nullable=False,
        default=CompanyStatus.Active,
        server_default="Active",
    )
    sheets_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
