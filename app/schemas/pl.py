"""
Pydantic schemas for P&L snapshots.
"""

import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class PLMonthData(BaseModel):
    """One monthly P&L data point."""
    month_index: int
    month_date: str                          # ISO date string "YYYY-MM-DD"

    revenue: Optional[float] = None
    subscription_revenue: Optional[float] = None
    usage_credits: Optional[float] = None
    revenue_growth_pct: Optional[float] = None

    cost_of_revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    gross_margin_pct: Optional[float] = None

    cac_spend: Optional[float] = None
    contribution_profit: Optional[float] = None
    contribution_margin_pct: Optional[float] = None

    team_cost: Optional[float] = None
    general_and_admin: Optional[float] = None

    ebitda: Optional[float] = None
    ebit: Optional[float] = None
    net_profit: Optional[float] = None

    cash_balance: Optional[float] = None
    team_members: Optional[float] = None
    engineering_founders: Optional[float] = None


class PLSummary(BaseModel):
    """Aggregate KPIs computed at parse time."""
    total_revenue: Optional[float] = None
    total_ebitda: Optional[float] = None
    total_net_profit: Optional[float] = None
    final_cash_balance: Optional[float] = None
    peak_team_members: Optional[float] = None
    final_arr_cr: Optional[float] = None          # ARR in crores (INR)
    gross_margin_avg_pct: Optional[float] = None
    months_to_ebitda_positive: Optional[int] = None
    months_to_net_positive: Optional[int] = None
    runway_months: Optional[float] = None


class PLSnapshotRead(BaseModel):
    """Full snapshot as returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    upload_date: date
    filename: Optional[str] = None
    scenario: Optional[str] = None
    months: Optional[list[PLMonthData]] = None
    summary: Optional[PLSummary] = None
    created_at: datetime


class PLSnapshotSummaryRead(BaseModel):
    """Lightweight snapshot — summary only, no months array (for history list)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    upload_date: date
    filename: Optional[str] = None
    scenario: Optional[str] = None
    summary: Optional[PLSummary] = None
    created_at: datetime


class PLUploadResponse(BaseModel):
    """Response after a successful P&L upload."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    upload_date: date
    filename: Optional[str] = None
    scenario: Optional[str] = None
    summary: Optional[PLSummary] = None
    month_count: int
    created_at: datetime


class PLSnapshotList(BaseModel):
    snapshots: list[PLSnapshotSummaryRead]
    total: int
