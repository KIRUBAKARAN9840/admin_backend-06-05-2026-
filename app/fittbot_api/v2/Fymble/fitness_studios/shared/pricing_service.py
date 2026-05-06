"""Reusable dailypass pricing resolution.

Extracts the price-calculation logic so it can be shared across
daily_pass (listing) and dailypass_bookings (checkout preview).
"""

import asyncio
from typing import Dict, List, Optional, Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.constants import GYM_OFFER_USER_CAP, OFFER_PRICE_PAISE, OFFER_PRICE_RUPEES
from app.config.pricing import get_markup_multiplier

from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.repository import (
    DAILYPASS_HASH_KEY,
    DailyPassRepository,
)


class PricingService:
    """Resolve the display price for a dailypass gym."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.dp_repo = DailyPassRepository(db, redis)

    # ── Single-gym price (used by dailypass_bookings) ─────────

    async def get_gym_dailypass_price(
        self,
        gym_id: int,
        user_dp_eligible: bool = False,
    ) -> Optional[int]:
        """Return the resolved dailypass price (rupees) for one gym."""

        pricing_map = await self.dp_repo.fetch_dailypass_pricing([gym_id])
        offer_map = await self.dp_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.dp_repo.fetch_promo_counts([gym_id])

        return self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=user_dp_eligible,
        )

    # ── Pricing breakdown (actual + offer) for head_count ──

    async def get_gym_pricing_breakdown(
        self,
        gym_id: int,
        user_dp_eligible: bool = False,
    ) -> Tuple[Optional[int], Optional[int], bool]:
        """Return (actual_price, offer_price, offer_active) for one gym.

        actual_price:  dynamic gym price (no intro offer).
        offer_price:   ₹49 if user+gym qualifies, else same as actual_price.
        offer_active:  True when user is getting intro-offer pricing.
        """
        pricing_map = await self.dp_repo.fetch_dailypass_pricing([gym_id])
        offer_map = await self.dp_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.dp_repo.fetch_promo_counts([gym_id])

        actual_price = self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=False,
        )
        if actual_price is None:
            return None, None, False

        if not user_dp_eligible:
            return actual_price, actual_price, False

        offer_price = self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=True,
        )
        offer_active = offer_price != actual_price
        return actual_price, (offer_price if offer_active else actual_price), offer_active

    # ── Core resolution (shared logic) ───────────────────────

    @staticmethod
    def resolve_price(
        gym_id: int,
        pricing_map: dict,
        offer_map: dict,
        promo_counts: dict,
        user_dp_eligible: bool = False,
        force_offer_price: bool = False,
    ) -> Optional[int]:
        """Determine dailypass price in rupees.

        - discount_price == 4900 paise → ₹49 (no markup)
        - Otherwise → (discount_price / 100) × markup
        - force_offer_price → ₹49 for all gyms
        - Gym offer active + user < 3 bookings + gym < 50 promo users → ₹49
        """
        pricing = pricing_map.get(gym_id)
        if not pricing or not pricing.discount_price:
            return None

        if pricing.discount_price == OFFER_PRICE_PAISE:
            actual_price = OFFER_PRICE_RUPEES
        else:
            actual_price = round((pricing.discount_price / 100) * get_markup_multiplier())

        if force_offer_price:
            return OFFER_PRICE_RUPEES

        offer = offer_map.get(gym_id)
        offer_active = (
            bool(offer and offer.dailypass)
            and user_dp_eligible
            and promo_counts.get(gym_id, 0) < GYM_OFFER_USER_CAP
        )

        if offer_active:
            return OFFER_PRICE_RUPEES

        return actual_price

    # ── Bulk price map (used by daily_pass list sorting) ─────

    async def build_price_sort_map(
        self, gym_ids: List[int], user_dp_eligible: bool = False
    ) -> Dict[int, float]:
        """Build {gym_id: display_price} for sorting."""

        pipe = self.redis.pipeline(transaction=False)
        for gid in gym_ids:
            pipe.hget(DAILYPASS_HASH_KEY, str(gid))
        raw_results = await pipe.execute()

        offer_map = await self.dp_repo.fetch_offer_flags(gym_ids)
        promo_counts = await self.dp_repo.fetch_promo_counts(gym_ids)

        price_map: Dict[int, float] = {}
        for gid, raw in zip(gym_ids, raw_results):
            if raw is None:
                continue
            discount_price_paisa = int(raw)

            if discount_price_paisa == OFFER_PRICE_PAISE:
                base_actual_price = OFFER_PRICE_RUPEES
            else:
                base_actual_price = round((discount_price_paisa / 100) * get_markup_multiplier())

            offer_entry = offer_map.get(gid)
            dp_offer_enabled = bool(offer_entry and offer_entry.dailypass)
            dp_under_50 = promo_counts.get(gid, 0) < GYM_OFFER_USER_CAP

            if dp_offer_enabled and user_dp_eligible and dp_under_50:
                price_map[gid] = OFFER_PRICE_RUPEES
            else:
                price_map[gid] = base_actual_price

        return price_map
