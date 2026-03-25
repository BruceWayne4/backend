"""
Company CRUD endpoints — /api/v1/companies
"""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyRead, CompanyList, CompanyUpdate
from app.auth.jwt import get_current_user, TokenData

router = APIRouter(prefix="/companies", tags=["companies"])


@router.post("/", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    company = Company(**payload.model_dump())
    db.add(company)
    await db.flush()
    await db.refresh(company)
    return company


@router.get("/", response_model=CompanyList)
async def list_companies(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    result = await db.execute(select(Company).order_by(Company.name))
    companies = result.scalars().all()
    count_result = await db.execute(select(func.count()).select_from(Company))
    total = count_result.scalar_one()
    return CompanyList(companies=list(companies), total=total)


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.put("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(company, field, value)

    await db.flush()
    await db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(get_current_user),
):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    await db.delete(company)
