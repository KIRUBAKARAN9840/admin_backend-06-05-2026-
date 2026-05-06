"""
Repository for the nutrition page — only the bits NOT already covered by
home's repo. Personal/session-package queries are delegated to HomeRepository.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nutrition_models import AiDietBooking, AiDietCoach


class NutritionPageRepository:

    async def fetch_active_ai_booking(
        self, session: AsyncSession, client_id: int
    ) -> Optional[AiDietBooking]:
        """Most recent active ai_diet_coach booking, or None."""
        stmt = (
            select(AiDietBooking)
            .where(
                AiDietBooking.client_id == client_id,
                AiDietBooking.status == "active",
                AiDietBooking.expires_at > datetime.now(),
            )
            .order_by(AiDietBooking.created_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def has_recent_ai_plan(
        self,
        session: AsyncSession,
        client_id: int,
        *,
        lookback_days: int = 45,
    ) -> bool:
        """True if any ai_diet_coach row was generated in the last `lookback_days`."""
        cutoff = datetime.now() - timedelta(days=lookback_days)
        stmt = (
            select(AiDietCoach.id)
            .where(
                AiDietCoach.client_id == client_id,
                AiDietCoach.created_at >= cutoff,
            )
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None
