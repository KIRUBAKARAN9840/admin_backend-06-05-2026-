from sqlalchemy import Column, Integer, String, DateTime
from app.models.database import Base
import datetime
import enum

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    CONSULTANT = "consultant"

class CorporateUser(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "corparate_dashboard"}

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    mobile_number = Column(String(15), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default=UserRole.CONSULTANT)
    refresh_token = Column(String(500), nullable=True)
    
    # OTP fields for password reset (Matching Admin standard)
    otp = Column(String(6), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
