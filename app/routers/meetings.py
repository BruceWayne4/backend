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
        
        await db.commit()
        
        logger.info(f"Meeting uploaded successfully. ID: {meeting.id}, Commitments: {len(commitments_created)}")
        
        return MeetingUploadResponse(
            success=True,
            meeting_id=str(meeting.id),
            meeting_date=str(meeting_data['date']),
            commitments_count=len(commitments_created)
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
            
            results.append({
                'meeting_id': str(meeting.id),
                'date': str(meeting_data['date']),
                'commitments_count': commitment_count
            })
        
        await db.commit()
        
        logger.info(f"Test dump uploaded successfully. Meetings: {len(results)}")
        
        return MeetingTestUploadResponse(
            success=True,
            meetings_processed=len(results),
            results=results
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
