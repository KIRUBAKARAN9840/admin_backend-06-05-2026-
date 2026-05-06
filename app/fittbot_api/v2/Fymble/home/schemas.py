"""Pydantic request/response models for Home feed endpoint."""

from typing import Optional, List
from pydantic import BaseModel, Field


# ── Request Schemas ──────────────────────────────────────────────────


class HomeDataParams(BaseModel):
    client_lat: float
    client_lng: float
    client_id: int


class SaveGymRequestPayload(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    area: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=10)


class SaveGymRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request saved successfully"
    already_requested: bool = False


# ── Session Slot Schemas ─────────────────────────────────────────────


class HomeSessionSlot(BaseModel):
    key: int
    gym_id: int
    gym_name: Optional[str] = None
    distance_km: Optional[float] = None
    session_name: str
    session_id: int
    schedule_id: int
    trainer_id: Optional[int] = None
    date: str
    start_time: str
    end_time: str
    price: Optional[int] = None
    session_offer_active: bool = False


# ── Membership Schemas ───────────────────────────────────────────────


class HomeMembershipGym(BaseModel):
    key: int
    gym_id: int
    plan_id: Optional[int] = None
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: Optional[float] = None
    per_month_price: Optional[int] = None


# ── Festival Offer Schemas ──────────────────────────────────────────


class HomeFestivalOffer(BaseModel):
    key: int
    gym_id: int
    plan_id: Optional[int] = None
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: Optional[float] = None
    offer_price: Optional[int] = None
    original_price: Optional[int] = None
    duration: Optional[int] = None
    per_month_price: Optional[int] = None


# ── Active Bookings ─────────────────────────────────────────────────


class ActiveBookings(BaseModel):
    dailypass: bool = False
    sessions: bool = False
    gym_membership: bool = False


# ── Top-level Response ───────────────────────────────────────────────


class NutritionPackageCard(BaseModel):
    """Nutrition package status card for home page."""
    has_active_package: bool = False
    total_sessions: int = 0
    sessions_used: int = 0
    sessions_remaining: int = 0
    next_session_number: Optional[int] = None
    next_session_duration: Optional[int] = None
    next_session_unlocked: bool = False
    next_unlock_date: Optional[str] = None
    eligibility_id: Optional[int] = None


class HomeDataResponse(BaseModel):
    status: int = 200
    profile: Optional[str] = None
    credits: int = 0
    dailypass_eligibility: bool = False
    rewards_eligibility: bool = False
    refer_eligibility: bool = False
    referral_code: Optional[str] = None
    no_of_passes_left: int = 0
    nutrition_purchased: bool = False
    diet_plan_assigned: bool = False
    not_attended: bool = False
    nutrition_booking_id: Optional[int] = None
    nutrition_schedule: Optional[dict] = None
    nutrition_package: Optional[NutritionPackageCard] = None
    first_time_user: bool = False
    bookings: Optional[ActiveBookings] = None
    nutrition_slots_available: int = 0
    home_gif: str = ""
    no_gyms: bool = True
    nearby_sessions: List[HomeSessionSlot] = []
    next_day: bool = False
    earliest_slot: Optional[str] = None
    nearby_memberships: List[HomeMembershipGym] = []
    festival_offers: List[HomeFestivalOffer] = []

    def __init__(self, **data):
        super().__init__(**data)
        self.no_gyms = not self.nearby_sessions and not self.nearby_memberships


# ── Nutrition Join Schemas ──────────────────────────────────────────


class NutritionJoinData(BaseModel):
    join_time: bool
    meeting_link: bool
    link: Optional[str] = None
    session_expired: bool = False
    message: Optional[str] = None
    booking_date: str
    start_time: str
    end_time: str


class NutritionJoinResponse(BaseModel):
    status: int = 200
    data: NutritionJoinData
