"""Database queries specific to Daily Pass Bookings.

Fetches reward-related data (opt-in status, fittbot cash balance).
Promo campaign validation and redemption tracking.
"""

from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import RewardProgramOptIn
from app.models.fittbot_models.gym import Gym
from app.models.fittbot_models.referral import ReferralFittbotCash
from app.models.dailypass_models import PromoCampaign, PromoCampaignRedemption
from app.config.constants import OFFER_PRICE_PAISE
from app.services.timezone_utils import IST


class DailyPassBookingsRepository:
    """Dailypass-bookings data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_gym_details(self, gym_id: int) -> Dict:
        """Fetch gym name and operating hours."""
        result = await self.db.execute(
            select(Gym.name, Gym.operating_hours).where(Gym.gym_id == gym_id)
        )
        row = result.first()
        return {
            "gym_name": row.name if row else None,
            "operating_hours": row.operating_hours if row else None,
        }

    async def get_reward_info(self, client_id: int) -> Dict:
        """Fetch reward opt-in status and available fittbot cash for a client."""

        opt_in_result, cash_result = await self.db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id
            )
        ), await self.db.execute(
            select(ReferralFittbotCash).where(
                ReferralFittbotCash.client_id == client_id
            )
        )

        opt_in = opt_in_result.scalars().first()
        cash_entry = cash_result.scalars().first()

        return {
            "opted_in": bool(opt_in and opt_in.status == "active"),
            "available_cash_rupees": cash_entry.fittbot_cash if cash_entry else 0,
        }

    async def has_active_promo_campaign(self) -> bool:
        """Check if any active promo campaign exists with remaining redemptions."""
        result = await self.db.execute(
            select(PromoCampaign.id).where(
                PromoCampaign.is_active.is_(True),
                PromoCampaign.current_redemptions < PromoCampaign.max_redemptions,
            ).limit(1)
        )
        return result.first() is not None

    # ── Promo campaign queries ──────────────────────────────────

    async def _check_promo_code(
        self, code: str, client_id: int, lock: bool = False,
    ) -> Tuple[Optional[PromoCampaign], Optional[str]]:
        """Core promo validation. lock=True uses FOR UPDATE (redemption), lock=False is read-only (apply check)."""

        stmt = select(PromoCampaign).where(
            func.upper(PromoCampaign.code) == code.upper()
        )
        if lock:
            stmt = stmt.with_for_update()

        result = await self.db.execute(stmt)
        campaign = result.scalars().first()

        if not campaign:
            return None, "Invalid promo code"

        if not campaign.is_active:
            return None, "This promo code is no longer active"

        now = datetime.now(IST)
        if campaign.valid_from and now < campaign.valid_from:
            return None, "This promo code is not yet active"
        if campaign.valid_until and now > campaign.valid_until:
            return None, "This promo code has expired"

        if campaign.current_redemptions >= campaign.max_redemptions:
            return None, "This promo code has reached its redemption limit"

        # Check if user already redeemed this campaign
        existing = await self.db.execute(
            select(PromoCampaignRedemption).where(
                PromoCampaignRedemption.campaign_id == campaign.id,
                PromoCampaignRedemption.client_id == str(client_id),
            )
        )
        if existing.scalars().first():
            return None, "You have already redeemed this promo code"

        return campaign, None

    async def check_promo_code(
        self, code: str, client_id: int,
    ) -> Tuple[Optional[PromoCampaign], Optional[str]]:
        """Read-only validation for Apply button. No row lock."""
        return await self._check_promo_code(code, client_id, lock=False)

    async def validate_promo_code(
        self, code: str, client_id: int,
    ) -> Tuple[Optional[PromoCampaign], Optional[str]]:
        """Locked validation for redemption. Uses FOR UPDATE to prevent race conditions."""
        return await self._check_promo_code(code, client_id, lock=True)

    async def record_redemption(
        self, campaign: PromoCampaign, client_id: int, gym_id: int, daily_pass_id: str,
        order_id: str,
    ) -> None:
        """Record a promo redemption. Atomic counter increment + unique constraint guard."""

        # Atomic increment — avoids read-then-write race on the counter
        await self.db.execute(
            update(PromoCampaign)
            .where(PromoCampaign.id == campaign.id)
            .values(current_redemptions=PromoCampaign.current_redemptions + 1)
        )

        redemption = PromoCampaignRedemption(
            campaign_id=campaign.id,
            client_id=str(client_id),
            gym_id=str(gym_id),
            daily_pass_id=daily_pass_id,
            order_id=order_id,
        )
        self.db.add(redemption)
        await self.db.flush()
