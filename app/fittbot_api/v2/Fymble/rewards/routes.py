from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import (
    RewardDashboardResponse,
    ShowRewardsPageResponse,
    RedeemPointsRequest,
    RedeemPointsResponse,
)
from .service import RewardService

router = APIRouter(prefix="/reward_program", tags=["Rewards V2"])


@router.get("/dashboard", response_model=RewardDashboardResponse)
@log_exceptions
async def reward_dashboard(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = RewardService(db, redis)
    data = await service.get_dashboard(client_id)
    return RewardDashboardResponse(data=data)


@router.get("/show_rewards_page", response_model=ShowRewardsPageResponse)
@log_exceptions
async def show_rewards_page(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = RewardService(db, redis)
    data = await service.get_show_rewards_page(client_id)
    return ShowRewardsPageResponse(data=data)


@router.post("/redeem_points", response_model=RedeemPointsResponse)
@log_exceptions
async def redeem_points(
    request: RedeemPointsRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = RewardService(db, redis)
    result = await service.redeem_points(client_id, request.redeemable_points)
    return RedeemPointsResponse(
        message=f"Successfully redeemed {result.points_redeemed} points for ₹{result.cash_earned}",
        data=result,
    )
