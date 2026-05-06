"""
Service for the unified nutrition page.

Reuses home's already-tested nutrition repo methods so `personal_status`
is byte-for-byte identical to the home response fields, just nested.
Adds the `ai` boolean from our new ai_diet_coach booking table on top.
"""

import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.home.repository import HomeRepository
from app.fittbot_api.v2.Fymble.home.schemas import NutritionPackageCard

from .repository import NutritionPageRepository
from .schemas import NutritionPageResponse, PersonalStatus


class NutritionPageService:

    def __init__(self, session: AsyncSession, redis: Redis):
        self.session = session
        self.redis = redis
        self.ai_repo = NutritionPageRepository()
        self.home_repo = HomeRepository(session, redis)

    async def get_page(self, client_id: int) -> NutritionPageResponse:
        # Fan out: home's nutrition status + package card + our AI booking lookup.
        # Each home method opens its own DB session, so safe under gather.
        nutrition_status, nutrition_package, ai_booking = await asyncio.gather(
            self.home_repo.fetch_nutrition_status(client_id),
            self.home_repo.fetch_nutrition_package_status(client_id),
            self.ai_repo.fetch_active_ai_booking(self.session, client_id),
        )
        nutr_purchased, diet_assigned, not_attended, nutr_schedule, nutr_booking_id = nutrition_status
        ai = ai_booking is not None

        # Only matters when ai=True. Frontend gets True → show "Generate Plan",
        # False → show "View Plan" (or, if ai=False, the AI card itself is
        # hidden so the value is irrelevant).
        create_plan = False
        if ai:
            has_recent = await self.ai_repo.has_recent_ai_plan(
                self.session, client_id, lookback_days=45,
            )
            create_plan = not has_recent

        personal_status = PersonalStatus(
            nutrition_purchased=nutr_purchased,
            diet_plan_assigned=diet_assigned,
            not_attended=not_attended,
            nutrition_booking_id=nutr_booking_id,
            nutrition_schedule=nutr_schedule,
            nutrition_package=(
                NutritionPackageCard(**nutrition_package)
                if nutrition_package else None
            ),
        )

        return NutritionPageResponse(
            ai=ai,
            personal=nutr_purchased,
            create_plan=create_plan,
            personal_status=personal_status,
        )
