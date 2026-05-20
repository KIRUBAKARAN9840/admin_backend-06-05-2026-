"""
Nutrition Consultation Models.

This module contains models for the nutrition consultation feature:
- Nutritionist management
- Schedule/slot management
- Client eligibility tracking
- Booking management
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON

from app.models.database import Base
from app.services.timezone_utils import now_ist

NUTRITION_SCHEMA = "nutrition"


# ═══════════════════════════════════════════════════════════════════════════════
# NUTRITIONIST - Manages nutritionist profiles
# ═══════════════════════════════════════════════════════════════════════════════
class Nutritionist(Base):
    """
    Nutritionists who provide consultation services.
    """
    __tablename__ = "nutritionists"
    __table_args__ = {"schema": NUTRITION_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(100), nullable=False)
    contact = Column(String(15), unique=True, nullable=False)
    email = Column(String(100), nullable=True)
    profile_image = Column(String(255), nullable=True)
    specializations = Column(JSON, nullable=True)
    experience = Column(Float, nullable=True)
    certifications = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# NUTRITION_SCHEDULE - Available time slots for consultations
# ═══════════════════════════════════════════════════════════════════════════════
class NutritionSchedule(Base):
    """
    Available time slots for nutrition consultations.
    Each slot can only be booked by one client per date.
    """
    __tablename__ = "nutrition_schedules"
    __table_args__ = (
        Index("ix_nutrition_schedule_nutritionist_weekday", "nutritionist_id", "weekday"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    nutritionist_id = Column(
        Integer,
        ForeignKey(f"{NUTRITION_SCHEMA}.nutritionists.id", ondelete="CASCADE"),
        nullable=False
    )
    weekday = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    start_date = Column(Date, nullable=True)  # Schedule validity start
    end_date = Column(Date, nullable=True)  # Schedule validity end
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# NUTRITION_ELIGIBILITY - Tracks client eligibility for free sessions
# ═══════════════════════════════════════════════════════════════════════════════
class NutritionEligibility(Base):
    """
    Tracks clients eligible for free nutrition consultation sessions.

    Eligibility is granted based on:
    - Fittbot subscriptions (Diamond/Platinum plans, 6+ months)
    - Gym memberships purchased online (3+ months)
    - Personal training purchased online (3+ months equivalent)

    Session allocation rules:
    - Fittbot Platinum 6M: 1 session | 12M: 2 sessions
    - Fittbot Diamond 6M: 2 sessions | 12M: 3 sessions
    - Gym Membership 3-5M: 1 session | 6-11M: 2 sessions | 12M+: 3 sessions
    """
    __tablename__ = "nutrition_eligibility"
    __table_args__ = (
        Index("ix_nutrition_eligibility_client_source", "client_id", "source_type"),
        Index("ix_nutrition_eligibility_remaining", "client_id", "remaining_sessions"),
        UniqueConstraint("client_id", "source_id", name="uq_nutrition_eligibility_client_source_id"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Note: Removed ForeignKey constraints to allow cross-database usage
    # The payments module uses a different DB session that can't resolve public.clients/gyms
    client_id = Column(
        Integer,
        nullable=False,
        index=True
    )
    gym_id = Column(
        Integer,
        nullable=True
    )  # Null for Fittbot subscriptions

    # Source of eligibility
    source_type = Column(
        Enum(
            "fittbot_subscription",
            "gym_membership",
            "personal_training",
            "fymble_purchase",
            name="nutrition_eligibility_source",
            schema=NUTRITION_SCHEMA
        ),
        nullable=False
    )
    source_id = Column(String(100), nullable=True)  # Reference ID (subscription_id, membership_id, etc.)

    # Plan details
    plan_name = Column(String(100), nullable=True)  # e.g., "Platinum 6M", "Diamond 12M", "Gym 6 months"
    plan_duration_months = Column(Integer, nullable=True)  # Duration in months

    # Session tracking
    total_sessions = Column(Integer, nullable=False, default=1)
    used_sessions = Column(Integer, nullable=False, default=0)
    remaining_sessions = Column(Integer, nullable=False, default=1)

    # Multi-session package support (nutrition_purchase_new flow)
    session_schedule = Column(JSON, nullable=True)  # [{seq, duration_minutes, unlock_after_days}, ...]
    last_booking_date = Column(Date, nullable=True)  # Date of most recent session booking (drives unlock calc)

    # Validity
    granted_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=True)  # Sessions expire if not used

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# NUTRITION_BOOKING - Client bookings for consultation sessions
# ═══════════════════════════════════════════════════════════════════════════════
class NutritionBooking(Base):

    __tablename__ = "nutrition_bookings"
    __table_args__ = (
        Index("ix_nutrition_booking_date_status", "booking_date", "status"),
        Index("ix_nutrition_booking_nutritionist_date", "nutritionist_id", "booking_date"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(
        Integer,
        nullable=False,
        index=True
    )
    eligibility_id = Column(
        Integer,
        nullable=False
    )
    nutritionist_id = Column(
        Integer,
        nullable=False
    )
    schedule_id = Column(
        Integer,
        nullable=True
    )

    # Booking details
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Status tracking
    status = Column(
        Enum(
            "pending",
            "booked",
            "attended",
            "rescheduled",
            "cancelled",
            "no_show",
            name="nutrition_booking_status",
            schema=NUTRITION_SCHEMA
        ),
        default="booked",
        nullable=False
    )

    # Reschedule tracking
    rescheduled_from_id = Column(
        Integer,
        nullable=True
    )
    reschedule_reason = Column(String(255), nullable=True)
    rescheduled_at = Column(DateTime, nullable=True)  # Timestamp when rescheduled
    reschedule_requested_by = Column(
        Enum(
            "client",
            "nutritionist",
            name="nutrition_reschedule_actor",
            schema=NUTRITION_SCHEMA
        ),
        nullable=True
    )

    # Multi-session package support
    session_number = Column(Integer, nullable=True)  # Which session in the package (1-4)
    duration_minutes = Column(Integer, nullable=True)  # Actual booked duration (60 or 30)

    # Session notes
    meeting_link = Column(String(255),nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# ═══════════════════════════════════════════════════════════════════════════════
# COMPLETED_SESSION - Records of completed nutrition consultation sessions
# ═══════════════════════════════════════════════════════════════════════════════
class CompletedSession(Base):
    """
    Records of completed nutrition consultation sessions.
    Stores session details including duration, feedback, and product interest.
    """
    __tablename__ = "completed_sessions"
    __table_args__ = (
        Index("ix_completed_sessions_client", "client_id"),
        Index("ix_completed_sessions_nutritionist", "nutritionist_id"),
        Index("ix_completed_sessions_booking", "booking_id"),
        Index("ix_completed_sessions_schedule", "schedule_id"),
        Index("ix_completed_sessions_date", "slot_date"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    nutritionist_id = Column(Integer, nullable=False)
    booking_id = Column(Integer, nullable=True)
    schedule_id = Column(Integer, nullable=True)

    # Session details
    meeting_duration = Column(Integer, nullable=False)
    feedback_advice = Column(Text, nullable=True)
    interested_in_nutrition_product = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)

    # Slot information
    slot_date = Column(Date, nullable=False)
    slot_time = Column(Time, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# DIET TEMPLATE - Stores reusable diet plan templates
# ═══════════════════════════════════════════════════════════════════════════════
class DietTemplate(Base):
    """
    Diet plan templates created by nutritionists.
    Each template contains multiple days, and each day contains multiple meals/servings.
    """
    __tablename__ = "diet_templates"
    __table_args__ = (
        Index("ix_diet_templates_nutritionist", "nutritionist_id"),
        Index("ix_diet_templates_name", "template_name"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    nutritionist_id = Column(Integer, nullable=False)
    template_name = Column(String(255), nullable=False)
    number_of_days = Column(Integer, nullable=False)

    diet_data = Column(JSON, nullable=False, default=list)

    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT_DIET_TEMPLATE - Stores diet template assignments to clients
# ═══════════════════════════════════════════════════════════════════════════════
class ClientDietTemplate(Base):
    """
    Records of diet templates assigned to clients by nutritionists.
    Tracks which template was assigned, when, and by which nutritionist.
    """
    __tablename__ = "client_diet_templates"
    __table_args__ = (
        Index("ix_client_diet_templates_client", "client_id"),
        Index("ix_client_diet_templates_nutritionist", "nutritionist_id"),
        Index("ix_client_diet_templates_template", "template_id"),
        Index("ix_client_diet_templates_booking", "booking_id"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    nutritionist_id = Column(Integer, nullable=False)
    template_id = Column(Integer, nullable=False)
    template_name = Column(String(1000), nullable=False)
    booking_id = Column(Integer, nullable=False)

    assigned_date = Column(Date, nullable=False)
    step = Column(Integer, nullable=True, default=0)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════════════════════
# NUTRITION_DIET_MEAL_LOG - Tracks which (day, title) meals a client has logged
# from a nutritionist's diet template, so the frontend can disable re-logging.
# ═══════════════════════════════════════════════════════════════════════════════
class NutritionDietMealLog(Base):
    """
    One row per (client_diet_template_id, day_number, meal title) the client
    has logged. Used by /nutrition_diet/get to set is_logged on each meal.
    """
    __tablename__ = "nutrition_diet_meal_logs"
    __table_args__ = (
        UniqueConstraint(
            "client_diet_template_id", "day_number", "title_norm",
            name="uq_nutrition_diet_meal_log",
        ),
        Index(
            "ix_nutrition_diet_meal_log_lookup",
            "client_diet_template_id",
        ),
        Index(
            "ix_nutrition_diet_meal_log_client",
            "client_id",
        ),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_diet_template_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    day_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    title_norm = Column(String(255), nullable=False)

    created_at = Column(DateTime, default=datetime.now)


class AiDietCoach(Base):

    __tablename__ = "ai_diet_coach"
    __table_args__ = (
        Index("ix_ai_diet_coach_client_fingerprint", "client_id", "fingerprint"),
        Index("ix_ai_diet_coach_client_created", "client_id", "created_at"),
        Index("ix_ai_diet_coach_client_step", "client_id", "step"),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False, index=True)
    fingerprint = Column(String(32), nullable=False)
    collected_data = Column(JSON, nullable=False)
    plan = Column(JSON, nullable=False)
    model_used = Column(String(64), nullable=True)
    step = Column(Integer, nullable=False, default=0)
    parent_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: now_ist().replace(tzinfo=None), nullable=False)



class AiDietBooking(Base):

    __tablename__ = "ai_diet_bookings"
    __table_args__ = (
        Index("ix_ai_diet_bookings_client_status", "client_id", "status"),
        Index("ix_ai_diet_bookings_client_expires", "client_id", "expires_at"),
        UniqueConstraint(
            "client_id", "source_id",
            name="uq_ai_diet_bookings_client_source",
        ),
        {"schema": NUTRITION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False, index=True)
    gym_id = Column(Integer, nullable=True)

    # Purchase linkage
    source_type = Column(String(50), nullable=False, default="fymble_purchase")
    source_id = Column(String(100), nullable=False)  # order_id
    entitlement_id = Column(String(100), nullable=True, index=True)
    plan_name = Column(String(100), nullable=True)

    # Validity
    granted_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(
        Enum(
            "active", "expired", "revoked",
            name="ai_diet_booking_status",
            schema=NUTRITION_SCHEMA,
        ),
        default="active", nullable=False, index=True,
    )

    # Usage tracking — incremented each time the AI coach generates a plan
    plans_generated = Column(Integer, default=0, nullable=False)
    last_generated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AnthropometricData(Base):

    __tablename__ = "anthropometric_data"
    __table_args__ = (
        Index("ix_anthropometric_data_client_id", "client_id"),
        Index("ix_anthropometric_data_nutritionist_id", "nutritionist_id"),
        {
            "schema": NUTRITION_SCHEMA
        },
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    nutritionist_id = Column(Integer, nullable=False, index=True)
    client_id = Column(Integer, nullable=False, index=True)

    occupation = Column(String(255), nullable=True)

    anthropometric = Column(JSON, nullable=True)

    biochemical_assessment = Column(Text, nullable=True)
    clinical_assessment = Column(Text, nullable=True)
    dietary_assessment = Column(Text, nullable=True)
    lifestyle_assessment = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: now_ist().replace(tzinfo=None),
        nullable=False
    )

    updated_at = Column(
        DateTime,
        default=lambda: now_ist().replace(tzinfo=None),
        onupdate=lambda: now_ist().replace(tzinfo=None),
        nullable=False
    )

class NutritionConsultationForm(Base):
    __tablename__ = "nutrition_consultation_form"
    __table_args__ = (
        Index("ix_nutrition_consultation_form_client_id", "client_id"),
        {
            "schema": NUTRITION_SCHEMA
        },
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    nutritionist_id = Column(Integer, nullable=False) # ID of nutritionist who last updated
    client_id = Column(Integer, nullable=False, index=True)
    
    # Section 1: Client Information
    full_name = Column(String(255), nullable=True)
    age = Column(String(50), nullable=True)
    gender = Column(String(50), nullable=True)
    occupation = Column(String(255), nullable=True)
    main_health_goal = Column(Text, nullable=True)
    native = Column(String(255), nullable=True)
    current_place = Column(String(255), nullable=True)
    
    # Section 2: Anthropometric Assessment
    anthropometric_table = Column(JSON, nullable=True) # Current/Goal for Weight, Height, BMI, etc.
    recent_changes = Column(JSON, nullable=True) # Weight gain/loss/etc
    fat_distribution = Column(JSON, nullable=True) # Abdomen/Hips/etc
    nutritionist_notes = Column(Text, nullable=True)
    
    # Section 3: Biochemical Assessment
    vitamin_deficiencies = Column(Text, nullable=True)
    biochemical_issues = Column(Text, nullable=True)
    ongoing_medications = Column(Text, nullable=True)
    
    # Section 4: Clinical Assessment
    clinical_concerns = Column(JSON, nullable=True) # Table with Never/Sometimes/Often/Severe
    edema_swelling = Column(Text, nullable=True)
    joint_pain = Column(Text, nullable=True)
    weakness_dizziness = Column(Text, nullable=True)
    other_symptoms = Column(Text, nullable=True)
    
    # Section 5: Dietary Assessment
    meals_daily = Column(String(100), nullable=True)
    skip_breakfast = Column(String(100), nullable=True)
    dinner_timing = Column(String(100), nullable=True)
    late_night_eating = Column(String(100), nullable=True)
    diet_preference = Column(String(100), nullable=True) # Veg/Non-Veg
    water_intake = Column(String(100), nullable=True)
    eat_outside_frequency = Column(String(100), nullable=True)
    food_allergies = Column(Text, nullable=True)
    cooking_time = Column(String(100), nullable=True)
    stay_arrangement = Column(String(100), nullable=True) # Home/PG/Hostel/Alone
    eating_pattern_desc = Column(Text, nullable=True)
    
    # Section 6: Lifestyle Assessment
    daily_routine = Column(JSON, nullable=True) # Work schedule, Wake up, Sleep, etc.
    lifestyle_habits = Column(JSON, nullable=True) # Water, Smoking/Alcohol, etc.
    exercise_routine = Column(Text, nullable=True)
    step_count = Column(String(100), nullable=True)
    activity_level = Column(String(100), nullable=True) # Sedentary/Moderate/Active
    work_mode = Column(String(100), nullable=True) # WFH/Office/Hybrid
    
    # Section 7: Goals & Expectations
    main_goals = Column(Text, nullable=True)
    consistency_challenges = Column(Text, nullable=True)
    expected_support = Column(Text, nullable=True)
    
    created_at = Column(
        DateTime,
        default=lambda: now_ist().replace(tzinfo=None),
        nullable=False
    )

    updated_at = Column(
        DateTime,
        default=lambda: now_ist().replace(tzinfo=None),
        onupdate=lambda: now_ist().replace(tzinfo=None),
        nullable=False
    )