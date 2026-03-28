from pydantic import BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import Optional
import uuid


class GanttTaskSuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    meeting_id: uuid.UUID
    task: str
    project: Optional[str] = None
    division: Optional[str] = None
    resource: Optional[str] = None
    suggested_start_date: Optional[date] = None
    suggested_end_date: Optional[date] = None
    note: Optional[str] = None
    status: str
    pushed_at: Optional[datetime] = None
    sheet_row_number: Optional[int] = None
    created_at: datetime


class GanttTaskSuggestionList(BaseModel):
    suggestions: list[GanttTaskSuggestionRead]
    total: int


class GanttTaskSuggestionUpdate(BaseModel):
    """Used to update editable fields or status (dismiss) on a single suggestion."""
    task: Optional[str] = None
    project: Optional[str] = None
    division: Optional[str] = None
    resource: Optional[str] = None
    suggested_start_date: Optional[date] = None
    suggested_end_date: Optional[date] = None
    note: Optional[str] = None
    status: Optional[str] = Field(
        None,
        description="Set to 'dismissed' to dismiss, or 'pending' to restore"
    )


class BulkPushSuggestionItem(BaseModel):
    """Edits for a single suggestion in a bulk push request."""
    task: Optional[str] = None
    project: Optional[str] = None
    division: Optional[str] = None
    resource: Optional[str] = None
    suggested_start_date: Optional[date] = None
    suggested_end_date: Optional[date] = None


class BulkPushRequest(BaseModel):
    """Request body for bulk-push endpoint."""
    suggestion_ids: list[uuid.UUID] = Field(
        description="IDs of suggestions to push to Google Sheet"
    )
    updates: Optional[dict[str, BulkPushSuggestionItem]] = Field(
        None,
        description="Map of suggestion_id (str) → field updates to apply before pushing"
    )


class BulkPushResponse(BaseModel):
    pushed: int = Field(description="Number of tasks successfully written to sheet")
    failed: int = Field(description="Number of tasks that failed to write")
    errors: list[dict] = Field(default_factory=list)
