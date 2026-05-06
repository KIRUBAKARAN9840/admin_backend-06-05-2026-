
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import settings

_IST = ZoneInfo("Asia/Kolkata")

WALKAWAY_DISCOUNT_PERCENT = 5


def get_markup_percent() -> int:
    """Return the platform markup percentage (e.g. 10 for 10%)."""
    return settings.platform_markup_percent


def get_markup_multiplier() -> float:
    """Return the multiplier to apply to base prices (e.g. 1.10 for 10%)."""
    return 1 + (settings.platform_markup_percent / 100)


def get_daily_offer_discount() -> int:
    
    

    if not settings.membership_daily_offer_enabled:
        return 0

    today = datetime.now(_IST).day
    if today % 2 != 0:  # odd date → no offer
        return 0

    return settings.membership_daily_offer_discount


def get_walkaway_visited_key(client_id: int) -> str:
    """Redis key marking that user visited a gym details page today."""
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    return f"membership:visited:{client_id}:{today}"


def get_walkaway_redis_key(client_id: int) -> str:
    """Redis key for walkaway discount (set only when user returns to listing)."""
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    return f"membership:walkaway:{client_id}:{today}"


def get_seconds_until_midnight_ist() -> int:
    """Seconds remaining until midnight IST (for Redis TTL)."""
    now = datetime.now(_IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((midnight - now).total_seconds()), 1)


def apply_walkaway_discount(amount: int) -> int:
    """Apply walkaway 5% discount and return the discounted amount."""
    discount = int(amount * WALKAWAY_DISCOUNT_PERCENT / 100)
    return max(amount - discount, 0)
