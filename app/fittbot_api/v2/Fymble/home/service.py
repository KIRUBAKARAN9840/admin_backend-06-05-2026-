"""Business logic for Home feed.

Combines three sections in a single low-latency response:
1. Nearby session slots (priority: yoga/pilates/PT/zumba, fallback to others)
2. Nearby membership gyms (top 5 by distance)
3. Festival offer gyms (membership gyms with active offers)

v2 split-cache architecture (designed for million-user scale):

  ┌─────────────────────────┬──────────────┬─────────────────────────────┐
  │ Cache key               │ TTL          │ Shared by                   │
  ├─────────────────────────┼──────────────┼─────────────────────────────┤
  │ home:v2:loc:{geohash}   │ 5 min        │ ALL users in geohash6 cell  │
  │ home:v2:loc:stale:{gh}  │ 30 min       │ Stampede fallback           │
  │ home:v2:ustate:{cid}    │ 60s          │ One user                    │
  └─────────────────────────┴──────────────┴─────────────────────────────┘

Per-request flow:
  1. Single Redis pipeline reads ALL caches + first_time GETDEL + dp_shown SET NX.
  2. Location data is shared across thousands of users in a cell — DB load drops
     by ~100x compared to per-user caching.
  3. Single-flight rebuild lock + stale-while-revalidate eliminates cache stampede.
  4. Per-user session offer state is applied as a pure overlay on cached location
     data — preserves SessionPricingService.is_offer_active business logic exactly.
  5. dp_eligibility (once-per-day) and first_time_user (one-shot GETDEL) are NEVER
     cached — they retain their original semantics.

Other latency optimizations preserved from v1:
- Gym info & cover pics cached 1hr in Redis
- Gym plans cached 5 min in Redis
- Session settings fetched in single bulk query (fixes N+1)
- Redis circuit breaker + 50ms timeout on every call (fault tolerance)
"""

import asyncio
import heapq
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import (
    get_daily_offer_discount, get_markup_multiplier,
    get_walkaway_redis_key, apply_walkaway_discount,
)
from app.fittbot_api.v2.Fymble.fitness_studios.shared.utils import (
    fetch_active_membership_offers,
    resolve_offer_base_amount,
)
from app.models.async_database import get_async_sessionmaker

from app.fittbot_api.v2.Fymble.fitness_studios.shared.base_listing_service import BaseListingService
from app.fittbot_api.v2.Fymble.fitness_studios.shared.session_pricing_service import SessionPricingService
from app.fittbot_api.v2.Fymble.fitness_studios.shared.utils import to_12hr, smart_round_price
from app.fittbot_api.v2.Fymble.fitness_studios.sessions.repository import SessionRepository
from app.fittbot_api.v2.Fymble.fitness_studios.gym_membership.repository import MembershipRepository
from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.repository import DailyPassRepository
from .repository import HomeRepository, geohash
from app.utils.logging_utils import FittbotHTTPException
from .schemas import (
    ActiveBookings,
    HomeDataParams,
    HomeDataResponse,
    HomeFestivalOffer,
    HomeMembershipGym,
    HomeSessionSlot,
    NutritionJoinData,
    NutritionJoinResponse,
    NutritionPackageCard,
)

NEARBY_RADIUS_KM = 10.0
MEMBERSHIP_LIMIT = 5
SLOT_LEAD_MINUTES = 30

_HOME_GIFS = ["dailypass", "99_offer", "falling_gif", "ai_diet"]

# Tiebreak priority when sessions start at the same time
_SESSION_NAME_PRIORITY = {"personal training": 0, "yoga": 1, "zumba": 2, "pilates": 3}

# Plan-type priority (same as MembershipService)
_PLAN_TYPE_PRIORITY = {
    "individual": 0, None: 0, "": 0,
    "personal": 1, "couple": 2, "buddy": 3,
}


def _today_home_gif() -> str:
    """Rotate through home GIFs: one per day, cycling every 3 days."""
    return _HOME_GIFS[datetime.now().timetuple().tm_yday % len(_HOME_GIFS)]


def _time_to_minutes(raw_time: str) -> int:
    """Convert "HH:MM" (24hr) to minutes since midnight for sorting."""
    h, m = raw_time.split(":")
    return int(h) * 60 + int(m)


def _select_display_plan(plans: List[dict], gym_offers: dict = None) -> Optional[Tuple[int, int, int]]:
    """Pick best plan from cached plan dicts → (display_price, plan_id, duration).

    Same logic as MembershipService._select_display_plan but works on dicts.
    """
    if not plans:
        return None

    def _sort_key(p):
        pt = p.get("personal_training", False)
        plan_for = p.get("plan_for")
        if pt:
            prio = {"couple": 2, "buddy": 3}.get(plan_for, 1)
        else:
            prio = _PLAN_TYPE_PRIORITY.get(plan_for, 0)
        return (p.get("duration", 0), prio)

    best = min(plans, key=_sort_key)
    multiplier = get_markup_multiplier()

    # Resolve offer price if active
    base, _ = resolve_offer_base_amount(best["id"], best["amount"], gym_offers or {})
    price = smart_round_price(base * multiplier)

    daily_discount = get_daily_offer_discount()
    if daily_discount > 0:
        price = max(price - daily_discount, 0)
    return price, best["id"], best["duration"]


class HomeService(BaseListingService):
    """Home feed: nearby sessions + nearby memberships."""

    _error_code_prefix = "HOME"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.home_repo = HomeRepository(db, redis)
        self.sess_repo = SessionRepository(db, redis)
        self.sess_pricing = SessionPricingService(db, redis)
        self.mem_repo = MembershipRepository(db, redis)
        self.dp_repo = DailyPassRepository(db, redis)

    async def get_home_data(self, params: HomeDataParams) -> HomeDataResponse:

        geo_hash = geohash(params.client_lat, params.client_lng)
        today = datetime.now().date()
        today_iso = today.isoformat()

        # Track daily active user — fire-and-forget, dedup via Redis SET NX
        asyncio.create_task(
            self.home_repo.track_daily_active_user(params.client_id, today)
        )

        # ── Step 1: Single pipelined Redis read ──
        initial = await self.home_repo.get_initial_state(
            params.client_id, geo_hash, today_iso,
        )
        is_first_time = initial["first_time_consumed"]
        dp_shown_existed = initial["dp_shown_existed"]
        nondp_shown_existed = initial["nondp_shown_existed"]
        nondp_last = initial["nondp_last"]

        # ── Step 2: Resolve location + user_state in PARALLEL ──
        # Both inner builders use independent sessions, so it's safe to gather.
        # On all-cache-hit (the common case), this is just two cheap reads.
        location, user_state = await asyncio.gather(
            self._resolve_location(params, geo_hash, initial),
            self._resolve_user_state(params.client_id, initial),
        )

        # ── Step 3: Time-sensitive per-user data + session overlay reads in PARALLEL ──
        # _resolve_promo_cards uses its OWN session (DailyPassRepository)
        # self.sess_repo.get_user_offer_eligibility uses self.db
        # → different sessions, safe to gather
        promo_task = self._resolve_promo_cards(
            params.client_id, dp_shown_existed, nondp_shown_existed, nondp_last,
        )
        user_offer_task = self.sess_repo.get_user_offer_eligibility(params.client_id)
        (
            (dp_eligible, rewards_eligible, refer_eligible, passes_left, referral_code),
            user_offer,
        ) = await asyncio.gather(promo_task, user_offer_task)

        # ── Step 4: Apply per-user session offer overlay ──
        session_slots_base = location.get("session_slots_base", [])
        if session_slots_base:
            # Sequential on self.db — safe (no concurrent gather on same session)
            slot_gym_ids = list({slot["gym_id"] for slot in session_slots_base})
            booked_gyms = await self.sess_repo.fetch_user_booked_promo_gyms(
                params.client_id, slot_gym_ids,
            )
            nearby_sessions = self._apply_session_user_overlay(
                session_slots_base,
                location.get("session_offer_flags_by_gym", {}),
                location.get("session_promo_counts_by_gym", {}),
                user_offer["session_offer_eligible"],
                booked_gyms,
            )
        else:
            nearby_sessions = []

        # Earliest-time slot's start_time in 24hr "HH:MM" — `nearby_sessions` is
        # already sorted by (start_time, distance), so the head IS the earliest.
        # We convert the per-slot 12hr display string back to 24hr for this field.
        earliest_slot: Optional[str] = None
        if nearby_sessions:
            _t12 = nearby_sessions[0]["start_time"]  # e.g. "6:30 AM"
            _time_part, _period = _t12.rsplit(" ", 1)
            _h_str, _m_str = _time_part.split(":")
            _h = int(_h_str)
            if _period == "AM" and _h == 12:
                _h = 0
            elif _period == "PM" and _h != 12:
                _h += 12
            earliest_slot = f"{_h:02d}:{_m_str}"

        # ── Step 5: Assemble response ──
        bookings_dict = user_state.get("bookings") or {}
        bookings_obj = (
            ActiveBookings(**bookings_dict) if any(bookings_dict.values()) else None
        )

        # Build nutrition package card if active
        nutr_pkg_data = user_state.get("nutrition_package")
        nutr_pkg_card = NutritionPackageCard(**nutr_pkg_data) if nutr_pkg_data else None

        # Apply walkaway 5% discount overlay to membership per_month prices
        walkaway_key = get_walkaway_redis_key(params.client_id)
        walkaway_active = bool(await self.redis.exists(walkaway_key))
        raw_memberships = location.get("nearby_memberships", [])
        if walkaway_active and raw_memberships:
            raw_memberships = [
                {**m, "per_month_price": apply_walkaway_discount(m["per_month_price"])}
                if m.get("per_month_price") else m
                for m in raw_memberships
            ]

        response = HomeDataResponse(
            profile=user_state.get("profile"),
            credits=user_state.get("credits", 0),
            dailypass_eligibility=dp_eligible,
            rewards_eligibility=rewards_eligible,
            refer_eligibility=refer_eligible,
            referral_code=referral_code,
            no_of_passes_left=passes_left,
            nutrition_purchased=user_state.get("nutrition_purchased", False),
            diet_plan_assigned=user_state.get("diet_plan_assigned", False),
            not_attended=user_state.get("not_attended", False),
            nutrition_booking_id=user_state.get("nutrition_booking_id"),
            nutrition_schedule=user_state.get("nutrition_schedule"),
            nutrition_package=nutr_pkg_card,
            bookings=bookings_obj,
            nutrition_slots_available=user_state.get("nutrition_slots_available", 0),
            home_gif=_today_home_gif(),
            nearby_sessions=nearby_sessions,
            next_day=bool(location.get("session_next_day", False)),
            earliest_slot=earliest_slot,
            nearby_memberships=raw_memberships,
            festival_offers=location.get("festival_offers", []),
            first_time_user=is_first_time,
        )
        return response

    # ── Location resolution (cache hit / rebuild / stale fallback) ──

    async def _resolve_location(
        self, params: HomeDataParams, geo_hash: str, initial: dict,
    ) -> dict:
        """Return the location-derived payload for this geohash cell.

        Resolution order:
          1. Fresh cache hit → use it.
          2. Acquire single-flight rebuild lock → rebuild + cache + return.
          3. Failed to acquire lock + stale fallback exists → serve stale.
          4. No stale either → degrade by rebuilding ourselves (don't write cache).
        """
        cached = initial.get("location")
        if cached is not None:
            return cached

        got_lock = await self.home_repo.try_acquire_rebuild_lock(geo_hash)
        if got_lock:
            try:
                payload = await self._build_location_payload(params)
                await self.home_repo.cache_location(geo_hash, payload)
                return payload
            finally:
                await self.home_repo.release_rebuild_lock(geo_hash)

        stale = initial.get("location_stale")
        if stale is not None:
            return stale

        return await self._build_location_payload(params)

    # ── User state resolution (cache hit / rebuild) ────────────────

    async def _resolve_user_state(self, client_id: int, initial: dict) -> dict:
        """Return the per-user state, building if cache missed."""
        cached = initial.get("user_state")
        if cached is not None:
            return cached

        state = await self._build_user_state(client_id)
        # Fire-and-forget cache write — don't block response
        asyncio.create_task(
            self.home_repo.cache_user_state(client_id, state)
        )
        return state

    # ── Location payload builder (cache miss path) ─────────────────

    async def _build_location_payload(self, params: HomeDataParams) -> dict:
        """Build location-derived data — runs only on cache miss / stampede.

        Returns a JSON-serializable dict shared across all users in this geohash cell.
        Contains NO client-specific data — per-user offer overlay is applied later.
        """
        AsyncSessionLocal = get_async_sessionmaker()

        # ── Phase 1: Hydrate caches in parallel — each on its own session ──
        async def _hydrate_geo():
            async with AsyncSessionLocal() as session:
                await self.geo.hydrate(session)

        async def _hydrate_sessions():
            async with AsyncSessionLocal() as session:
                await SessionRepository(session, self.redis).hydrate()

        async def _hydrate_membership():
            async with AsyncSessionLocal() as session:
                await MembershipRepository(session, self.redis).hydrate()

        await asyncio.gather(
            _hydrate_geo(), _hydrate_sessions(), _hydrate_membership(),
        )

        # ── Phase 2: Nearby gyms ──
        distance_map = await self.geo.get_nearby_distances(
            params.client_lat, params.client_lng, NEARBY_RADIUS_KM,
        )
        nearby_ids = set(distance_map.keys())

        if not nearby_ids:
            return {
                "session_slots_base": [],
                "session_next_day": False,
                "session_offer_flags_by_gym": {},
                "session_promo_counts_by_gym": {},
                "nearby_memberships": [],
                "festival_offers": [],
            }

        # ── Phase 3: Pre-fetch shared gym data (own session, sequential — safe) ──
        all_gym_ids = list(nearby_ids)
        async with AsyncSessionLocal() as session:
            shared_repo = HomeRepository(session, self.redis)
            gyms_map = await shared_repo.fetch_gyms_cached(all_gym_ids)
            cover_pics = await shared_repo.fetch_cover_pics_cached(all_gym_ids)

        # ── Phase 4: Build sections in parallel — each on its own session ──
        async def _sessions_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_nearby_sessions(
                    session, params, nearby_ids, distance_map, gyms_map, cover_pics,
                )

        async def _memberships_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_nearby_memberships(
                    session, nearby_ids, distance_map, gyms_map, cover_pics,
                )

        async def _festival_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_festival_offers(
                    session, nearby_ids, distance_map, gyms_map, cover_pics,
                )

        sessions_payload, memberships_result, festival_result = await asyncio.gather(
            _sessions_branch(), _memberships_branch(), _festival_branch(),
        )

        return {
            "session_slots_base": sessions_payload["slots"],
            "session_next_day": sessions_payload["next_day"],
            "session_offer_flags_by_gym": sessions_payload["offer_flags_by_gym"],
            "session_promo_counts_by_gym": sessions_payload["promo_counts_by_gym"],
            "nearby_memberships": memberships_result,
            "festival_offers": festival_result,
        }

    # ── User-state builder (cache miss path) ───────────────────────

    async def _build_user_state(self, client_id: int) -> dict:
        """Build per-user time-sensitive state.

        All queries run in parallel with INDEPENDENT sessions (no shared self.db),
        so this is safe under asyncio.gather. Excludes dp_eligibility and
        first_time_user — those are computed fresh on every request.
        """
        credits, profile, nutrition_status, has_bookings, nutr_slots, nutr_package = await asyncio.gather(
            self.home_repo.fetch_credit_balance_isolated(client_id),
            self.home_repo.fetch_client_profile(client_id),
            self.home_repo.fetch_nutrition_status(client_id),
            self.home_repo.check_active_bookings(client_id),
            self.home_repo.fetch_nutrition_slots_available(),
            self.home_repo.fetch_nutrition_package_status(client_id),
        )
        nutr_purchased, diet_assigned, not_attended, nutr_schedule, nutr_booking_id = nutrition_status
        return {
            "profile": profile,
            "credits": credits,
            "nutrition_purchased": nutr_purchased,
            "diet_plan_assigned": diet_assigned,
            "not_attended": not_attended,
            "nutrition_schedule": nutr_schedule,
            "nutrition_booking_id": nutr_booking_id,
            "bookings": has_bookings,
            "nutrition_slots_available": nutr_slots,
            "nutrition_package": nutr_package,
        }

    # ── Promo cards: dailypass + rewards/refer rotation ────────────

    async def _resolve_promo_cards(
        self,
        client_id: int,
        dp_shown_existed: bool,
        nondp_shown_existed: bool,
        nondp_last: Optional[str],
    ) -> Tuple[bool, bool, bool, int, Optional[str]]:
        """Resolve all three home promo cards + dailypass passes_left counter.

        Returns (dp_eligible, rewards_eligible, refer_eligible, passes_left, referral_code).
        referral_code is non-None ONLY when refer_eligible=True (avoids unnecessary fetch).

        Two independent per-day slots:
          1. DP slot   — `home:dp_shown:{cid}:{today}`     (existing, preserved)
          2. Non-DP slot — `home:nondp_shown:{cid}:{today}` (rewards/refer rotation)

        Per-call sequencing (matters for DP-eligible users on the SAME day):
          - Call 1: DP fires (DP=True). Non-DP slot is NOT touched on this call —
            so the user sees a single DP modal, not DP+rewards together.
          - Call 2: DP slot is already burned → DP=False → non-DP slot fires →
            rewards/refer = True (whichever the rotation pointer says next).
          - Call 3+: both slots burned → all three False.

        For DP-INELIGIBLE users, call 1 already shows the non-DP modal directly
        (DP=False from DB), so they get exactly 1 modal/day max.

        The rotation pointer (`home:nondp_last:{cid}`) only advances when a non-DP
        modal is actually shown. DP-only days do not disturb it.
        """
        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            repo = DailyPassRepository(session, self.redis)
            dp_offer = await repo.get_user_offer_eligibility(client_id)

        db_dp_eligible = dp_offer["dailypass_offer_eligible"]
        passes_left = max(3 - dp_offer["dailypass_count"], 0)

        today = datetime.now().date()

        # ── DP slot ──
        dp_eligible = False
        dp_just_shown = False
        if db_dp_eligible:
            if dp_shown_existed:
                # Pipeline read confirmed DP slot already burned today
                dp_eligible = False
            else:
                # Possibly first DP-eligible call today — atomically claim
                dp_eligible = await self.home_repo.check_and_mark_dp_shown_today(
                    client_id, today,
                )
                dp_just_shown = dp_eligible

        # ── Non-DP slot ──
        # Skipped on the SAME call where DP just fired (sequential modal UX):
        # call 1 = DP, call 2 = rewards/refer, call 3+ = nothing.
        rewards_eligible = False
        refer_eligible = False
        referral_code: Optional[str] = None
        if not dp_just_shown and not nondp_shown_existed:
            # Default first-ever non-DP modal = "rewards"; alternate after that
            next_promo = "refer" if nondp_last == "rewards" else "rewards"

            won = await self.home_repo.try_claim_nondp_slot(
                client_id, today, next_promo,
            )
            if won:
                # Advance the rotation pointer only when we actually showed it
                await self.home_repo.advance_nondp_pointer(client_id, next_promo)
                if next_promo == "rewards":
                    rewards_eligible = True
                else:
                    refer_eligible = True
                    # Fetch referral code only when the refer modal actually fires.
                    # Cache is permanent (immutable per user) so this is a single
                    # Redis GET on the steady-state path.
                    referral_code = await self.home_repo.fetch_referral_code(client_id)

        return dp_eligible, rewards_eligible, refer_eligible, passes_left, referral_code

    # ── Section 1: Nearby Session Slots (BASE — no per-user overlay) ──

    async def _build_nearby_sessions(
        self,
        db_session: AsyncSession,
        params: HomeDataParams,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> dict:
        """Build session slot BASE data (no per-user offer applied).

        Returns:
            {
                "slots": [dict, ...],  # full HomeSessionSlot fields except price/session_offer_active
                "offer_flags_by_gym": {gym_id_str: bool},  # for is_offer_active overlay
                "promo_counts_by_gym": {gym_id_str: int},  # for is_offer_active overlay
            }

        NOTE: per-user session_offer_eligible + booked_promo_gyms are deliberately
        excluded — they're applied at request time as a pure overlay so location
        data can be safely shared across all users in a geohash cell.
        """
        # Use repos bound to this branch's isolated session
        sess_repo = SessionRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)

        # 1. Session-enabled candidates
        sess_enabled_ids = await sess_repo.get_session_enabled_gym_ids()
        session_candidates = nearby_ids & sess_enabled_ids

        if not session_candidates:
            return {"slots": [], "next_day": False, "offer_flags_by_gym": {}, "promo_counts_by_gym": {}}

        # 2. Fetch today's schedules (single DB query)
        now = datetime.now()
        today = now.date()
        min_start = (now + timedelta(minutes=SLOT_LEAD_MINUTES)).time()

        schedules = await home_repo.fetch_today_schedules(
            session_candidates, today, min_start,
        )
        slot_date = today
        
        if not schedules:
            # Fallback: nothing left today → show tomorrow's slots (full day)
            tomorrow = today + timedelta(days=1)
           
            schedules = await home_repo.fetch_today_schedules(
                session_candidates, tomorrow, time(0, 0),
            )
            slot_date = tomorrow

        if not schedules:
            return {"slots": [], "next_day": False, "offer_flags_by_gym": {}, "promo_counts_by_gym": {}}

        # 3. Priority session IDs (yoga/pilates/zumba/PT)
        priority_ids = await home_repo.resolve_priority_session_ids()

        # Pick ONE slot per session_id — the nearest gym offering it
        best_per_session: Dict[int, object] = {}
        for sched in schedules:
            existing = best_per_session.get(sched.session_id)
            if existing is None or distance_map.get(sched.gym_id, 999) < distance_map.get(existing.gym_id, 999):
                best_per_session[sched.session_id] = sched

        # Sort: nearest distance first; same distance → earliest start time
        session_names_map = await home_repo.fetch_session_names(
            {s.session_id for s in best_per_session.values()}
        )

        def _session_sort_key(sched):
            return (sched.start_time, distance_map.get(sched.gym_id, 999))

        available_slots = sorted(best_per_session.values(), key=_session_sort_key)

        # 5. Pricing — bulk fetch (LOCATION-only data; user offer applied later as overlay)
        slot_gym_ids = list({s.gym_id for s in available_slots})
        slot_session_ids = list({s.session_id for s in available_slots})

        offer_map = await sess_repo.fetch_offer_flags(slot_gym_ids)
        promo_counts = await sess_repo.fetch_promo_counts(slot_gym_ids)

        # Fetch session settings — single bulk query (fixes N+1)
        multiplier = get_markup_multiplier()
        raw_settings = await home_repo.fetch_bulk_session_settings(
            slot_gym_ids, slot_session_ids,
        )
        # Convert dicts to SimpleNamespace so .final_price works below
        settings_cache: Dict[Tuple[int, int], object] = {
            k: SimpleNamespace(**v) for k, v in raw_settings.items()
        }

        # 6. Build slot base data — store actual_price; defer offer_active to overlay
        slots_out: List[dict] = []
        for sched in available_slots:
            gid = sched.gym_id
            gym = gyms_map.get(gid)
            if not gym:
                continue

            setting = settings_cache.get((gid, sched.session_id))
            actual_price = None
            if setting and setting.final_price:
                actual_price = round(setting.final_price * multiplier)

            raw_start = (
                sched.start_time.strftime("%H:%M")
                if hasattr(sched.start_time, "strftime")
                else str(sched.start_time)[:5]
            )
            raw_end = (
                sched.end_time.strftime("%H:%M")
                if hasattr(sched.end_time, "strftime")
                else str(sched.end_time)[:5]
            )

            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name

            slots_out.append({
                "key": len(slots_out),
                "gym_id": gid,
                "gym_name": gym_name.upper() if gym_name else None,
                "distance_km": round(distance, 2) if distance is not None else None,
                "session_name": session_names_map.get(sched.session_id, ""),
                "session_id": sched.session_id,
                "schedule_id": sched.id,
                "trainer_id": sched.trainer_id,
                "date": slot_date.isoformat(),
                "start_time": to_12hr(raw_start),
                "end_time": to_12hr(raw_end),
                "actual_price": actual_price,
            })

        # Serialize per-gym data needed by is_offer_active (only `.session` attr is read)
        offer_flags_by_gym = {
            str(gid): bool(getattr(offer_map.get(gid), "session", False))
            for gid in slot_gym_ids
        }
        promo_counts_by_gym = {
            str(gid): int(promo_counts.get(gid, 0))
            for gid in slot_gym_ids
        }

        return {
            "slots": slots_out,
            "next_day": slot_date != today,
            "offer_flags_by_gym": offer_flags_by_gym,
            "promo_counts_by_gym": promo_counts_by_gym,
        }

    # ── User-overlay applier (pure function, identical business logic) ──

    @staticmethod
    def _apply_session_user_overlay(
        slots_base: List[dict],
        offer_flags_by_gym: Dict[str, bool],
        promo_counts_by_gym: Dict[str, int],
        user_sess_eligible: bool,
        booked_gyms: Set[int],
    ) -> List[dict]:
        """Apply per-user offer state onto location-cached session slots.

        Reconstructs synthetic offer_map / promo_counts and calls the SAME
        SessionPricingService.is_offer_active function used by all other code paths
        — business logic is byte-identical to the original _build_nearby_sessions.
        """
        # Reconstruct as is_offer_active expects (int gym_id keys, .session attr on offer)
        offer_map = {
            int(gid_str): SimpleNamespace(session=flag)
            for gid_str, flag in offer_flags_by_gym.items()
        }
        promo_counts = {
            int(gid_str): count
            for gid_str, count in promo_counts_by_gym.items()
        }

        results: List[dict] = []
        for slot in slots_base:
            gid = slot["gym_id"]
            actual_price = slot.get("actual_price")

            offer_active = SessionPricingService.is_offer_active(
                gid, offer_map, promo_counts, booked_gyms,
                user_sess_eligible=user_sess_eligible,
            )
            display_price = 99 if offer_active else actual_price

            results.append({
                "key": slot["key"],
                "gym_id": slot["gym_id"],
                "gym_name": slot["gym_name"],
                "distance_km": slot["distance_km"],
                "session_name": slot["session_name"],
                "session_id": slot["session_id"],
                "schedule_id": slot["schedule_id"],
                "trainer_id": slot["trainer_id"],
                "date": slot["date"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "price": display_price,
                "session_offer_active": offer_active,
            })

        return results

    # ── Section 2: Nearby Membership Gyms ─────────────────────────

    async def _build_nearby_memberships(
        self,
        db_session: AsyncSession,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> List[dict]:
        """Build nearby membership gyms — returns list of dicts (no client-specific data)."""
        mem_repo = MembershipRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)

        # 1. Membership-enabled candidates
        mem_enabled_ids = await mem_repo.get_membership_enabled_gym_ids()
        mem_candidates = nearby_ids & mem_enabled_ids

        if not mem_candidates:
            return []

        # 2. Top 5 nearest — O(n) selection instead of O(n log n) full sort
        top5 = heapq.nsmallest(MEMBERSHIP_LIMIT, mem_candidates, key=lambda gid: distance_map.get(gid, 999))

        # 3. Fetch plans (cached 5min) + active offers
        plans_map = await home_repo.fetch_plans_cached(top5)
        all_offers_map = await mem_repo.fetch_active_offers(top5)

        # 4. Build response items (gym data already pre-fetched)
        results: List[dict] = []
        idx = 0
        for gid in top5:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            plans = plans_map.get(gid, [])
            plan_result = _select_display_plan(plans, all_offers_map.get(gid, {}))
            per_month = None
            selected_plan_id = None
            if plan_result:
                price, selected_plan_id, duration = plan_result
                per_month = round(price / duration) if duration > 0 else price

            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name

            results.append({
                "key": idx,
                "gym_id": gid,
                "plan_id": selected_plan_id,
                "gym_name": gym_name.upper() if gym_name else None,
                "cover_pic": cover_pics.get(gid, ""),
                "distance_km": round(distance, 2) if distance is not None else None,
                "per_month_price": per_month,
            })
            idx += 1

        return results

    # ── Section 3: Festival Offers (gyms with active membership offers) ──

    async def _build_festival_offers(
        self,
        db_session: AsyncSession,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> List[dict]:
        """Build festival offer gyms — returns list of dicts (no client-specific data)."""
        mem_repo = MembershipRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)

        # 1. Membership-enabled nearby gyms
        mem_enabled_ids = await mem_repo.get_membership_enabled_gym_ids()
        mem_candidates = list(nearby_ids & mem_enabled_ids)

        if not mem_candidates:
            return []

        # 2. Fetch active offers — only gyms with offers qualify
        all_offers_map = await fetch_active_membership_offers(db_session, mem_candidates)
        offer_gym_ids = [gid for gid in mem_candidates if gid in all_offers_map]

        if not offer_gym_ids:
            return []

        # 3. Sort by distance
        offer_gym_ids.sort(key=lambda gid: distance_map.get(gid, 999))

        # 4. Fetch plans for offer gyms
        plans_map = await home_repo.fetch_plans_cached(offer_gym_ids)

        # 5. Build response
        multiplier = get_markup_multiplier()
        daily_discount = get_daily_offer_discount()
        results: List[dict] = []
        idx = 0

        for gid in offer_gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            plans = plans_map.get(gid, [])
            gym_offers = all_offers_map.get(gid, {})

            plan_result = _select_display_plan(plans, gym_offers)
            if not plan_result:
                continue

            offer_price, selected_plan_id, duration = plan_result

            # Original price without offer (for strikethrough)
            best = min(plans, key=lambda p: (p.get("duration", 0), p.get("amount", 0)))
            original_price = smart_round_price(best["amount"] * multiplier)
            if daily_discount > 0:
                original_price = max(original_price - daily_discount, 0)

            per_month = round(offer_price / duration) if duration > 0 else offer_price
            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name

            results.append({
                "key": idx,
                "gym_id": gid,
                "plan_id": selected_plan_id,
                "gym_name": gym_name.upper() if gym_name else None,
                "cover_pic": cover_pics.get(gid, ""),
                "distance_km": round(distance, 2) if distance is not None else None,
                "offer_price": offer_price,
                "original_price": original_price,
                "duration": duration,
                "per_month_price": per_month,
            })
            idx += 1

        return results

    # ── Nutrition Join ────────────────────────────────────────────

    async def check_nutrition_join(self, booking_id: int, client_id: int) -> NutritionJoinResponse:
        booking = await self.home_repo.get_active_nutrition_booking(booking_id, client_id)

        if not booking:
            raise FittbotHTTPException(
                status_code=404,
                detail="Booking not found or not accessible",
                error_code="NUTRITION_BOOKING_NOT_FOUND",
            )

        today = datetime.now().date()
        now = datetime.now().time()
        has_link = bool(booking.meeting_link and booking.meeting_link.strip())

        booking_date_str = booking.booking_date.isoformat()
        start_str = booking.start_time.strftime("%I:%M %p")
        end_str = booking.end_time.strftime("%I:%M %p")

        # Session expired
        if today > booking.booking_date or (today == booking.booking_date and now > booking.end_time):
            return NutritionJoinResponse(data=NutritionJoinData(
                join_time=False,
                meeting_link=has_link,
                session_expired=True,
                message="Session time has passed.",
                booking_date=booking_date_str,
                start_time=start_str,
                end_time=end_str,
            ))

        # Session not started yet
        if today < booking.booking_date or (today == booking.booking_date and now < booking.start_time):
            return NutritionJoinResponse(data=NutritionJoinData(
                join_time=False,
                meeting_link=has_link,
                message="Session has not started yet. Please join at the scheduled time.",
                booking_date=booking_date_str,
                start_time=start_str,
                end_time=end_str,
            ))

        # Within time window — can join
        return NutritionJoinResponse(data=NutritionJoinData(
            join_time=True,
            meeting_link=has_link,
            link=booking.meeting_link if has_link else None,
            message=None if has_link else "Meeting link not yet available. Please wait for the nutritionist to share the link.",
            booking_date=booking_date_str,
            start_time=start_str,
            end_time=end_str,
        ))
