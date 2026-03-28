"""
Meetings router - handles meeting notes upload and retrieval.
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.meeting import Meeting
from app.models.commitment import Commitment
from app.schemas.meeting import (
    MeetingRead,
    MeetingList,
    MeetingUploadResponse,
    MeetingTestUploadResponse
)
from app.services import docx_parser, gemini_parser
from app.services.gantt_suggestion_service import persist_suggestions, suggestion_to_dict
import uuid
from datetime import datetime, date
import os
import tempfile
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meetings"])


def parse_due_date(date_str):
    """
    Parse a date string (YYYY-MM-DD) into a Python date object.
    Returns None if date_str is None or invalid.
    """
    if not date_str:
        return None
    try:
        if isinstance(date_str, date):
            return date_str
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse due_date '{date_str}': {e}")
        return None


@router.post("/companies/{company_id}/meetings/upload-docx", response_model=MeetingUploadResponse)
async def upload_meeting_docx(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a DOCX file containing ONE meeting's notes.
    Parses the meeting and stores in database.
    """
    if not file.filename or not file.filename.endswith('.docx'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files accepted"
        )
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
        content = await file.read()
        tmp.write(content)
        temp_path = tmp.name
    
    try:
        # Parse DOCX (single meeting)
        meeting_data = docx_parser.parse_single_meeting_docx(temp_path)
        
        if not meeting_data['date']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not extract meeting date from file"
            )
        
        # Parse with Gemini
        ai_parsed = await gemini_parser.parse_meeting_with_gemini(
            meeting_data['raw_text']
        )
        
        # Create Meeting record
        meeting = Meeting(
            company_id=company_id,
            meeting_date=meeting_data['date'],
            raw_notes=meeting_data['raw_text'],
            docx_filename=file.filename,
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
        commitments_created = []
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
            commitments_created.append(commitment)

        # Diff and persist Gantt task suggestions
        new_suggestions = await persist_suggestions(
            company_id=company_id,
            meeting_id=meeting.id,
            suggested_tasks=ai_parsed.get('suggested_gantt_tasks', []),
            db=db,
        )

        await db.commit()
        
        logger.info(
            f"Meeting uploaded successfully. ID: {meeting.id}, "
            f"Commitments: {len(commitments_created)}, "
            f"Suggestions: {len(new_suggestions)}"
        )
        
        return MeetingUploadResponse(
            success=True,
            meeting_id=str(meeting.id),
            meeting_date=str(meeting_data['date']),
            commitments_count=len(commitments_created),
            suggestions_count=len(new_suggestions),
            suggestions=[suggestion_to_dict(s) for s in new_suggestions],
        )
    
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@router.post("/companies/{company_id}/meetings/upload-test-dump", response_model=MeetingTestUploadResponse)
async def upload_test_meeting_dump(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    TEST ENDPOINT: Upload Meeting_Dump.docx with multiple meetings.
    This is for testing only. Production uses single meeting uploads.
    """
    if not file.filename or not file.filename.endswith('.docx'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files accepted"
        )
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
        content = await file.read()
        tmp.write(content)
        temp_path = tmp.name
    
    try:
        # Parse all meetings from dump file
        meetings_data = docx_parser.parse_multi_meeting_docx(temp_path)
        
        results = []
        all_suggestions = []
        for meeting_data in meetings_data:
            # Check if meeting already exists
            from sqlalchemy import select, delete
            existing_meeting = await db.scalar(
                select(Meeting).where(
                    Meeting.company_id == company_id,
                    Meeting.meeting_date == meeting_data['date']
                )
            )
            
            # Parse with Gemini
            ai_parsed = await gemini_parser.parse_meeting_with_gemini(
                meeting_data['raw_text']
            )
            
            # UPSERT: Update existing or create new
            if existing_meeting:
                logger.info(f"Updating existing meeting {existing_meeting.id} for date {meeting_data['date']}")
                
                # Update existing meeting fields
                existing_meeting.raw_notes = meeting_data['raw_text']
                existing_meeting.docx_filename = file.filename
                existing_meeting.ai_summary = ai_parsed.get('summary')
                existing_meeting.decisions = ai_parsed.get('decisions')
                existing_meeting.risks = ai_parsed.get('risks')
                existing_meeting.gap_assessment = ai_parsed.get('gap_assessment')
                existing_meeting.alignment_points = ai_parsed.get('alignment_points')
                existing_meeting.gantt_status = ai_parsed.get('gantt_status')
                existing_meeting.gantt_notes = ai_parsed.get('gantt_notes')
                existing_meeting.gantt_task_mentions = ai_parsed.get('gantt_task_mentions')
                existing_meeting.vc_recommendations = ai_parsed.get('vc_recommendations')
                existing_meeting.initiatives = ai_parsed.get('initiatives')
                existing_meeting.financials_mentioned = ai_parsed.get('financials_mentioned')
                existing_meeting.sentiment = ai_parsed.get('sentiment')
                existing_meeting.sentiment_reason = ai_parsed.get('sentiment_reason')
                existing_meeting.parsed_at = datetime.utcnow()
                
                meeting = existing_meeting
                
                # Delete old commitments for this meeting
                await db.execute(
                    delete(Commitment).where(
                        Commitment.meeting_id == meeting.id
                    )
                )
                await db.flush()
            else:
                logger.info(f"Creating new meeting for date {meeting_data['date']}")
                
                # Create new Meeting record
                meeting = Meeting(
                    company_id=company_id,
                    meeting_date=meeting_data['date'],
                    raw_notes=meeting_data['raw_text'],
                    docx_filename=file.filename,
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
            commitment_count = 0
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
                commitment_count += 1

            # Diff and persist Gantt task suggestions
            meeting_suggestions = await persist_suggestions(
                company_id=company_id,
                meeting_id=meeting.id,
                suggested_tasks=ai_parsed.get('suggested_gantt_tasks', []),
                db=db,
            )

            results.append({
                'meeting_id': str(meeting.id),
                'date': str(meeting_data['date']),
                'commitments_count': commitment_count,
                'suggestions_count': len(meeting_suggestions),
            })
            all_suggestions.extend([suggestion_to_dict(s) for s in meeting_suggestions])
        
        await db.commit()
        
        logger.info(f"Test dump uploaded successfully. Meetings: {len(results)}")
        
        return MeetingTestUploadResponse(
            success=True,
            meetings_processed=len(results),
            results=results,
            suggestions_count=len(all_suggestions),
            suggestions=all_suggestions,
        )
    
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@router.get("/companies/{company_id}/meetings", response_model=MeetingList)
async def list_meetings(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """List all meetings for a company, ordered by date descending."""
    result = await db.execute(
        select(Meeting)
        .where(Meeting.company_id == company_id)
        .order_by(Meeting.meeting_date.desc())
    )
    meetings = result.scalars().all()
    return {"meetings": meetings}


@router.get("/companies/{company_id}/meetings/{meeting_id}", response_model=MeetingRead)
async def get_meeting(
    company_id: uuid.UUID,
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific meeting."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.company_id == company_id
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )
    return meeting


@router.post("/companies/{company_id}/meetings/sync-from-granola")
async def sync_company_from_granola(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Sync all meetings for a company from Granola API.
    
    Fetches meeting notes from Granola, parses with Gemini AI,
    and stores in database with proper linkage.
    """
    from app.models.company import Company
    from app.database import AsyncSessionLocal
    from app.services.meeting_sync_service import sync_company_meetings
    
    # Get company name (use the request-scoped session just for this lookup)
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    # Sync meetings — each note gets its own fresh session via AsyncSessionLocal
    result = await sync_company_meetings(
        company.name, company_id, db_session_factory=AsyncSessionLocal
    )
    
    return {
        "success": result.success,
        "company_name": result.company_name,
        "notes_processed": result.notes_processed,
        "notes_skipped": result.notes_skipped,
        "notes_failed": result.notes_failed,
        "errors": result.errors,
        "duration_seconds": result.duration_seconds,
        "suggestions_count": result.suggestions_count,
        "suggestions": result.suggestions,
    }


@router.post("/meetings/sync-all-from-granola")
async def sync_all_from_granola(
    company_ids: list[uuid.UUID],
    db: AsyncSession = Depends(get_db)
):
    """
    Sync meetings for multiple companies from Granola API in parallel.
    
    Processes up to 5 companies concurrently with rate limiting.
    """
    from app.models.company import Company
    from app.database import AsyncSessionLocal
    from app.services.meeting_sync_service import sync_all_companies_parallel
    
    # Get company names
    companies = []
    for company_id in company_ids:
        company = await db.get(Company, company_id)
        if company:
            companies.append({
                'id': str(company_id),
                'name': company.name
            })
    
    # Sync in parallel
    results = await sync_all_companies_parallel(
        companies,
        AsyncSessionLocal,
        max_concurrent=5
    )
    
    # Aggregate results
    total_processed = sum(r.notes_processed for r in results if hasattr(r, 'notes_processed'))
    total_failed = sum(r.notes_failed for r in results if hasattr(r, 'notes_failed'))
    total_skipped = sum(r.notes_skipped for r in results if hasattr(r, 'notes_skipped'))
    
    return {
        "success": all(r.success for r in results if hasattr(r, 'success')),
        "companies_processed": len(results),
        "total_notes_processed": total_processed,
        "total_notes_skipped": total_skipped,
        "total_notes_failed": total_failed,
        "results": [
            {
                "company_name": r.company_name,
                "success": r.success,
                "notes_processed": r.notes_processed,
                "notes_skipped": r.notes_skipped,
                "notes_failed": r.notes_failed,
                "duration_seconds": r.duration_seconds
            }
            for r in results if hasattr(r, 'company_name')
        ]
    }
