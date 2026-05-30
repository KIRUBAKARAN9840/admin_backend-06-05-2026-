from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List
from pydantic import BaseModel
from jose import jwt

from app.models.async_database import get_async_db
from app.models.corporate_models import CorporateCompany
from app.utils.security import SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/api/corporate/companies", tags=["CorporateCompanies"])

class CompanyResponseSchema(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    contact: Optional[str] = None
    website: Optional[str] = None

    class Config:
        from_attributes = True

class PaginatedCompaniesResponse(BaseModel):
    status: int
    message: str
    data: List[CompanyResponseSchema]
    total_count: int
    overall_total_count: int
    page: int
    per_page: int
    total_pages: int

async def get_current_corporate_user(request: Request):
    """
    Ensure the caller has a valid corporate session.
    """
    access_token = request.cookies.get("access_token")
    if not access_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            access_token = auth_header.split(" ")[1]
        else:
            raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("user_type") != "corporate":
            raise HTTPException(status_code=403, detail="Corporate access required")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")

@router.get("", response_model=PaginatedCompaniesResponse)
async def get_companies(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_corporate_user),
    db: AsyncSession = Depends(get_async_db)
):

    offset = (page - 1) * per_page

    # Get overall total count (without search filters)
    overall_count_query = select(func.count(CorporateCompany.id))
    overall_count_result = await db.execute(overall_count_query)
    overall_total_count = overall_count_result.scalar_one()

    # Construct count and list queries using async select statements
    count_query = select(func.count(CorporateCompany.id))
    list_query = select(CorporateCompany)

    if search:
        search_filter = CorporateCompany.name.ilike(f"%{search}%")
        count_query = count_query.where(search_filter)
        list_query = list_query.where(search_filter)

    # Get filtered count asynchronously
    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar_one()

    # Optimized pagination query utilizing DB limit and offset
    list_query = list_query.offset(offset).limit(per_page)
    list_result = await db.execute(list_query)
    companies = list_result.scalars().all()

    total_pages = (total_count + per_page - 1) // per_page

    return {
        "status": 200,
        "message": "Companies retrieved successfully",
        "data": companies,
        "total_count": total_count,
        "overall_total_count": overall_total_count,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages
    }
