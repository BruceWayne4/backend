import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.company import CompanyType, CompanyStatus


class CompanyBase(BaseModel):
    name: str = Field(
        ...,
        description="Company name",
        examples=["TechStartup Inc", "FinanceAI Ltd"]
    )
    type: Optional[CompanyType] = Field(
        None,
        description="Type of company investment"
    )
    stage: Optional[str] = Field(
        None,
        description="Investment stage (e.g., Seed, Series A, Series B)",
        examples=["Seed", "Series A", "Series B", "Growth"]
    )
    sector: Optional[str] = Field(
        None,
        description="Industry sector",
        examples=["FinTech", "HealthTech", "EdTech", "SaaS"]
    )
    investment_date: Optional[date] = Field(
        None,
        description="Date of investment"
    )
    investment_amount_inr: Optional[Decimal] = Field(
        None,
        description="Investment amount in Indian Rupees",
        examples=[5000000, 10000000]
    )
    status: CompanyStatus = Field(
        CompanyStatus.Active,
        description="Current status of the company"
    )
    sheets_url: Optional[str] = Field(
        None,
        description="Google Sheets URL for Gantt chart data",
        examples=["https://docs.google.com/spreadsheets/d/1abc...xyz/edit"]
    )


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[CompanyType] = None
    stage: Optional[str] = None
    sector: Optional[str] = None
    investment_date: Optional[date] = None
    investment_amount_inr: Optional[Decimal] = None
    status: Optional[CompanyStatus] = None
    sheets_url: Optional[str] = None


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class CompanyList(BaseModel):
    companies: list[CompanyRead]
    total: int
