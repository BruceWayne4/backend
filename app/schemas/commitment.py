from pydantic import BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import Optional
import uuid


class CommitmentBase(BaseModel):
    person: str = Field(
        ...,
        description="Person responsible for the commitment",
        examples=["John Doe (CEO)", "Jane Smith (CTO)", "Founder"]
    )
    action: str = Field(
        ...,
        description="Description of the action to be taken",
        examples=["Complete user onboarding flow", "Hire 2 engineers", "Launch MVP"]
    )
    due_date: Optional[date] = Field(
        None,
        description="Due date for the commitment",
        examples=["2024-04-01"]
    )
    source: str = Field(
        ...,
        description="Source of the commitment",
        examples=["founder-initiated", "aviral-pushed"]
    )


class CommitmentCreate(CommitmentBase):
    company_id: uuid.UUID
    meeting_id: uuid.UUID
    origin_meeting_id: Optional[uuid.UUID] = None


class CommitmentUpdate(BaseModel):
    status: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolved_in_meeting_id: Optional[uuid.UUID] = None


class CommitmentRead(CommitmentBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID = Field(description="Unique commitment identifier")
    company_id: uuid.UUID = Field(description="Company this commitment belongs to")
    meeting_id: uuid.UUID = Field(description="Meeting where this commitment was mentioned")
    status: str = Field(
        description="Current status of the commitment",
        examples=["open", "due-soon", "overdue", "resolved"]
    )
    days_overdue: Optional[int] = Field(
        None,
        description="Number of days overdue (only for overdue commitments)",
        examples=[3, 7, 14]
    )
    origin_meeting_id: Optional[uuid.UUID] = Field(
        None,
        description="Meeting where this commitment was originally created"
    )
    resolved_in_meeting_id: Optional[uuid.UUID] = Field(
        None,
        description="Meeting where this commitment was resolved"
    )
    resolved_at: Optional[datetime] = Field(
        None,
        description="Timestamp when commitment was marked as resolved"
    )
    created_at: datetime = Field(description="Record creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")


class CommitmentList(BaseModel):
    commitments: list[CommitmentRead]
