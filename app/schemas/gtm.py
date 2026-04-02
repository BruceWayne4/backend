from pydantic import BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import Optional, Any, List
import uuid


# ── Playbook ──────────────────────────────────────────────────────────────────

class GTMPlaybookUpload(BaseModel):
    title: str = Field(..., description="Playbook title", examples=["AJVC B2B GTM Playbook v2"])
    content: str = Field(..., description="Full playbook content in markdown")


class GTMPlaybookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    title: str
    content: str
    is_active: bool
    uploaded_at: datetime
    created_at: datetime


class GTMPlaybookSummary(BaseModel):
    """Lightweight version for history list (no content body)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    title: str
    is_active: bool
    uploaded_at: datetime


# ── Plan ──────────────────────────────────────────────────────────────────────

class GTMPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    playbook_id: Optional[uuid.UUID] = None
    generated_at: Optional[datetime] = None
    meetings_used_count: int
    meetings_date_range_start: Optional[date] = None
    meetings_date_range_end: Optional[date] = None
    generation_status: str  # pending | generating | done | failed
    error_message: Optional[str] = None

    gtm_stage: Optional[str] = None
    target_customer: Optional[Any] = None
    current_gtm_approach: Optional[Any] = None
    recommended_actions: Optional[Any] = None
    open_loops: Optional[Any] = None
    bottlenecks: Optional[Any] = None
    focus_this_week: Optional[str] = None
    sentiment_trend: Optional[str] = None

    # raw_claude_response omitted from API responses (debug only)
    created_at: datetime
    updated_at: Optional[datetime] = None


class GTMPlanSummary(BaseModel):
    """For history list — no JSONB payload."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    playbook_id: Optional[uuid.UUID] = None
    generated_at: Optional[datetime] = None
    meetings_used_count: int
    generation_status: str
    gtm_stage: Optional[str] = None
    sentiment_trend: Optional[str] = None
    created_at: datetime


class GTMGenerateResponse(BaseModel):
    plan_id: uuid.UUID
    status: str = "pending"
    message: str = "GTM plan generation started"


class GTMPlanHistoryList(BaseModel):
    plans: List[GTMPlanSummary]
    total: int
