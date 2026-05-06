from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, text, func, cast, Date as SQLDate
from typing import Optional, Dict, List, Any
from datetime import datetime, date, timedelta
from pydantic import BaseModel
import json

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.fittbot_models import Client, ActualDiet, ActualWorkout, ClientActual
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie

router = APIRouter(prefix="/api/admin/nutritionist_sessions", tags=["NutritionistClientDetails"])


# Pydantic models for pagination response
class PaginatedResponse(BaseModel):
    """Standard paginated response structure"""
    success: bool
    data: Dict[str, Any]


class PaginationMeta(BaseModel):
    """Pagination metadata"""
    total_records: int
    total_pages: int
    current_page: int
    page_size: int
    has_next: bool
    has_previous: bool


@router.get("/client/{client_id}")
async def get_client_details(
    client_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """Get detailed information about a client"""
    try:
        # Fetch client details
        query = select(Client).where(Client.client_id == client_id)
        result = await db.execute(query)
        client = result.scalar_one_or_none()

        if not client:
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        return {
            "success": True,
            "data": {
                "client_id": client.client_id,
                "name": client.name,
                "email": client.email,
                "contact": client.contact,
                "profile": client.profile,
                "location": client.location,
                "age": client.age,
                "gender": client.gender,
                "height": client.height,
                "weight": client.weight,
                "bmi": client.bmi,
                "goals": client.goals,
                "lifestyle": client.lifestyle,
                "medical_issues": client.medical_issues,
                "joined_date": client.joined_date.isoformat() if client.joined_date else None,
                "dob": client.dob.isoformat() if client.dob else None,
                "status": client.status
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching client details: {str(e)}"
        )


def _parse_diet_data(diet_data: Any) -> List[Dict]:
    """
    Pure function to parse diet_data JSON safely.
    No I/O operations, just data transformation.
    """
    if isinstance(diet_data, str):
        try:
            return json.loads(diet_data)
        except json.JSONDecodeError:
            return []
    elif isinstance(diet_data, list):
        return diet_data
    return []


def _parse_workout_data(workout_details: Any) -> List[Dict]:
    """
    Pure function to parse workout_details JSON safely.
    No I/O operations, just data transformation.
    """
    if isinstance(workout_details, str):
        try:
            return json.loads(workout_details)
        except json.JSONDecodeError:
            return []
    elif isinstance(workout_details, list):
        return workout_details
    return []


@router.get("/client/{client_id}/food-logs")
async def get_client_food_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated food logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    - JSON parsing done in-memory (pure functions)
    """
    try:
        # Build base conditions for filtering
        conditions = [ActualDiet.client_id == client_id]

        if start_date:
            conditions.append(ActualDiet.date >= start_date)
        if end_date:
            conditions.append(ActualDiet.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ActualDiet.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "food_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ActualDiet.record_id,
            ActualDiet.client_id,
            ActualDiet.date,
            ActualDiet.diet_data
        ).where(
            and_(*conditions)
        ).order_by(
            ActualDiet.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Parse JSON data in-memory (pure functions, no DB calls)
        food_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "diet_data": _parse_diet_data(row.diet_data)
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "food_logs": food_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching food logs: {str(e)}"
        )


@router.get("/client/{client_id}/workout-logs")
async def get_client_workout_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated workout logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    - JSON parsing done in-memory (pure functions)
    """
    try:
        # Build base conditions for filtering
        conditions = [ActualWorkout.client_id == client_id]

        if start_date:
            conditions.append(ActualWorkout.date >= start_date)
        if end_date:
            conditions.append(ActualWorkout.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ActualWorkout.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "workout_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ActualWorkout.record_id,
            ActualWorkout.client_id,
            ActualWorkout.date,
            ActualWorkout.workout_details
        ).where(
            and_(*conditions)
        ).order_by(
            ActualWorkout.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Parse JSON data in-memory (pure functions, no DB calls)
        workout_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "workout_details": _parse_workout_data(row.workout_details)
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "workout_logs": workout_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching workout logs: {str(e)}"
        )


@router.get("/client/{client_id}/water-logs")
async def get_client_water_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated water intake logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    """
    try:
        # Build base conditions for filtering
        conditions = [
            ClientActual.client_id == client_id,
            ClientActual.water_intake.isnot(None),
            ClientActual.water_intake > 0
        ]

        if start_date:
            conditions.append(ClientActual.date >= start_date)
        if end_date:
            conditions.append(ClientActual.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ClientActual.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "water_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ClientActual.record_id,
            ClientActual.client_id,
            ClientActual.date,
            ClientActual.water_intake
        ).where(
            and_(*conditions)
        ).order_by(
            ClientActual.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Format response (pure transformation, no DB calls)
        water_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "water_intake": row.water_intake
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "water_logs": water_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching water logs: {str(e)}"
        )
