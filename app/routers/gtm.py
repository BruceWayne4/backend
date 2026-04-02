"""
GTM router — /api/v1/gtm (playbook) and /api/v1/companies/{id}/gtm (plans)
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.jwt import get_current_user, TokenData
from app.schemas.gtm import (
    GTMPlaybookUpload,
    GTMPlaybookRead,
    GTMPlaybookSummary,
    GTMPlanRead,
    GTMPlanSummary,
    GTMGenerateResponse,
    GTMPlanHistoryList,
)
from app.services import gtm_service

router = APIRouter(tags=["gtm"])


# ── Playbook endpoints ────────────────────────────────────────────────────────

@router.post(
    "/gtm/playbook",
    response_model=GTMPlaybookRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload / replace the active GTM playbook",
)
async def upload_playbook(
    payload: GTMPlaybookUpload,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    playbook = await gtm_service.upload_playbook(db, payload.title, payload.content)
    await db.commit()
    await db.refresh(playbook)
    return playbook


@router.get(
    "/gtm/playbook",
    response_model=GTMPlaybookRead,
    summary="Get the currently active GTM playbook",
)
async def get_playbook(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    playbook = await gtm_service.get_active_playbook(db)
    if not playbook:
        raise HTTPException(status_code=404, detail="No active playbook found")
    return playbook


@router.get(
    "/gtm/playbook/history",
    response_model=list[GTMPlaybookSummary],
    summary="List all playbook versions",
)
async def list_playbook_history(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    return await gtm_service.list_playbooks(db)


# ── Plan endpoints ────────────────────────────────────────────────────────────

@router.post(
    "/companies/{company_id}/gtm/generate",
    response_model=GTMGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger async GTM plan generation for a company",
)
async def generate_plan(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    """
    Starts async generation. Returns {plan_id, status: pending} immediately.
    Poll GET /companies/{id}/gtm/latest until status == done or failed.
    """
    # Guard: no playbook → fail fast
    playbook = await gtm_service.get_active_playbook(db)
    if not playbook:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No active GTM playbook found. Upload one at POST /api/v1/gtm/playbook first.",
        )

    plan = await gtm_service.trigger_generation(db, company_id)
    return GTMGenerateResponse(plan_id=plan.id, status="pending")


@router.get(
    "/companies/{company_id}/gtm/latest",
    response_model=GTMPlanRead,
    summary="Get the latest GTM plan for a company",
)
async def get_latest_plan(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    plan = await gtm_service.get_latest_plan(db, company_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No GTM plan found for this company")
    return plan


@router.get(
    "/companies/{company_id}/gtm/history",
    response_model=GTMPlanHistoryList,
    summary="List all GTM plan versions for a company",
)
async def list_plan_history(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    plans = await gtm_service.list_plans(db, company_id)
    return GTMPlanHistoryList(plans=plans, total=len(plans))


@router.get(
    "/companies/{company_id}/gtm/{plan_id}",
    response_model=GTMPlanRead,
    summary="Get a specific GTM plan version",
)
async def get_plan(
    company_id: uuid.UUID,
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    plan = await gtm_service.get_plan_by_id(db, company_id, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="GTM plan not found")
    return plan
