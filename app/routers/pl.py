"""
P&L snapshot endpoints — /api/v1/companies/{company_id}/pl
"""

import os
import uuid
import tempfile
import logging
from datetime import date, timezone, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenData, get_current_user
from app.database import get_db
from app.models.company import Company
from app.models.pl_snapshot import PLSnapshot
from app.schemas.pl import (
    PLSnapshotList,
    PLSnapshotRead,
    PLSnapshotSummaryRead,
    PLUploadResponse,
)
from app.services import pl_parser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["pl"])

# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _get_company_or_404(company_id: uuid.UUID, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{company_id}/pl/upload",
    response_model=PLUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a P&L Excel file",
    description=(
        "Upload a `.xlsx` P&L file for a company. "
        "The file is parsed immediately, key monthly financials are extracted, "
        "and an aggregate summary is computed. The result is stored as a snapshot."
    ),
)
async def upload_pl_excel(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .xlsx / .xls files are accepted",
        )

    # Write to temp file so openpyxl can open it by path
    suffix = ".xlsx" if filename.lower().endswith(".xlsx") else ".xls"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        temp_path = tmp.name

    try:
        parsed = pl_parser.parse_pl_excel(temp_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    snapshot = PLSnapshot(
        company_id=company_id,
        upload_date=date.today(),
        filename=filename,
        scenario=parsed.get("scenario", "Base"),
        months=parsed["months"],
        summary=parsed["summary"],
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    month_count = len(parsed["months"]) if parsed.get("months") else 0
    log.info(
        "PL_UPLOAD: company=%s filename=%r months=%d scenario=%s",
        company_id, filename, month_count, snapshot.scenario,
    )

    # Build response manually (PLUploadResponse has month_count, not months)
    return PLUploadResponse(
        id=snapshot.id,
        company_id=snapshot.company_id,
        upload_date=snapshot.upload_date,
        filename=snapshot.filename,
        scenario=snapshot.scenario,
        summary=snapshot.summary,
        month_count=month_count,
        created_at=snapshot.created_at,
    )


@router.get(
    "/{company_id}/pl/latest",
    response_model=PLSnapshotRead,
    summary="Get the latest P&L snapshot",
)
async def get_latest_pl(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    result = await db.execute(
        select(PLSnapshot)
        .where(PLSnapshot.company_id == company_id)
        .order_by(PLSnapshot.upload_date.desc(), PLSnapshot.created_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="No P&L snapshot found for this company")
    return snapshot


@router.get(
    "/{company_id}/pl/history",
    response_model=PLSnapshotList,
    summary="List P&L upload history (summary only, no months data)",
)
async def list_pl_history(
    company_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    result = await db.execute(
        select(PLSnapshot)
        .where(PLSnapshot.company_id == company_id)
        .order_by(PLSnapshot.upload_date.desc(), PLSnapshot.created_at.desc())
        .limit(limit)
    )
    snapshots = result.scalars().all()

    count_result = await db.execute(
        select(func.count())
        .select_from(PLSnapshot)
        .where(PLSnapshot.company_id == company_id)
    )
    total = count_result.scalar_one()

    return PLSnapshotList(
        snapshots=[PLSnapshotSummaryRead.model_validate(s) for s in snapshots],
        total=total,
    )


@router.get(
    "/{company_id}/pl/{snapshot_id}",
    response_model=PLSnapshotRead,
    summary="Get a specific P&L snapshot by ID (includes full months data)",
)
async def get_pl_snapshot(
    company_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    await _get_company_or_404(company_id, db)

    result = await db.execute(
        select(PLSnapshot).where(
            PLSnapshot.id == snapshot_id,
            PLSnapshot.company_id == company_id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="P&L snapshot not found")
    return snapshot


# ── Schemas for the pull-from-sheets endpoints ───────────────────────────────

from pydantic import BaseModel as _BaseModel

class PLSheetsPullRequest(_BaseModel):
    """Optional override URL; if omitted uses company.pl_sheets_url."""
    sheets_url: Optional[str] = None


class PLSheetsUrlUpdate(_BaseModel):
    pl_sheets_url: str


# ── set P&L sheets URL on company ─────────────────────────────────────────────

@router.patch(
    "/{company_id}/pl/set-sheets-url",
    summary="Save the P&L Google Sheets URL on this company",
)
async def set_pl_sheets_url(
    company_id: uuid.UUID,
    body: PLSheetsUrlUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    company = await _get_company_or_404(company_id, db)
    company.pl_sheets_url = body.pl_sheets_url
    await db.flush()
    await db.refresh(company)
    return {"pl_sheets_url": company.pl_sheets_url}


# ── pull from Google Sheets ────────────────────────────────────────────────────

@router.post(
    "/{company_id}/pl/pull-from-sheets",
    response_model=PLUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Pull P&L data directly from a Google Sheet",
    description=(
        "Reads the linked P&L Google Sheet (or an override URL in the request body), "
        "parses the computed monthly values, and stores a new snapshot. "
        "No file upload required."
    ),
)
async def pull_pl_from_sheets(
    company_id: uuid.UUID,
    body: PLSheetsPullRequest = PLSheetsPullRequest(),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    company = await _get_company_or_404(company_id, db)

    url = body.sheets_url or company.pl_sheets_url
    if not url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No P&L Sheets URL configured for this company. "
                "Either pass sheets_url in the request body or save one via "
                "PATCH /companies/{id}/pl/set-sheets-url"
            ),
        )

    try:
        import asyncio
        parsed = await asyncio.get_event_loop().run_in_executor(
            None, pl_parser.parse_pl_from_sheets, url
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        log.error("PL_PULL: error pulling from sheets: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to pull from Google Sheets: {exc}",
        )

    # Save URL back to company if it was passed as override and not yet stored
    if body.sheets_url and not company.pl_sheets_url:
        company.pl_sheets_url = body.sheets_url

    snapshot = PLSnapshot(
        company_id=company_id,
        upload_date=date.today(),
        filename=url,
        scenario=parsed.get("scenario", "Base"),
        months=parsed["months"],
        summary=parsed["summary"],
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    month_count = len(parsed["months"]) if parsed.get("months") else 0
    log.info(
        "PL_PULL: company=%s months=%d scenario=%s url=%s",
        company_id, month_count, snapshot.scenario, url[:80],
    )

    return PLUploadResponse(
        id=snapshot.id,
        company_id=snapshot.company_id,
        upload_date=snapshot.upload_date,
        filename=snapshot.filename,
        scenario=snapshot.scenario,
        summary=snapshot.summary,
        month_count=month_count,
        created_at=snapshot.created_at,
    )
