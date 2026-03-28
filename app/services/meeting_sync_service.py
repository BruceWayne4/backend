"""
Meeting sync service - orchestrates syncing meetings from Granola API.
"""
import asyncio
import uuid
import logging
import time
from typing import List, Dict, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from dataclasses import dataclass, field

from app.services.granola_service import granola_service
from app.services.gemini_parser import parse_meeting_with_gemini
from app.services.gantt_suggestion_service import persist_suggestions, suggestion_to_dict
from app.models.meeting import Meeting
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of syncing meetings for a company."""
    company_name: str
    company_id: str
    success: bool = False
    notes_processed: int = 0
    notes_failed: int = 0
    notes_skipped: int = 0
    errors: List[dict] = field(default_factory=list)
    duration_seconds: float = 0
    suggestions: List[dict] = field(default_factory=list)
    suggestions_count: int = 0


def parse_due_date(date_str):
    """Parse due date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def process_granola_note(
    note: Dict,
    company_id: uuid.UUID,
    db_session_factory,
) -> List[dict]:
    """
    Process a single Granola note: transform, parse with Gemini, store in DB.

    Uses its own fresh DB session per note so that long Gemini API calls
    (10-30 s each) don't leave a shared connection idle long enough for
    Neon's idle-connection timeout to close it.

    Args:
        note: Granola note dictionary (basic info)
        company_id: UUID of the company
        db_session_factory: Callable that returns an AsyncSession context manager
    """
    # Fetch full note details with transcript
    note_id = note['id']
    logger.info(f"Processing note {note_id}: {note.get('title')}")

    full_note = await granola_service.get_note_details(note_id)

    # Transform to raw text
    raw_text = granola_service.transform_to_raw_text(full_note)

    # Extract meeting date
    scheduled_time = full_note.get('calendar_event', {}).get('scheduled_start_time')
    if scheduled_time:
        meeting_date = datetime.fromisoformat(
            scheduled_time.replace('Z', '+00:00')
        ).date()
    else:
        meeting_date = datetime.utcnow().date()

    # Parse Granola note's own updated_at for incremental sync cursor
    granola_updated_at: Optional[datetime] = None
    raw_updated_at = full_note.get('updated_at') or note.get('updated_at')
    if raw_updated_at:
        try:
            granola_updated_at = datetime.fromisoformat(
                raw_updated_at.replace('Z', '+00:00')
            )
        except (ValueError, TypeError):
            pass

    # ── Long Gemini call happens BEFORE we open the DB session ───────────────
    # This keeps the DB connection idle time to a minimum.
    ai_parsed = await parse_meeting_with_gemini(raw_text)

    # ── Open a fresh session just for the DB writes ───────────────────────────
    async with db_session_factory() as db:
        # Create Meeting record
        meeting = Meeting(
            company_id=company_id,
            meeting_date=meeting_date,
            raw_notes=raw_text,
            granola_note_id=note_id,
            granola_updated_at=granola_updated_at,
            sync_source='granola',
            ai_summary=ai_parsed.get('summary'),
            decisions=ai_parsed.get('decisions'),
            risks=ai_parsed.get('risks'),
            gap_assessment=ai_parsed.get('gap_assessment'),
            alignment_points=ai_parsed.get('alignment_points'),
            gantt_status=ai_parsed.get('gantt_status'),
            gantt_notes=ai_parsed.get('gantt_notes'),
            gantt_task_mentions=ai_parsed.get('gantt_task_mentions'),
            vc_recommendations=ai_parsed.get('vc_recommendations'),
            initiatives=ai_parsed.get('initiatives'),
            financials_mentioned=ai_parsed.get('financials_mentioned'),
            sentiment=ai_parsed.get('sentiment'),
            sentiment_reason=ai_parsed.get('sentiment_reason'),
            parsed_at=datetime.utcnow()
        )
        db.add(meeting)
        await db.flush()

        # Create Commitment records
        for comm in ai_parsed.get('commitments', []):
            commitment = Commitment(
                company_id=company_id,
                meeting_id=meeting.id,
                origin_meeting_id=meeting.id,
                person=comm.get('person') or 'Unknown',
                action=comm.get('action') or '',
                due_date=parse_due_date(comm.get('due_date')),
                source=comm.get('source') or 'founder-initiated',
                status='open'
            )
            db.add(commitment)

        # Diff and persist Gantt task suggestions
        new_suggestions = await persist_suggestions(
            company_id=company_id,
            meeting_id=meeting.id,
            suggested_tasks=ai_parsed.get('suggested_gantt_tasks', []),
            db=db,
        )

        await db.commit()
        logger.info(
            f"✅ Processed note {note_id} with {len(ai_parsed.get('commitments', []))} commitments "
            f"and {len(new_suggestions)} new Gantt suggestions"
        )
        return [suggestion_to_dict(s) for s in new_suggestions]


async def sync_company_meetings(
    company_name: str,
    company_id: uuid.UUID,
    db_session_factory,
    all_notes: Optional[List[Dict]] = None,
    # Legacy: accept a bare AsyncSession for backwards-compat with callers that
    # still pass db=<session>.  We wrap it in a factory that returns it directly.
    db: Optional[AsyncSession] = None,
) -> SyncResult:
    """
    Sync all meetings for a single company from Granola.

    Args:
        company_name: Name of the company (for Granola search)
        company_id: UUID of the company (for database linkage)
        db_session_factory: Callable returning an AsyncSession context manager.
                            Each note gets its own fresh session so long Gemini
                            calls don't leave a connection idle long enough for
                            Neon to close it.
        all_notes: Optional pre-fetched full note list from list_all_notes().
                   When provided, skips the API list call and filters in memory.
        db: Deprecated — pass db_session_factory instead.

    Returns:
        SyncResult with statistics and errors
    """
    from app.database import AsyncSessionLocal

    # Support legacy callers that pass db=<session> directly.
    # In that case we use AsyncSessionLocal as the factory (each note still
    # gets its own session; the passed-in session is used only for the
    # initial cursor query below).
    if db_session_factory is None:
        db_session_factory = AsyncSessionLocal

    start_time = time.time()

    result = SyncResult(
        company_name=company_name,
        company_id=str(company_id)
    )

    try:
        # Use pre-fetched notes if provided, otherwise do a full API scan.
        # For individual company syncs (all_notes=None), use the company's own
        # MAX(granola_updated_at) as updated_after for an incremental fetch.
        if all_notes is not None:
            notes = granola_service.filter_notes_for_company(all_notes, company_name)
            logger.info(f"📊 Found {len(notes)} notes for {company_name} (from pre-fetched list)")
        else:
            # Query the latest granola_updated_at using a short-lived session
            async with db_session_factory() as cursor_db:
                latest_ts = await cursor_db.scalar(
                    select(func.max(Meeting.granola_updated_at)).where(
                        Meeting.company_id == company_id,
                        Meeting.sync_source == 'granola',
                    )
                )
            updated_after: Optional[str] = None
            if latest_ts:
                updated_after = latest_ts.strftime('%Y-%m-%d')
                logger.info(
                    f"🔍 Fetching notes for {company_name} updated after {updated_after}…"
                )
            else:
                logger.info(f"🔍 Fetching all notes for {company_name} (first sync)…")

            notes = await granola_service.list_notes_for_company(
                company_name, updated_after=updated_after
            )
            logger.info(f"📊 Found {len(notes)} notes for {company_name}")

        for note in notes:
            try:
                # Check if already exists — use a short-lived session
                async with db_session_factory() as check_db:
                    existing = await check_db.scalar(
                        select(Meeting).where(
                            Meeting.company_id == company_id,
                            Meeting.granola_note_id == note['id']
                        )
                    )

                if existing:
                    logger.info(f"⏭️  Skipping duplicate note {note['id']}")
                    result.notes_skipped += 1
                    continue

                # Process note — opens its own fresh session internally
                note_suggestions = await process_granola_note(
                    note, company_id, db_session_factory
                )
                result.notes_processed += 1
                result.suggestions.extend(note_suggestions)

            except Exception as e:
                result.notes_failed += 1
                result.errors.append({
                    'note_id': note.get('id'),
                    'note_title': note.get('title'),
                    'error': str(e),
                    'timestamp': datetime.utcnow().isoformat()
                })
                logger.error(f"❌ Failed to process note {note.get('id')}: {e}")

        result.success = result.notes_failed == 0
        result.suggestions_count = len(result.suggestions)

    except Exception as e:
        result.success = False
        result.errors.append({
            'stage': 'company_sync',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        })
        logger.error(f"❌ Failed to sync company {company_name}: {e}")

    finally:
        result.duration_seconds = time.time() - start_time

    return result


async def sync_all_companies_parallel(
    company_list: List[dict],
    db_session_factory,
    max_concurrent: int = 5
) -> List[SyncResult]:
    """
    Process multiple companies in parallel with rate limiting.

    Fetches the full Granola note list ONCE upfront, then filters per company
    in memory — avoiding 38 redundant full-scan API calls that would exhaust
    the rate limit (25 req / 5 s burst, 5 req/s sustained).

    Args:
        company_list: List of dicts with 'name' and 'id' keys
        db_session_factory: Function that returns AsyncSession
        max_concurrent: Maximum concurrent operations

    Returns:
        List of SyncResult objects
    """
    # ── Step 1: determine bulk sync cursor from DB ───────────────────────────
    # Use the oldest MAX(granola_updated_at) across all companies as the
    # updated_after cutoff — this ensures every company gets notes it may have
    # missed, while still skipping notes older than any company's last sync.
    # On first run (no granola_updated_at values yet) this is None → full scan.
    async with db_session_factory() as cursor_db:
        min_of_max_ts = await cursor_db.scalar(
            select(func.min(
                select(func.max(Meeting.granola_updated_at))
                .where(
                    Meeting.sync_source == 'granola',
                    Meeting.granola_updated_at.isnot(None),
                )
                .correlate(None)
                .scalar_subquery()
            ))
        )

    bulk_updated_after: Optional[str] = None
    if min_of_max_ts:
        bulk_updated_after = min_of_max_ts.strftime('%Y-%m-%d')
        logger.info(
            f"📥 Incremental bulk sync: fetching notes updated after {bulk_updated_after}"
        )
    else:
        logger.info("📥 Full bulk sync: fetching all Granola notes (first run)…")

    # ── Step 2: single paginated scan of all Granola notes ──────────────────
    all_notes = await granola_service.list_all_notes(updated_after=bulk_updated_after)
    logger.info(f"📥 Fetched {len(all_notes)} total notes from Granola API")

    # ── Step 2: process each company in parallel, reusing the note list ──────
    semaphore = asyncio.Semaphore(max_concurrent)

    async def sync_with_limit(company: dict):
        async with semaphore:
            async with db_session_factory() as db:
                try:
                    result = await sync_company_meetings(
                        company['name'],
                        uuid.UUID(company['id']),
                        db,
                        all_notes=all_notes,
                    )
                    logger.info(
                        f"✅ Synced {company['name']}: "
                        f"{result.notes_processed} processed, "
                        f"{result.notes_skipped} skipped, "
                        f"{result.notes_failed} failed"
                    )
                    return result
                except Exception as e:
                    logger.error(f"❌ Failed to sync {company['name']}: {e}")
                    return SyncResult(
                        company_name=company['name'],
                        company_id=company['id'],
                        success=False,
                        errors=[{'error': str(e)}]
                    )

    tasks = [sync_with_limit(company) for company in company_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out any raw exceptions that slipped through gather
    return [r for r in results if isinstance(r, SyncResult)]
