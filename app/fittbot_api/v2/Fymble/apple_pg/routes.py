"""
Apple Payment Gateway — minimal status endpoint.

Returns just two values:
  - credits_balance     : current AI credits (CreditBalance.balance)
  - nutrition_purchased : whether the user has an active nutrition package

Both reuse the home repository so the values match what the home page shows.
"""

import asyncio

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from app.fittbot_api.v2.Fymble.home.repository import HomeRepository

from .schemas import NutritionCreditStatusResponse

router = APIRouter(prefix="/apple_pg", tags=["Apple Payment Gateway V2"])


@router.get(
    "/nutrition_credit_status",
    response_model=NutritionCreditStatusResponse,
)
@log_exceptions
async def get_nutrition_credit_status(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    repo = HomeRepository(db, redis)

    credits_balance, nutrition_status = await asyncio.gather(
        repo.fetch_credit_balance_isolated(client_id),
        repo.fetch_nutrition_status(client_id),
    )

    nutrition_purchased = nutrition_status[0]

    return NutritionCreditStatusResponse(
        credits_balance=credits_balance,
        nutrition_purchased=nutrition_purchased,
    )
