from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import CheckEligibilityResponse, GetMacrosMicrosResponse, SetTargetRequest, MessageResponse
from .service import DietService

router = APIRouter(prefix="/diet_apis", tags=["Diet V2"])


@router.get("/get_macros_micros", response_model=GetMacrosMicrosResponse)
@log_exceptions
async def get_macros_micros(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = DietService(db, redis)
    return await service.get_macros_micros(client_id)


@router.get("/check_eligibility", response_model=CheckEligibilityResponse)
@log_exceptions
async def check_eligibility(
    request: Request,
    client_lat: Optional[float] = Query(None),
    client_lng: Optional[float] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = DietService(db, redis)
    return await service.check_eligibility(client_id, client_lat, client_lng)


@router.post("/set_target", response_model=MessageResponse)
@log_exceptions
async def set_target(
    req: SetTargetRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = DietService(db, redis)
    return await service.set_target(client_id, req)


