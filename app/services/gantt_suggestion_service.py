"""
Gantt task suggestion service.

Handles:
  - Diffing Gemini-extracted suggested_gantt_tasks against the latest Gantt snapshot
  - Persisting genuinely new suggestions to the DB
  - Returning suggestions as serialisable dicts for API responses
"""
import uuid
import logging
from datetime import datetime, date
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.gantt_suggestion import GanttTaskSuggestion
from app.models.gantt import GanttSnapshot

logger = logging.getLogger(__name__)


def _is_new_task(suggestion_name: str, existing_tasks: list[dict]) -> bool:
    """
    Returns True if suggestion_name does NOT fuzzy-match any existing Gantt task.

    Matching strategy (conservative — prefer false positives over false negatives):
      1. Direct substring: needle in haystack or haystack in needle
      2. First-word match: first word of needle appears in haystack
    """
    needle = suggestion_name.lower().strip()
    if not needle:
        return False

    needle_words = needle.split()

    for t in existing_tasks:
        haystack = f"{t.get('task', '')} {t.get('project', '')}".lower().strip()
        if not haystack:
            continue
        # Direct substring match
        if needle in haystack or haystack in needle:
            return False
        # First-word anchor match (e.g. "Razorpay" matches "Razorpay webhook integration")
        if needle_words and needle_words[0] in haystack:
            return False

    return True


def _parse_date_str(date_str: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD string to date, return None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def get_latest_snapshot_tasks(
    company_id: uuid.UUID,
    db: AsyncSession
) -> list[dict]:
    """Load the latest GanttSnapshot tasks for a company."""
    result = await db.execute(
        select(GanttSnapshot)
        .where(GanttSnapshot.company_id == company_id)
        .order_by(GanttSnapshot.upload_date.desc(), GanttSnapshot.created_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    return snapshot.tasks or [] if snapshot else []


async def persist_suggestions(
    company_id: uuid.UUID,
    meeting_id: uuid.UUID,
    suggested_tasks: list[dict],
    db: AsyncSession,
) -> list[GanttTaskSuggestion]:
    """
    Diff suggested_tasks against the latest Gantt snapshot and persist
    genuinely new ones as GanttTaskSuggestion rows with status='pending'.

    Skips:
      - Tasks that fuzzy-match an existing Gantt task
      - Tasks already pending for this company (same task name, case-insensitive)

    Returns the list of newly created GanttTaskSuggestion objects.
    """
    if not suggested_tasks:
        return []

    # Load existing Gantt tasks for diff
    existing_tasks = await get_latest_snapshot_tasks(company_id, db)

    # Load already-pending suggestion names to avoid duplicates
    pending_result = await db.execute(
        select(GanttTaskSuggestion.task)
        .where(
            GanttTaskSuggestion.company_id == company_id,
            GanttTaskSuggestion.status == 'pending',
        )
    )
    pending_names = {row[0].lower().strip() for row in pending_result.all()}

    created: list[GanttTaskSuggestion] = []

    for item in suggested_tasks:
        task_name = (item.get('task') or '').strip()
        if not task_name:
            continue

        # Skip if already in Gantt
        if not _is_new_task(task_name, existing_tasks):
            logger.info(f"SUGGESTION_SKIP (exists in Gantt): {task_name!r}")
            continue

        # Skip if already pending
        if task_name.lower() in pending_names:
            logger.info(f"SUGGESTION_SKIP (already pending): {task_name!r}")
            continue

        suggestion = GanttTaskSuggestion(
            company_id=company_id,
            meeting_id=meeting_id,
            task=task_name,
            project=item.get('project'),
            division=item.get('division'),
            resource=item.get('resource'),
            suggested_start_date=_parse_date_str(item.get('suggested_start_date')),
            suggested_end_date=_parse_date_str(item.get('suggested_end_date')),
            note=item.get('note'),
            status='pending',
        )
        db.add(suggestion)
        created.append(suggestion)
        pending_names.add(task_name.lower())  # prevent duplicates within same batch
        logger.info(f"SUGGESTION_CREATED: {task_name!r}")

    if created:
        await db.flush()  # assign IDs without committing (caller commits)

    return created


def suggestion_to_dict(s: GanttTaskSuggestion) -> dict:
    """Serialise a GanttTaskSuggestion to a plain dict for API responses."""
    return {
        "id": str(s.id),
        "company_id": str(s.company_id),
        "meeting_id": str(s.meeting_id),
        "task": s.task,
        "project": s.project,
        "division": s.division,
        "resource": s.resource,
        "suggested_start_date": s.suggested_start_date.isoformat() if s.suggested_start_date else None,
        "suggested_end_date": s.suggested_end_date.isoformat() if s.suggested_end_date else None,
        "note": s.note,
        "status": s.status,
        "pushed_at": s.pushed_at.isoformat() if s.pushed_at else None,
        "sheet_row_number": s.sheet_row_number,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
