"""Business logic for Daily Pass active bookings.

Orchestrates repository queries and gym info to build the list
of active/upcoming daily passes for a client.
"""

import json
import logging
from typing import List, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.timezone_utils import today_ist as _today_ist

from ..shared.gym_info_repository import GymInfoRepository
from ..shared.schemas import GymAddress
from .repository import DailyPassBookingRepository
from .schemas import DailyPassBookingDetail, DailyPassListResponse

logger = logging.getLogger("bookings.dailypass.service")

CACHE_PREFIX = "bookings:dailypass:active"


def _cache_key(client_id: int, today_str: str) -> str:
    return f"{CACHE_PREFIX}:{client_id}:{today_str}"


async def invalidate_dailypass_bookings_cache(
    redis: Redis, client_id,
) -> None:
    """Delete all dailypass booking cache keys for a client.

    Called from dailypass_processor after a booking is completed/upgraded.
    Uses pattern scan so it works regardless of the date suffix.
    """
    pattern = f"{CACHE_PREFIX}:{client_id}:*"
    try:
        cursor = b"0"
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == b"0":
                break
    except Exception:
        logger.warning("DAILYPASS_BOOKINGS_CACHE_INVALIDATE_FAILED", extra={"client_id": client_id})


class DailyPassBookingService:
    """List active daily-pass bookings for a client."""

    def __init__(self, db: AsyncSession, redis: Optional[Redis] = None):
        self.db = db
        self.redis = redis
        self.repo = DailyPassBookingRepository(db)
        self.gym_info = GymInfoRepository(db)

    async def list_active(self, client_id: int) -> DailyPassListResponse:
        today = _today_ist()
        today_str = today.isoformat()

        # Check Redis cache
        if self.redis:
            key = _cache_key(client_id, today_str)
            try:
                cached = await self.redis.get(key)
                if cached:
                    return DailyPassListResponse(**json.loads(cached))
            except Exception:
                logger.warning("DAILYPASS_BOOKINGS_CACHE_READ_FAILED", extra={"client_id": client_id})

        all_passes = await self.repo.get_active_passes(client_id, today)

        # Filter out original passes that were upgraded
        upgraded_passes = [p for p in all_passes if p.status == "upgraded"]
        original_ids = {p.order_id for p in upgraded_passes if p.order_id}
        passes = [p for p in all_passes if p.id not in original_ids]


        all_gym_ids = {int(p.gym_id) for p in passes if p.gym_id}

        for p in passes:
            if p.status == "upgraded" and p.order_id:
                original = next((op for op in all_passes if op.id == p.order_id), None)
                if original:
                    all_gym_ids.add(int(original.gym_id))
        gyms_map = await self.gym_info.get_bulk_gym_info(all_gym_ids)

        results: List[DailyPassBookingDetail] = []

        for p in passes:
            remaining = await self.repo.get_remaining_count(p.id, today)
            next_dates = await self.repo.get_next_dates(p.id, today)
            booked_dates = await self.repo.get_all_booked_dates(p.id)
            current_day_id = await self.repo.get_current_or_next_day_id(p.id, today)
            can_upg = not await self.repo.has_audit_action(p.id, "upgrade")

            gym = gyms_map.get(int(p.gym_id), {})
            amount = await self.repo.get_display_price(int(p.gym_id), p.amount_paid)

            # Edited / rescheduled day breakdown
            is_upgraded = p.status == "upgraded"
            pass_id_to_check = p.order_id if (is_upgraded and p.order_id) else p.id
            has_reschedule = await self.repo.has_audit_action(pass_id_to_check, "reschedule")
            is_edited = has_reschedule or bool(p.partial_schedule)

            actual_days, rescheduled_days = None, None
            if is_edited:
                actual_days, rescheduled_days = await self.repo.get_day_breakdown(p.id)

            results.append(
                DailyPassBookingDetail(
                    pass_id=p.id,
                    gym_id=int(p.gym_id),
                    amount=amount,
                    gym_name=gym.get("name"),
                    cover_pic=gym.get("cover_pic"),
                    locality=gym.get("location"),
                    city=gym.get("city"),
                    days_total=int(p.days_total or 0),
                    booking_type=p.booking_type or "single",
                    head_count=p.head_count or 1,
                    booked_dates=booked_dates,
                    current_day_id=current_day_id,
                    selected_time=p.selected_time,
                    remaining_days=remaining,
                    next_dates=next_dates,
                    can_upgrade=can_upg,
                    actual_days=actual_days,
                    rescheduled_days=rescheduled_days,
                    address=GymAddress(**gym["address"]) if gym.get("address") else None,
                    latitude=gym.get("latitude"),
                    longitude=gym.get("longitude"),
                    owner_mobile=gym.get("owner_mobile"),
                )
            )

        response = DailyPassListResponse(client_id=str(client_id), passes=results)

        # Cache for the rest of the day (max 24h)
        if self.redis:
            key = _cache_key(client_id, today_str)
            try:
                await self.redis.set(key, response.model_dump_json(), ex=86400)
            except Exception:
                logger.warning("DAILYPASS_BOOKINGS_CACHE_WRITE_FAILED", extra={"client_id": client_id})

        return response
