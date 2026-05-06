import secrets
import time
from datetime import date as date_type, datetime
from typing import Optional

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dailypass_models import DailyPass, DailyPassDay, DailyPassAudit, LedgerAllocation
from app.models.fittbot_payments_models import Payment as FittbotPayment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.enums import ItemType, StatusOrder, StatusPayment
from app.config.constants import OFFER_PRICE_RUPEES, OFFER_PRICE_PAISE
from app.services.timezone_utils import IST
from app.services.reward_calculator import calculate_reward
from app.services.dailypass_pricing_calculator import calculate_dailypass_pricing

from ..daily_pass.repository import DailyPassRepository
from ..shared.pricing_service import PricingService
from .repository import DailyPassBookingsRepository
from .schemas import CalculateRewardResponse, PromoApplyResponse, PromoRedeemResponse


def _new_id(prefix: str) -> str:
    ts = int(time.time() * 1000)
    return f"{prefix}{ts}_{secrets.token_hex(4)}"


class DailyPassBookingsService:
    """Calculate reward for a dailypass purchase."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.pricing = PricingService(db, redis)
        self.dp_repo = DailyPassRepository(db, redis)
        self.details_repo = DailyPassBookingsRepository(db)

    async def calculate_reward(
        self,
        client_id: int,
        gym_id: int,
        number_of_days: int,
        head_count: Optional[int] = None,
    ) -> CalculateRewardResponse:

        gym_details = await self.details_repo.get_gym_details(gym_id)
        user_offer = await self.dp_repo.get_user_offer_eligibility(client_id)

        actual_price, offer_price, offer_active = (
            await self.pricing.get_gym_pricing_breakdown(
                gym_id,
                user_dp_eligible=user_offer["dailypass_offer_eligible"],
            )
        )

        if actual_price is None:
            return CalculateRewardResponse(
                gym_name=gym_details["gym_name"],
                operating_hours=gym_details["operating_hours"],
                dailypass_price=None,
                actual_price=None,
                number_of_days=number_of_days,
                head_count=head_count,
                total_amount=None,
                reward_amount=0,
                opted_in=False,
            )

        # ── Common pricing calculation (rupees) ──────────────
        pricing = calculate_dailypass_pricing(
            number_of_days=number_of_days,
            offer_price=offer_price,
            actual_price=actual_price,
            offer_active=offer_active,
            dp_count=user_offer["dailypass_count"],
            friends_count=head_count or 0,
        )

        # ── Reward calculation on total ──────────────────────
        reward_info = await self.details_repo.get_reward_info(client_id)
        total_amount_minor = pricing["total_amount"] * 100
        reward_result = calculate_reward(
            amount_minor=total_amount_minor,
            available_cash_rupees=reward_info["available_cash_rupees"],
        )

        # ── Show coupon code only for ₹49 offer gyms + active campaign ──
        show_coupon = False
        if offer_active:
            show_coupon = await self.details_repo.has_active_promo_campaign()


        return CalculateRewardResponse(
            gym_name=gym_details["gym_name"],
            operating_hours=gym_details["operating_hours"],
            dailypass_price=pricing["dailypass_price"],
            actual_price=actual_price,
            number_of_days=number_of_days,
            number_of_users=1 + (head_count or 0),
            head_count=head_count,
            user_amount=pricing["user_amount"],
            friends_amount=pricing["friends_amount"],
            per_user_amount=pricing["per_friend_amount"],
            total_amount=pricing["total_amount"],
            billing_lines=pricing["billing_lines"],
            show_modal_self=pricing["show_modal_self"],
            show_modal_friend=pricing["show_modal_friend"],
            reward_amount=int(reward_result["reward_amount_rupees"]),
            opted_in=reward_info["opted_in"],
            show_coupon_code=show_coupon,
        )

    # ── Promo code apply (validate only, no DB writes) ──────────

    async def apply_coupon(self, client_id: int, coupon_code: str) -> PromoApplyResponse:
        """Check if coupon is valid. Read-only — no row lock, no writes."""
        campaign, error = await self.details_repo.check_promo_code(coupon_code, client_id)
        if error:
            return PromoApplyResponse(valid=False, message=error)
        return PromoApplyResponse(
            valid=True,
            message="Coupon code applied successfully",
            coupon_code=campaign.code,
        )

    # ── Promo code redemption ───────────────────────────────────

    async def redeem_promo(
        self,
        client_id: int,
        gym_id: int,
        coupon_code: str,
        selected_date: date_type,
    ) -> PromoRedeemResponse:
        """Validate promo code and create a free daily pass (mirrors paid purchase DB writes)."""

        campaign, error = await self.details_repo.validate_promo_code(coupon_code, client_id)
        if error:
            raise HTTPException(status_code=400, detail=error)

        gym_details = await self.details_repo.get_gym_details(gym_id)
        if not gym_details["gym_name"]:
            raise HTTPException(status_code=404, detail="Gym not found")

        now_ist = datetime.now(IST)
        order_metadata = {
            "source": "promo_campaign",
            "campaign_id": campaign.id,
            "campaign_code": campaign.code,
            "campaign_description": campaign.description,
        }
        item_metadata = {
            "dates": [selected_date.isoformat()],
            "gym_id": gym_id,
            "number_of_users": 1,
            "booking_type": "single",
            "daily_pass_pricing": {
                "per_day_minor": OFFER_PRICE_PAISE,
                "per_day_rupees": OFFER_PRICE_RUPEES,
                "actual_per_day_minor": OFFER_PRICE_PAISE,
                "actual_per_day_rupees": OFFER_PRICE_RUPEES,
                "per_day_per_user_minor": OFFER_PRICE_PAISE,
                "actual_price_minor": OFFER_PRICE_PAISE,
                "actual_price_rupees": OFFER_PRICE_RUPEES,
                "gross_minor": OFFER_PRICE_PAISE,
                "discount_minor": 0,
                "subtotal_minor": OFFER_PRICE_PAISE,
                "per_user_total_minor": OFFER_PRICE_PAISE,
            },
            "head_count_breakdown": {
                "num_users": 1,
                "friends_count": 0,
                "user_amount_minor": OFFER_PRICE_PAISE,
                "friends_amount_minor": 0,
                "per_friend_amount_minor": 0,
                "offer_days_count": 1,
                "offer_days_amount": OFFER_PRICE_PAISE,
                "non_offer_days_count": 0,
                "non_offer_days_amount": 0,
            },
            "pricing_breakdown": {
                "subtotal_minor": OFFER_PRICE_PAISE,
                "subtotal_rupees": OFFER_PRICE_RUPEES,
                "discount_amount_minor": 0,
                "discount_amount_rupees": 0,
                "number_of_users": 1,
            },
            "reward_details": {},
            "promo_code": campaign.code,
            "campaign_id": campaign.id,
        }

        order = Order(
            id=_new_id("ord_"),
            customer_id=str(client_id),
            currency="INR",
            provider="promo",
            provider_order_id=None,
            gross_amount_minor=OFFER_PRICE_PAISE,
            status=StatusOrder.paid,
            order_metadata=order_metadata,
        )
        self.db.add(order)
        await self.db.flush()

        self.db.add(
            OrderItem(
                id=_new_id("itm_"),
                order_id=order.id,
                item_type=ItemType.daily_pass,
                gym_id=str(gym_id),
                unit_price_minor=OFFER_PRICE_PAISE,
                qty=1,
                item_metadata=item_metadata,
            )
        )
        await self.db.flush()

        provider_payment_id = _new_id("promo_pay_")

        daily_pass = DailyPass(
            client_id=str(client_id),
            gym_id=str(gym_id),
            start_date=selected_date,
            end_date=selected_date,
            days_total=1,
            selected_dates=[selected_date.isoformat()],
            amount_paid=OFFER_PRICE_PAISE,
            order_id=order.id,
            payment_id=provider_payment_id,
            status="active",
            booking_type="single",
            head_count=1,
            per_user_price=OFFER_PRICE_PAISE,
            purchase_timestamp=now_ist,
        )
        self.db.add(daily_pass)
        await self.db.flush()

        day_record = DailyPassDay(
            daily_pass_id=daily_pass.id,
            date=selected_date,
            status="available",
            gym_id=str(gym_id),
            client_id=str(client_id),
            dailypass_price=OFFER_PRICE_RUPEES,
        )
        self.db.add(day_record)
        await self.db.flush()

        self.db.add(
            DailyPassAudit(
                daily_pass_id=daily_pass.id,
                action="purchase",
                details="Daily pass purchased for 1 days, 1 user(s)",
                timestamp=now_ist,
                client_id=str(client_id),
                actor="system",
            )
        )

        self.db.add(
            LedgerAllocation(
                daily_pass_id=daily_pass.id,
                pass_day_id=day_record.id,
                gym_id=str(gym_id),
                client_id=str(client_id),
                payment_id=provider_payment_id,
                amount=OFFER_PRICE_PAISE,
                amount_net_minor=OFFER_PRICE_PAISE,
                allocation_date=now_ist.date(),
                status="allocated",
            )
        )

        self.db.add(
            Payment(
                id=_new_id("pay_"),
                order_id=order.id,
                customer_id=str(client_id),
                amount_minor=OFFER_PRICE_PAISE,
                currency="INR",
                provider="promo",
                provider_payment_id=provider_payment_id,
                status=StatusPayment.captured,
                captured_at=now_ist,
                payment_metadata={
                    "campaign_id": campaign.id,
                    "campaign_code": campaign.code,
                },
            )
        )

        self.db.add(
            FittbotPayment(
                source_type="daily_pass",
                source_id=str(daily_pass.id),
                entitlement_id=str(day_record.id),
                gym_id=int(gym_id),
                client_id=int(client_id),
                amount_gross=OFFER_PRICE_RUPEES,
                amount_net=OFFER_PRICE_RUPEES,
                currency="INR",
                gateway="promo",
                gateway_payment_id=provider_payment_id,
                gateway_order_id=order.id,
                status="paid",
                paid_at=now_ist,
            )
        )

        try:
            await self.details_repo.record_redemption(
                campaign, client_id, gym_id, daily_pass.id, order_id=order.id,
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(status_code=409, detail="You have already redeemed this promo code")

        return PromoRedeemResponse(
            success=True,
            payment_captured=True,
            order_id=order.id,
            payment_id=provider_payment_id,
            daily_pass_activated=True,
            daily_pass_details={
                "daily_pass_id": daily_pass.id,
                "start_date": selected_date.isoformat(),
                "end_date": selected_date.isoformat(),
                "dates": [selected_date.isoformat()],
                "days_total": 1,
                "number_of_users": 1,
                "booking_type": "single",
                "per_user_price": OFFER_PRICE_PAISE,
                "status": "active",
            },
            subscription_activated=False,
            subscription_details=None,
            total_amount=OFFER_PRICE_PAISE,
            currency="INR",
            message="Promo code redeemed successfully",
        )
