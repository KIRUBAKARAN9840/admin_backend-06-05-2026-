# Users Stats API - Total Users Count
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, case, literal_column, and_, or_
from typing import Dict, List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.models.async_database import get_async_db
from app.models.fittbot_models import Client, ActiveUser
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.fittbot_admin_api.purchases.purchases import compute_gmv_totals

router = APIRouter(prefix="/api/admin/users-stats", tags=["AdminUsersStats"])


# Pydantic Schemas
class UsersStatsResponse(BaseModel):
    success: bool
    data: Dict
    message: str


class CityStatsItem(BaseModel):
    city: str
    users_count: int


class CityStatsResponse(BaseModel):
    success: bool
    data: List[CityStatsItem]
    next_cursor: Optional[int]
    has_more: bool
    message: str


async def get_total_bookings_count(db: AsyncSession, start_date: Optional[str] = None, end_date: Optional[str] = None):
    try:
        # Get GMV totals with date filters
        gmv_data = await compute_gmv_totals(db, start_date, end_date)
        
        # Sum up counts from all categories
        total = (
            (gmv_data.get("daily_pass", {}).get("count") or 0) +
            (gmv_data.get("session", {}).get("count") or 0) +
            (gmv_data.get("nutrition_plan", {}).get("count") or 0) +
            (gmv_data.get("gym_membership", {}).get("count") or 0) +
            (gmv_data.get("ai_credits", {}).get("count") or 0)
        )
        return int(total)
    except Exception as e:
        return 0


@router.get("/data")
async def get_users_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db)
):

    try:
        # Parse dates
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_date else None

        # Query 1: Count total clients 
        total_filters = []
        if start_date_obj:
            total_filters.append(Client.created_at >= start_date_obj)
        if end_date_obj:
            total_filters.append(Client.created_at <= end_date_obj)

        total_query = select(func.count()).select_from(Client)
        if total_filters:
            total_query = total_query.where(and_(*total_filters))
            
        total_result = await db.execute(total_query)
        total_count = total_result.scalar() or 0

        # Query 2: Count distinct client_id from active_users with optional date filter
        # Active users: users with at least 1 login in the last 30 days (or within date range if provided)
        # Exclude users from gym_id = 1
        thirty_days_ago = datetime.now() - timedelta(days=30)
        active_filters = []
        if start_date_obj:
            active_filters.append(ActiveUser.created_at >= start_date_obj)
        else:
            # Default to last 30 days if no start_date provided
            active_filters.append(ActiveUser.created_at >= thirty_days_ago)
        if end_date_obj:
            active_filters.append(ActiveUser.created_at <= end_date_obj)

        active_subquery = select(ActiveUser.client_id).join(
            Client, ActiveUser.client_id == Client.client_id
        ).where(
            and_(
                *active_filters,
                or_(Client.gym_id != 1, Client.gym_id.is_(None))
            )
        )

        active_query = select(
            func.coalesce(func.count(func.distinct(ActiveUser.client_id)), 0)
        ).where(
            ActiveUser.client_id.in_(active_subquery)
        )
        active_result = await db.execute(active_query)
        active_count = active_result.scalar() or 0

        # Query 3: Count distinct customer_id from payments table (paying users)
        # Exclude payments associated with gym_id = 1 and internal/test contacts
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956"]
        
        paying_filters = [
            Payment.status == "captured",
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != "1",
            or_(Client.contact.is_(None), ~Client.contact.in_(EXCLUDED_CONTACTS))
        ]
        
        if start_date_obj:
            paying_filters.append(Payment.captured_at >= start_date_obj)
        if end_date_obj:
            paying_filters.append(Payment.captured_at <= end_date_obj)

        paying_subquery = select(Payment.customer_id).join(
            Order, Order.id == Payment.order_id
        ).join(
            OrderItem, OrderItem.order_id == Order.id
        ).outerjoin(
            Client, Payment.customer_id == Client.client_id
        ).where(
            and_(*paying_filters)
        ).distinct().alias("paying_users_subquery")

        paying_query = select(func.count()).select_from(paying_subquery)
        paying_result = await db.execute(paying_query)
        paying_count = paying_result.scalar() or 0

        # Query 4: Count customers who appear more than once (repeat users)
        # Exclude payments associated with gym_id = 1
        # Apply date filter if provided (using payment.captured_at or booking date)
        repeat_filters = [
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != "1"
        ]
        if start_date_obj:
            repeat_filters.append(Payment.captured_at >= start_date_obj)
        if end_date_obj:
            repeat_filters.append(Payment.captured_at <= end_date_obj)

        repeat_subquery = select(
            Payment.customer_id
        ).join(
            Order, Order.id == Payment.order_id
        ).join(
            OrderItem, OrderItem.order_id == Order.id
        ).where(
            and_(*repeat_filters)
        ).group_by(
            Payment.customer_id
        ).having(
            func.count(Payment.customer_id) > 1
        ).alias("repeat_users_subquery")

        repeat_query = select(func.count()).select_from(repeat_subquery)
        repeat_result = await db.execute(repeat_query)
        repeat_count = repeat_result.scalar() or 0

        # Query 5: Get users per city with normalization
        # Excluding gym_id = 1
        # Using pure SQLAlchemy ORM - no raw SQL
        # Fetch all locations and filter in Python for better compatibility

        # Get all clients (including gym_1)
        clients_query = select(Client.location)
        clients_result = await db.execute(clients_query)
        locations = [row[0] for row in clients_result.fetchall()]

        # Group by normalized location in Python and filter for valid city names
        # Valid city names must contain at least one letter
        city_counts = {}
        skipped_no_alpha = 0
        sample_skipped = []

        for loc in locations:
            normalized = loc.strip().lower() if loc and loc.strip() else ""
            
            # Use only valid city names (containing at least one letter)
            if normalized and any(c.isalpha() for c in normalized):
                # Title case for display
                display_city = normalized.title()
                city_counts[display_city] = city_counts.get(display_city, 0) + 1
            else:
                skipped_no_alpha += 1
                if loc and len(sample_skipped) < 10:
                    sample_skipped.append(loc)

        # Sort by count desc and take top 30
        city_stats = [
            {"city": city, "users_count": count}
            for city, count in sorted(city_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        ]

        return {
            "success": True,
            "data": {
                "total_users": int(total_count),
                "active_users": int(active_count),
                "paying_users": int(paying_count),
                "repeat_users": int(repeat_count),
                "total_bookings": await get_total_bookings_count(db, start_date, end_date),
                "users_by_city": city_stats,
                "total_cities": len(city_counts)
            },
            "message": "Users stats fetched successfully"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cities")
async def get_cities_paginated(
    offset: int = Query(0, description="Number of cities to skip for pagination", ge=0),
    limit: int = Query(30, description="Number of cities to return per page", ge=1, le=100),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get cities with offset-based pagination.

    Returns cities ordered by users_count descending (excluding gym_id = 1).
    """
    try:
        # Subquery to get grouped and counted cities (including everyone)
        # We use a CASE statement to handle NULL/empty locations as 'Unspecified'
        city_group_subquery = select(
            case(
                (or_(Client.location.is_(None), func.trim(Client.location) == ''), 'Unspecified'),
                else_=func.trim(func.lower(Client.location))
            ).label("normalized_city"),
            func.count(Client.client_id).label("users_count")
        ).group_by(
            case(
                (or_(Client.location.is_(None), func.trim(Client.location) == ''), 'Unspecified'),
                else_=func.trim(func.lower(Client.location))
            )
        ).order_by(
            func.count(Client.client_id).desc()
        ).alias("city_groups")

        # Fetch with offset and limit
        cities_query = select(
            city_group_subquery.c.normalized_city,
            city_group_subquery.c.users_count
        ).offset(offset).limit(limit)

        result = await db.execute(cities_query)
        rows = result.fetchall()

        # Also fetch one more to check if there are more results
        next_check_query = select(func.count()).select_from(city_group_subquery)
        total_cities_result = await db.execute(next_check_query)
        total_available = total_cities_result.scalar() or 0
        
        has_more = (offset + limit) < total_available

        # Process results - format city names (Skipping 'Unspecified')
        city_stats = []
        for row in rows:
            normalized = row[0]
            count = row[1]

            if normalized and normalized != 'Unspecified' and any(c.isalpha() for c in normalized):
                city_stats.append({
                    "city": normalized.title(),
                    "users_count": count
                })

        return {
            "success": True,
            "data": city_stats,
            "next_offset": offset + len(city_stats),
            "has_more": has_more,
            "message": "Cities fetched successfully"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))