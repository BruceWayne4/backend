from pydantic import BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import Optional, Any
import uuid


class MeetingBase(BaseModel):
    meeting_date: date = Field(
        ...,
        description="Date when the meeting took place",
        examples=["2024-03-15"]
    )
    raw_notes: str = Field(
        ...,
        description="Raw meeting notes text content",
        examples=["Meeting with CEO to discuss Q1 roadmap..."]
    )
    docx_filename: Optional[str] = Field(
        None,
        description="Original DOCX filename",
        examples=["meeting_2024_03_15.docx"]
    )


class MeetingCreate(MeetingBase):
    pass


class MeetingRead(MeetingBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID = Field(description="Unique meeting identifier")
    company_id: uuid.UUID = Field(description="Company this meeting belongs to")
    ai_summary: Optional[Any] = Field(
        None,
        description="AI-generated summary of the meeting (JSON array of strings)"
    )
    decisions: Optional[Any] = Field(
        None,
        description="Key decisions made during the meeting (JSON array)"
    )
    risks: Optional[Any] = Field(
        None,
        description="Identified risks and concerns (JSON array)"
    )
    gap_assessment: Optional[Any] = Field(
        None,
        description="Assessment of gaps between expectations and reality"
    )
    alignment_points: Optional[Any] = Field(
        None,
        description="Points of alignment between VC and founder (JSON array)"
    )
    gantt_status: Optional[str] = Field(
        None,
        description="Status of Gantt chart alignment",
        examples=["on-track", "delayed", "ahead"]
    )
    gantt_notes: Optional[str] = Field(
        None,
        description="Notes about Gantt chart progress"
    )
    commitments: Optional[Any] = Field(
        None,
        description="Commitments extracted from meeting (deprecated - use commitments endpoint)"
    )
    vc_recommendations: Optional[Any] = Field(
        None,
        description="VC's recommendations and advice (JSON array)"
    )
    initiatives: Optional[Any] = Field(
        None,
        description="New initiatives discussed (JSON array)"
    )
    financials_mentioned: Optional[Any] = Field(
        None,
        description="Financial metrics and discussions (JSON object)"
    )
    sentiment: Optional[int] = Field(
        None,
        description="Overall sentiment score (1-5, where 1=very negative, 5=very positive)",
        ge=1,
        le=5
    )
    sentiment_reason: Optional[str] = Field(
        None,
        description="Explanation of the sentiment score"
    )
    parsed_at: Optional[datetime] = Field(
        None,
        description="Timestamp when AI parsing was completed"
    )
    created_at: datetime = Field(description="Record creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")


class MeetingList(BaseModel):
    meetings: list[MeetingRead] = Field(
        description="List of meetings"
    )


class MeetingUploadResponse(BaseModel):
    success: bool = Field(
        description="Whether the upload was successful",
        examples=[True]
    )
    meeting_id: str = Field(
        description="UUID of the created meeting",
        examples=["550e8400-e29b-41d4-a716-446655440000"]
    )
    meeting_date: str = Field(
        description="Date of the meeting in ISO format",
        examples=["2024-03-15"]
    )
    commitments_count: int = Field(
        description="Number of commitments extracted from the meeting",
        examples=[5]
    )


class MeetingTestUploadResponse(BaseModel):
    success: bool = Field(
        description="Whether the upload was successful",
        examples=[True]
    )
    meetings_processed: int = Field(
        description="Number of meetings processed from the dump file",
        examples=[3]
    )
    results: list[dict] = Field(
        description="Results for each processed meeting"
    )
