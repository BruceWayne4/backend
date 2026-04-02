"""
GTM service — orchestrates playbook management and plan generation.
Generation runs async in a background task (pending → generating → done/failed).
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.gtm_plan import GTMPlan
from app.models.gtm_playbook import GTMPlaybook
from app.models.meeting import Meeting
from app.models.company import Company

logger = logging.getLogger(__name__)

# Use meetings from the last 6 weeks
MEETING_LOOKBACK_WEEKS = 6


# ── Playbook helpers ──────────────────────────────────────────────────────────

async def get_active_playbook(db: AsyncSession) -> GTMPlaybook | None:
    result = await db.execute(
        select(GTMPlaybook)
        .where(GTMPlaybook.is_active == True)
        .order_by(GTMPlaybook.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upload_playbook(db: AsyncSession, title: str, content: str) -> GTMPlaybook:
    """Deactivate all existing playbooks and insert the new one as active."""
    # Deactivate old versions
    from sqlalchemy import update
    await db.execute(
        update(GTMPlaybook).values(is_active=False)
    )

    # Get next version number
    version_result = await db.execute(select(func.max(GTMPlaybook.version)))
    last_version = version_result.scalar_one_or_none() or 0

    playbook = GTMPlaybook(
        title=title,
        content=content,
        version=last_version + 1,
        is_active=True,
    )
    db.add(playbook)
    await db.flush()
    await db.refresh(playbook)
    return playbook


async def list_playbooks(db: AsyncSession) -> list[GTMPlaybook]:
    result = await db.execute(
        select(GTMPlaybook).order_by(GTMPlaybook.version.desc())
    )
    return list(result.scalars().all())


# ── Plan helpers ──────────────────────────────────────────────────────────────

async def get_latest_plan(db: AsyncSession, company_id: uuid.UUID) -> GTMPlan | None:
    result = await db.execute(
        select(GTMPlan)
        .where(GTMPlan.company_id == company_id)
        .order_by(GTMPlan.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_plan_by_id(
    db: AsyncSession, company_id: uuid.UUID, plan_id: uuid.UUID
) -> GTMPlan | None:
    result = await db.execute(
        select(GTMPlan).where(
            GTMPlan.id == plan_id,
            GTMPlan.company_id == company_id,
        )
    )
    return result.scalar_one_or_none()


async def list_plans(db: AsyncSession, company_id: uuid.UUID) -> list[GTMPlan]:
    result = await db.execute(
        select(GTMPlan)
        .where(GTMPlan.company_id == company_id)
        .order_by(GTMPlan.created_at.desc())
    )
    return list(result.scalars().all())


# ── Generation ────────────────────────────────────────────────────────────────

async def create_pending_plan(
    db: AsyncSession, company_id: uuid.UUID, playbook_id: uuid.UUID | None
) -> GTMPlan:
    """Insert a plan row with status=pending and return it immediately."""
    plan = GTMPlan(
        company_id=company_id,
        playbook_id=playbook_id,
        generation_status="pending",
    )
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return plan


async def _run_generation(plan_id: uuid.UUID, company_id: uuid.UUID) -> None:
    """
    Background coroutine — runs in its own DB session.
    1. Mark plan as 'generating'
    2. Fetch company + meetings + playbook
    3. Call Claude
    4. Persist results
    """
    from app.services.claude_service import generate_gtm_plan

    async with AsyncSessionLocal() as db:
        try:
            plan = await db.get(GTMPlan, plan_id)
            if not plan:
                logger.error(f"GTM plan {plan_id} not found for background generation")
                return

            # Mark as generating
            plan.generation_status = "generating"
            await db.commit()
            await db.refresh(plan)

            # Fetch company
            company = await db.get(Company, company_id)
            if not company:
                plan.generation_status = "failed"
                plan.error_message = "Company not found"
                await db.commit()
                return

            # Fetch active playbook
            playbook = None
            if plan.playbook_id:
                playbook = await db.get(GTMPlaybook, plan.playbook_id)
            if not playbook:
                playbook = await get_active_playbook(db)

            if not playbook:
                plan.generation_status = "failed"
                plan.error_message = "No active playbook found. Upload a playbook first."
                await db.commit()
                return

            # Fetch meetings from last 6 weeks
            cutoff = datetime.now(timezone.utc) - timedelta(weeks=MEETING_LOOKBACK_WEEKS)
            meetings_result = await db.execute(
                select(Meeting)
                .where(
                    Meeting.company_id == company_id,
                    Meeting.meeting_date >= cutoff.date(),
                )
                .order_by(Meeting.meeting_date.desc())
            )
            meetings = list(meetings_result.scalars().all())

            if not meetings:
                plan.generation_status = "failed"
                plan.error_message = "No meetings found in the last 6 weeks for this company."
                await db.commit()
                return

            # Build meeting summaries (lightweight — not raw_notes, to stay inside token budget)
            meeting_summaries = []
            for m in meetings:
                meeting_summaries.append({
                    "date": str(m.meeting_date),
                    "summary": m.ai_summary,
                    "commitments": m.commitments,
                    "risks": m.risks,
                    "financials": m.financials_mentioned,
                    "sentiment": m.sentiment,
                    "sentiment_reason": m.sentiment_reason,
                })

            # Date range
            dates = [m.meeting_date for m in meetings]

            # Call Claude
            parsed, raw = await generate_gtm_plan(
                company_name=company.name,
                playbook_content=playbook.content,
                meeting_summaries=meeting_summaries,
            )

            # Persist results
            plan.generation_status = "done"
            plan.generated_at = datetime.now(timezone.utc)
            plan.playbook_id = playbook.id
            plan.meetings_used_count = len(meetings)
            plan.meetings_date_range_start = min(dates)
            plan.meetings_date_range_end = max(dates)
            plan.gtm_stage = parsed.get("gtm_stage")
            plan.sentiment_trend = parsed.get("sentiment_trend")
            plan.focus_this_week = parsed.get("focus_this_week")
            plan.target_customer = parsed.get("target_customer")
            plan.current_gtm_approach = parsed.get("current_gtm_approach")
            plan.recommended_actions = parsed.get("recommended_actions")
            plan.open_loops = parsed.get("open_loops")
            plan.bottlenecks = parsed.get("bottlenecks")
            plan.raw_claude_response = raw

            await db.commit()
            logger.info(f"GTM plan {plan_id} generated successfully for {company.name}")

        except Exception as exc:
            logger.exception(f"GTM plan generation failed for plan {plan_id}: {exc}")
            try:
                plan = await db.get(GTMPlan, plan_id)
                if plan:
                    plan.generation_status = "failed"
                    plan.error_message = str(exc)[:500]
                    await db.commit()
            except Exception:
                pass


async def trigger_generation(
    db: AsyncSession,
    company_id: uuid.UUID,
) -> GTMPlan:
    """
    Create a pending plan row, fire off background generation, return the pending plan.
    The caller can immediately return plan_id to the client for polling.
    """
    import asyncio

    # Get active playbook id (if any) for FK linkage
    playbook = await get_active_playbook(db)
    playbook_id = playbook.id if playbook else None

    plan = await create_pending_plan(db, company_id, playbook_id)
    await db.commit()
    await db.refresh(plan)

    # Fire-and-forget background task
    asyncio.create_task(_run_generation(plan.id, company_id))

    return plan
