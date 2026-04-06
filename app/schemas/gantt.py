import uuid
from datetime import date, datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field


class TaskObject(BaseModel):
    division: Optional[str] = Field(None, description="Division/team owning the task", examples=["Product", "Engineering", "Marketing"])
    project: Optional[str] = Field(None, description="Project name", examples=["User Dashboard", "Mobile App"])
    task: Optional[str] = Field(None, description="Task description", examples=["Implement authentication", "Design mockups"])
    start_date: Optional[str] = Field(None, description="Task start date", examples=["2024-03-01"])
    end_date: Optional[str] = Field(None, description="Task end date", examples=["2024-03-15"])
    duration_days: Optional[int] = Field(None, description="Duration in days", examples=[14])
    resource_1: Optional[str] = Field(None, description="Primary resource assigned", examples=["John Doe"])
    resource_2: Optional[str] = Field(None, description="Secondary resource assigned")
    resource_3: Optional[str] = Field(None, description="Tertiary resource assigned")
    stage: Optional[str] = Field(None, description="Current task stage", examples=["In Progress", "Done", "Delayed"])
    completion_date: Optional[str] = Field(None, description="Actual completion date", examples=["2024-03-14"])


class GanttDiff(BaseModel):
    stage_changes: list[dict[str, Any]] = Field(
        default=[],
        description="Tasks that changed stage since last snapshot"
    )
    new_tasks: list[dict[str, Any]] = Field(
        default=[],
        description="Tasks added since last snapshot"
    )
    removed_tasks: list[dict[str, Any]] = Field(
        default=[],
        description="Tasks removed since last snapshot"
    )


class GanttPullRequest(BaseModel):
    sheets_url: Optional[str] = Field(
        None,
        description="Google Sheets URL to pull from (overrides company's default)",
        examples=["https://docs.google.com/spreadsheets/d/1abc...xyz/edit"]
    )


class GanttPullResponse(BaseModel):
    snapshot_id: uuid.UUID = Field(description="ID of the created snapshot")
    upload_date: date = Field(description="Date of the snapshot")
    task_count: int = Field(description="Total number of tasks in the snapshot")
    shipping_velocity: Optional[float] = Field(None, description="Shipping velocity KPI (can be negative when many tasks are Delayed)", examples=[0.755])
    execution_speed: Optional[float] = Field(None, description="Execution speed KPI (can be negative due to Delayed task penalties)", examples=[0.823])
    planning_depth: Optional[float] = Field(None, description="Planning depth KPI (0-100)", examples=[68.9])
    planning_quality_score: Optional[float] = Field(None, description="Overall planning quality score (0-100)", examples=[71.2])
    gantt_diff: Optional[GanttDiff] = Field(None, description="Diff from previous snapshot")


class GanttSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    upload_date: date
    tasks: Optional[list[Any]] = None
    shipping_velocity: Optional[float] = None
    execution_speed: Optional[float] = None
    planning_depth: Optional[float] = None
    planning_quality_score: Optional[float] = None
    task_count: Optional[int] = None
    gantt_diff: Optional[Any] = None
    scorecard_history: Optional[list[Any]] = None
    created_at: datetime


class GanttSnapshotList(BaseModel):
    snapshots: list[GanttSnapshotRead]
    total: int
    page: int
    page_size: int


class TasksResponse(BaseModel):
    tasks: list[TaskObject]
    total: int
    filters_applied: dict[str, Optional[str]]


class VelocityPoint(BaseModel):
    upload_date: date = Field(description="Date of the velocity measurement")
    shipping_velocity: Optional[float] = Field(None, description="Shipping velocity value (can be negative when many tasks are Delayed)")


class VelocityHistoryResponse(BaseModel):
    company_id: uuid.UUID = Field(description="Company ID")
    history: list[VelocityPoint] = Field(description="Historical velocity data points")


class PortfolioRow(BaseModel):
    company_id: uuid.UUID = Field(description="Company identifier")
    company_name: str = Field(description="Company name")
    company_status: Optional[str] = Field(None, description="Company status", examples=["Active", "Exited"])
    yet_to_start: int = Field(default=0, description="Count of tasks not yet started")
    delayed: int = Field(default=0, description="Count of delayed tasks")
    in_progress: int = Field(default=0, description="Count of tasks in progress")
    done: int = Field(default=0, description="Count of completed tasks")
    done_but_delayed: int = Field(default=0, description="Count of tasks completed but delayed")
    total_tasks: int = Field(default=0, description="Total task count")
    execution_speed: Optional[float] = Field(None, description="Execution speed KPI (0-100)")
    planning_depth: Optional[float] = Field(None, description="Planning depth KPI (0.0–1.0)")
    shipping_velocity: Optional[float] = Field(None, description="Shipping velocity KPI (can be negative when many tasks are Delayed)")
    has_snapshot: bool = Field(default=False, description="Whether company has any Gantt snapshots")


class PortfolioOverviewResponse(BaseModel):
    rows: list[PortfolioRow] = Field(description="Portfolio company data")
    total_companies: int = Field(description="Total number of companies")
