"""
Gantt endpoints — /api/v1/gantt
"""

import uuid
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.company import Company
from app.models.gantt import GanttSnapshot
from app.models.meeting import Meeting
from app.models.gantt_suggestion import GanttTaskSuggestion
from app.schemas.gantt_suggestion import (
    GanttTaskSuggestionRead,
    GanttTaskSuggestionList,
    GanttTaskSuggestionUpdate,
    BulkPushRequest,
    BulkPushResponse,
)
from app.schemas.gantt import (
    GanttPullRequest,
    GanttPullResponse,
    GanttDiff,
    GanttSnapshotRead,
    GanttSnapshotList,
    TasksResponse,
    TaskObject,
    VelocityHistoryResponse,
    VelocityPoint,
    PortfolioRow,
    PortfolioOverviewResponse,
)
from app.auth.jwt import get_current_user, TokenData
from app.services import sheets_service, gantt_service

router = APIRouter(prefix="/gantt", tags=["gantt"])


async def _get_company_or_404(company_id: uuid.UUID, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


async def _get_latest_snapshot(
    company_id: uuid.UUID, db: AsyncSession
) -> Optional[GanttSnapshot]:
    result = await db.execute(
        select(GanttSnapshot)
        .where(GanttSnapshot.company_id == company_id)
        .order_by(GanttSnapshot.upload_date.desc(), GanttSnapshot.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/{company_id}/pull", response_model=GanttPullResponse)
async def pull_gantt(
    company_id: uuid.UUID,
    body: GanttPullRequest = GanttPullRequest(),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    company = await _get_company_or_404(company_id, db)

    # Resolve sheets URL
    sheets_url = body.sheets_url or company.sheets_url
    if not sheets_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No sheets_url provided and company has no sheets_url configured",
        )

    # Fetch raw data from Google Sheets
    raw_data = sheets_service.fetch_sheet_data(sheets_url)

    # Parse into structured form
    parsed = gantt_service.parse_sheet_data(raw_data)

    # Get previous snapshot for diff
    prev_snapshot = await _get_latest_snapshot(company_id, db)

    # Compute diff
    diff_result = gantt_service.diff_snapshots(prev_snapshot, parsed["tasks"])

    # Build snapshot record
    snapshot = GanttSnapshot(
        company_id=company_id,
        upload_date=date.today(),
        tasks=parsed["tasks"],
        shipping_velocity=parsed["shipping_velocity"],
        execution_speed=parsed["execution_speed"],
        planning_depth=parsed["planning_depth"],
        planning_quality_score=parsed["planning_quality_score"],
        task_count=parsed["task_count"],
        gantt_diff=diff_result,
        # Always store the latest scorecard_history so the sparkline seeding
        # works even if earlier snapshots were created before this column existed.
        scorecard_history=parsed.get("scorecard_history") or None,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    # Build response
    gantt_diff_schema = None
    if diff_result is not None:
        gantt_diff_schema = GanttDiff(
            stage_changes=diff_result.get("stage_changes", []),
            new_tasks=diff_result.get("new_tasks", []),
            removed_tasks=diff_result.get("removed_tasks", []),
        )

    return GanttPullResponse(
        snapshot_id=snapshot.id,
        upload_date=snapshot.upload_date,
        task_count=snapshot.task_count or 0,
        shipping_velocity=snapshot.shipping_velocity,
        execution_speed=snapshot.execution_speed,
        planning_depth=snapshot.planning_depth,
        planning_quality_score=snapshot.planning_quality_score,
        gantt_diff=gantt_diff_schema,
    )


@router.get("/{company_id}/latest", response_model=GanttSnapshotRead)
async def get_latest_snapshot(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)
    snapshot = await _get_latest_snapshot(company_id, db)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for this company")
    return snapshot


@router.get("/{company_id}/snapshots", response_model=GanttSnapshotList)
async def list_snapshots(
    company_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    offset = (page - 1) * page_size

    count_result = await db.execute(
        select(func.count())
        .select_from(GanttSnapshot)
        .where(GanttSnapshot.company_id == company_id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(GanttSnapshot)
        .where(GanttSnapshot.company_id == company_id)
        .order_by(GanttSnapshot.upload_date.desc(), GanttSnapshot.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    snapshots = result.scalars().all()

    return GanttSnapshotList(
        snapshots=list(snapshots),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{company_id}/tasks", response_model=TasksResponse)
async def get_tasks(
    company_id: uuid.UUID,
    division: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)
    snapshot = await _get_latest_snapshot(company_id, db)

    if not snapshot or not snapshot.tasks:
        return TasksResponse(
            tasks=[],
            total=0,
            filters_applied={"division": division, "stage": stage},
        )

    tasks: list[dict] = snapshot.tasks

    # Apply filters
    if division:
        tasks = [t for t in tasks if (t.get("division") or "").lower() == division.lower()]
    if stage:
        tasks = [t for t in tasks if (t.get("stage") or "").lower() == stage.lower()]

    # Sort tasks
    tasks = gantt_service.sort_tasks(tasks)

    task_objects = [TaskObject(**t) for t in tasks]

    return TasksResponse(
        tasks=task_objects,
        total=len(task_objects),
        filters_applied={"division": division, "stage": stage},
    )


# All date formats that may appear in scorecard_history entries
_SEED_DATE_FORMATS = (
    "%Y-%m-%d",    # ISO: stored by sheets_service._parse_sheets_date for serials
    "%m/%d/%Y",    # US: "3/14/2026"
    "%d/%m/%Y",    # day-first locale: "14/3/2026"
    "%m/%d/%y",    # 2-digit year US: "3/14/26"
    "%d/%m/%y",    # 2-digit year day-first: "14/3/26"
    "%b %d, %Y",   # "Mar 14, 2026"
    "%B %d, %Y",   # "March 14, 2026"
    "%b %d %Y",    # "Mar 14 2026"
)


def _parse_seed_date(raw: str) -> "date | None":
    from datetime import datetime as _dt
    raw = raw.strip()
    for fmt in _SEED_DATE_FORMATS:
        try:
            return _dt.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


@router.get("/{company_id}/velocity-history", response_model=VelocityHistoryResponse)
async def get_velocity_history(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    # ── 1. Fetch ALL snapshots (needed for scorecard seed search + full history) ─
    result = await db.execute(
        select(GanttSnapshot)
        .where(GanttSnapshot.company_id == company_id)
        .order_by(GanttSnapshot.upload_date.desc(), GanttSnapshot.created_at.desc())
    )
    all_snapshots = result.scalars().all()

    # ── 2. Deduplicate: keep LATEST snapshot per calendar day (up to 8 for chart) ─
    seen_dates_db: set[date] = set()
    deduped: list[GanttSnapshot] = []
    for s in all_snapshots:  # already newest-first
        if s.upload_date not in seen_dates_db:
            seen_dates_db.add(s.upload_date)
            deduped.append(s)
        if len(deduped) >= 8:
            break

    # Return in chronological order (oldest → newest)
    db_points = [
        VelocityPoint(upload_date=s.upload_date, shipping_velocity=s.shipping_velocity)
        for s in reversed(deduped)
    ]

    # ── 3. Seed historical weekly data from scorecard_history ─────────────────
    # Search ALL snapshots (not just the 8 deduped ones) so we find scorecard_history
    # even if it was stored on a snapshot older than the current 8-day window.
    seeded_points: list[VelocityPoint] = []
    seed_snapshot: GanttSnapshot | None = next(
        (s for s in reversed(all_snapshots) if s.scorecard_history),
        None,
    )
    if seed_snapshot is not None:
        raw_history: list[dict] = seed_snapshot.scorecard_history or []
        for entry in raw_history:
            raw_date = entry.get("date")
            vel = entry.get("velocity")
            if not raw_date or vel is None:
                continue
            parsed_date = _parse_seed_date(str(raw_date))
            if parsed_date is None:
                continue
            seeded_points.append(
                VelocityPoint(upload_date=parsed_date, shipping_velocity=float(vel))
            )

    # ── 4. Merge: DB points override seeded points on the same date ───────────
    seen_dates_seeded: set[date] = {p.upload_date for p in db_points}
    unique_seeded = [p for p in seeded_points if p.upload_date not in seen_dates_seeded]
    history = sorted(unique_seeded + db_points, key=lambda p: p.upload_date)

    return VelocityHistoryResponse(company_id=company_id, history=history)


# ── Task Suggestion endpoints ─────────────────────────────────────────────────

@router.get("/{company_id}/task-suggestions", response_model=GanttTaskSuggestionList)
async def list_task_suggestions(
    company_id: uuid.UUID,
    status_filter: Optional[str] = Query(default="pending", alias="status"),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    List Gantt task suggestions for a company.
    Defaults to pending suggestions only. Pass ?status=all to see all.
    """
    await _get_company_or_404(company_id, db)

    query = select(GanttTaskSuggestion).where(
        GanttTaskSuggestion.company_id == company_id
    )
    if status_filter and status_filter != "all":
        query = query.where(GanttTaskSuggestion.status == status_filter)

    query = query.order_by(GanttTaskSuggestion.created_at.desc())
    result = await db.execute(query)
    suggestions = result.scalars().all()

    return GanttTaskSuggestionList(
        suggestions=list(suggestions),
        total=len(suggestions),
    )


@router.patch("/{company_id}/task-suggestions/{suggestion_id}", response_model=GanttTaskSuggestionRead)
async def update_task_suggestion(
    company_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    body: GanttTaskSuggestionUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    Update a task suggestion's fields or status (dismiss/restore).
    """
    await _get_company_or_404(company_id, db)

    result = await db.execute(
        select(GanttTaskSuggestion).where(
            GanttTaskSuggestion.id == suggestion_id,
            GanttTaskSuggestion.company_id == company_id,
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(suggestion, field, value)

    await db.commit()
    await db.refresh(suggestion)
    return suggestion


@router.post("/{company_id}/task-suggestions/bulk-push", response_model=BulkPushResponse)
async def bulk_push_suggestions(
    company_id: uuid.UUID,
    body: BulkPushRequest,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    Push approved task suggestions to the company's Google Sheet.

    Applies any field updates from the request body before pushing.
    Marks successfully pushed suggestions as status='pushed'.
    """
    from app.services.sheets_service import append_task_to_sheet
    from app.models.company import Company as CompanyModel

    company = await _get_company_or_404(company_id, db)

    # Need sheets_url from company record
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == company_id)
    )
    company_obj = company_result.scalar_one_or_none()
    if not company_obj or not company_obj.sheets_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Company has no Google Sheet URL configured",
        )

    pushed = 0
    failed = 0
    errors = []

    for suggestion_id in body.suggestion_ids:
        result = await db.execute(
            select(GanttTaskSuggestion).where(
                GanttTaskSuggestion.id == suggestion_id,
                GanttTaskSuggestion.company_id == company_id,
            )
        )
        suggestion = result.scalar_one_or_none()
        if not suggestion:
            errors.append({"id": str(suggestion_id), "error": "Not found"})
            failed += 1
            continue

        # Apply any field updates from the request
        if body.updates:
            update_item = body.updates.get(str(suggestion_id))
            if update_item:
                for field, value in update_item.model_dump(exclude_unset=True).items():
                    setattr(suggestion, field, value)

        # Build task dict for sheet append
        task_dict = {
            "task": suggestion.task,
            "project": suggestion.project,
            "division": suggestion.division,
            "resource": suggestion.resource,
            "suggested_start_date": suggestion.suggested_start_date,
            "suggested_end_date": suggestion.suggested_end_date,
        }

        try:
            row_number = append_task_to_sheet(company_obj.sheets_url, task_dict)
            suggestion.status = "pushed"
            suggestion.pushed_at = datetime.now(timezone.utc)
            suggestion.sheet_row_number = row_number
            pushed += 1
        except Exception as e:
            errors.append({"id": str(suggestion_id), "task": suggestion.task, "error": str(e)})
            failed += 1

    await db.commit()

    return BulkPushResponse(pushed=pushed, failed=failed, errors=errors)


@router.get("/{company_id}/meeting-context")
async def get_meeting_gantt_context(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    Return the most recent meeting's Gantt-related fields for a company.

    Used by the frontend to annotate the task table with meeting signals
    (gantt_status, gantt_notes, sentiment, gantt_task_mentions).

    Returns 204 No Content if the company has no meetings yet.
    """
    await _get_company_or_404(company_id, db)

    result = await db.execute(
        select(Meeting)
        .where(Meeting.company_id == company_id)
        .order_by(Meeting.meeting_date.desc())
        .limit(1)
    )
    meeting = result.scalar_one_or_none()

    if meeting is None:
        from fastapi import Response
        return Response(status_code=204)

    return {
        "meeting_id": str(meeting.id),
        "meeting_date": meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "gantt_status": meeting.gantt_status,
        "gantt_notes": meeting.gantt_notes,
        "gantt_task_mentions": meeting.gantt_task_mentions or [],
        "sentiment": meeting.sentiment,
        "sentiment_reason": meeting.sentiment_reason,
    }


@router.get("/portfolio-overview", response_model=PortfolioOverviewResponse)
async def get_portfolio_overview(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    Return one row per company with stage counts + KPI scores from their
    latest GanttSnapshot. Companies with no snapshot are included with
    zero counts and null KPIs.
    """
    # ── 1. Fetch all companies ordered by name ────────────────────────────────
    companies_result = await db.execute(select(Company).order_by(Company.name))
    companies = companies_result.scalars().all()

    if not companies:
        return PortfolioOverviewResponse(rows=[], total_companies=0)

    company_ids = [c.id for c in companies]

    # ── 2. Fetch latest snapshot per company in one query ─────────────────────
    # Use a subquery: rank snapshots per company by (upload_date DESC, created_at DESC)
    # then keep only rank=1.
    from sqlalchemy import over, desc
    from sqlalchemy.dialects.postgresql import aggregate_order_by  # noqa – not used
    from sqlalchemy import func as sqlfunc

    # Subquery: add row_number partitioned by company_id
    rn_col = sqlfunc.row_number().over(
        partition_by=GanttSnapshot.company_id,
        order_by=[
            GanttSnapshot.upload_date.desc(),
            GanttSnapshot.created_at.desc(),
        ],
    ).label("rn")

    subq = (
        select(GanttSnapshot, rn_col)
        .where(GanttSnapshot.company_id.in_(company_ids))
        .subquery()
    )

    latest_result = await db.execute(
        select(GanttSnapshot).from_statement(
            select(subq).where(subq.c.rn == 1)
        )
    )

    # Build lookup: company_id → snapshot
    snapshots_by_company: dict[uuid.UUID, GanttSnapshot] = {}
    for snap in latest_result.scalars().all():
        snapshots_by_company[snap.company_id] = snap

    # ── 3. Build portfolio rows ───────────────────────────────────────────────
    _STAGE_KEY_MAP = {
        "Yet to Start": "yet_to_start",
        "Delayed": "delayed",
        "In Progress": "in_progress",
        "Done": "done",
        "Done but Delayed": "done_but_delayed",
    }

    rows: list[PortfolioRow] = []
    for company in companies:
        snap = snapshots_by_company.get(company.id)

        if snap is None:
            rows.append(
                PortfolioRow(
                    company_id=company.id,
                    company_name=company.name,
                    company_status=company.status.value if company.status else None,
                    has_snapshot=False,
                )
            )
            continue

        # Count stages from tasks JSONB
        counts: dict[str, int] = {k: 0 for k in _STAGE_KEY_MAP.values()}
        tasks: list[dict] = snap.tasks or []
        for task in tasks:
            stage_label = (task.get("stage") or "").strip()
            key = _STAGE_KEY_MAP.get(stage_label)
            if key:
                counts[key] += 1

        rows.append(
            PortfolioRow(
                company_id=company.id,
                company_name=company.name,
                company_status=company.status.value if company.status else None,
                yet_to_start=counts["yet_to_start"],
                delayed=counts["delayed"],
                in_progress=counts["in_progress"],
                done=counts["done"],
                done_but_delayed=counts["done_but_delayed"],
                total_tasks=len(tasks),
                execution_speed=snap.execution_speed,
                planning_depth=snap.planning_depth,
                shipping_velocity=snap.shipping_velocity,
                has_snapshot=True,
            )
        )

    return PortfolioOverviewResponse(rows=rows, total_companies=len(rows))
