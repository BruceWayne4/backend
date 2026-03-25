"""
Commitments router - handles commitment CRUD and status tracking.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.commitment import Commitment
from app.schemas.commitment import (
    CommitmentRead,
    CommitmentList,
    CommitmentUpdate
)
import uuid
from datetime import date, datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["commitments"])


async def update_commitment_statuses(company_id: uuid.UUID, db: AsyncSession):
    """Update commitment statuses based on due dates."""
    today = date.today()
    
    # Get all open/due-soon commitments
    result = await db.execute(
        select(Commitment).where(
            Commitment.company_id == company_id,
            Commitment.status.in_(['open', 'due-soon'])
        )
    )
    commitments = result.scalars().all()
    
    for comm in commitments:
        if comm.due_date:
            days_diff = (today - comm.due_date).days
            
            if days_diff > 0:
                comm.status = 'overdue'
                comm.days_overdue = days_diff
            elif days_diff >= -3:  # Within 3 days
                comm.status = 'due-soon'
                comm.days_overdue = None
            else:
                comm.status = 'open'
                comm.days_overdue = None
    
    await db.commit()


@router.get("/companies/{company_id}/commitments", response_model=CommitmentList)
async def list_commitments(
    company_id: uuid.UUID,
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    """
    List commitments for a company.
    Sorted with overdue first, then by due date.
    
    Query params:
    - status_filter: Filter by status (open, due-soon, overdue, resolved)
    """
    # First update statuses
    await update_commitment_statuses(company_id, db)
    
    # Build query
    query = select(Commitment).where(Commitment.company_id == company_id)
    
    if status_filter:
        query = query.where(Commitment.status == status_filter)
    
    # Order: overdue first (status='overdue'), then by due date ascending
    query = query.order_by(
        Commitment.status == 'overdue',
        Commitment.due_date.asc().nullslast()
    )
    
    result = await db.execute(query)
    commitments = result.scalars().all()
    
    # Compute days_overdue for each
    for comm in commitments:
        if comm.due_date and comm.status in ['overdue', 'due-soon']:
            days_diff = (date.today() - comm.due_date).days
            comm.days_overdue = days_diff if days_diff > 0 else None
    
    return {"commitments": commitments}


@router.get("/companies/{company_id}/commitments/{commitment_id}", response_model=CommitmentRead)
async def get_commitment(
    company_id: uuid.UUID,
    commitment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific commitment."""
    result = await db.execute(
        select(Commitment).where(
            Commitment.id == commitment_id,
            Commitment.company_id == company_id
        )
    )
    commitment = result.scalar_one_or_none()
    if not commitment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commitment not found"
        )
    
    # Compute days_overdue
    if commitment.due_date and commitment.status in ['overdue', 'due-soon']:
        days_diff = (date.today() - commitment.due_date).days
        commitment.days_overdue = days_diff if days_diff > 0 else None
    
    return commitment


@router.patch("/companies/{company_id}/commitments/{commitment_id}", response_model=CommitmentRead)
async def update_commitment(
    company_id: uuid.UUID,
    commitment_id: uuid.UUID,
    update_data: CommitmentUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update commitment status or details."""
    result = await db.execute(
        select(Commitment).where(
            Commitment.id == commitment_id,
            Commitment.company_id == company_id
        )
    )
    commitment = result.scalar_one_or_none()
    if not commitment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commitment not found"
        )
    
    # Update fields
    if update_data.status is not None:
        commitment.status = update_data.status
        if update_data.status == 'resolved':
            commitment.resolved_at = datetime.utcnow()
            if update_data.resolved_in_meeting_id:
                commitment.resolved_in_meeting_id = update_data.resolved_in_meeting_id
    
    if update_data.resolved_at is not None:
        commitment.resolved_at = update_data.resolved_at
    
    if update_data.resolved_in_meeting_id is not None:
        commitment.resolved_in_meeting_id = update_data.resolved_in_meeting_id
    
    await db.commit()
    await db.refresh(commitment)
    
    logger.info(f"Updated commitment {commitment_id} to status: {commitment.status}")
    
    # Compute days_overdue
    if commitment.due_date and commitment.status in ['overdue', 'due-soon']:
        days_diff = (date.today() - commitment.due_date).days
        commitment.days_overdue = days_diff if days_diff > 0 else None
    
    return commitment
