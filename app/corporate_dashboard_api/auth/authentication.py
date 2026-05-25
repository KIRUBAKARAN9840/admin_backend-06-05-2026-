from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.models.database import get_db
from app.models.corporate_models import CorporateUser, UserRole
from app.utils.security import (
    verify_password, create_access_token, create_refresh_token,
    SECRET_KEY, ALGORITHM, get_password_hash
)
from app.utils.otp import generate_otp, send_password_reset_sms
from app.config.settings import settings
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timedelta
import logging

router = APIRouter(prefix="/api/corporate/auth", tags=["CorporateAuthentication"])
logger = logging.getLogger("corporate_auth")

class LoginRequest(BaseModel):
    mobile_number: str
    password: str
    role: Optional[str] = None

class SendOTPRequest(BaseModel):
    mobile_number: str

class VerifyOTPRequest(BaseModel):
    mobile_number: str
    otp: str

class ChangePasswordRequest(BaseModel):
    mobile_number: str
    new_password: str

@router.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Unified login for Corporate Admin and Consultant using mobile number"""
    query = db.query(CorporateUser).filter(CorporateUser.mobile_number == request.mobile_number)
    if request.role:
        user = query.filter(CorporateUser.role == request.role).first()
    else:
        user = query.first()

    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_password(request.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Create tokens
    access_token = create_access_token({
        "sub": str(user.id), 
        "role": user.role, 
        "user_type": "corporate"
    })
    refresh_token = create_refresh_token({
        "sub": str(user.id), 
        "user_type": "corporate"
    })
    
    # Save refresh token in DB
    user.refresh_token = refresh_token
    db.commit()
    
    response_data = {
        "status": 200,
        "message": "Login successful",
        "data": {
            "user_id": user.id,
            "name": user.name,
            "role": user.role,
            "mobile_number": user.mobile_number,
            "user_type": "corporate"
        },
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }
    
    response = JSONResponse(content=response_data)
    
    cookie_params = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "domain": settings.cookie_domain_value,
        "samesite": settings.cookie_samesite_value
    }
    
    response.set_cookie(key="access_token", value=access_token, max_age=3600, **cookie_params)
    response.set_cookie(key="refresh_token", value=refresh_token, max_age=604800, **cookie_params)
    
    return response

@router.get("/verify")
async def verify_token(
    request: Request,
    device: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Verify authentication token (Fittbot Standard)"""
    access_token = request.cookies.get("access_token")
    
    if not access_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            access_token = auth_header.split(" ")[1]
        else:
            raise HTTPException(status_code=401, detail="No authentication method found")

    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user_type = payload.get("user_type")

        if user_type != "corporate":
            raise HTTPException(status_code=403, detail="Corporate access required")

        user = db.query(CorporateUser).filter(CorporateUser.id == int(user_id)).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        return {
            "status": 200,
            "message": "valid token",
            "data": {
                "user_id": user.id,
                "name": user.name,
                "role": user.role,
                "mobile_number": user.mobile_number,
                "user_type": "corporate"
            }
        }
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, Please Login again")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

@router.post("/refresh-cookie")
async def refresh_cookie(request: Request, db: Session = Depends(get_db)):
    """Refresh access token using httpOnly refresh cookie (Fittbot Standard)"""
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token found")

    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user_type = payload.get("user_type")

        if user_type != "corporate":
            raise HTTPException(status_code=401, detail="Invalid user type")

        user = db.query(CorporateUser).filter(CorporateUser.id == int(user_id)).first()
        if not user or user.refresh_token != refresh_token:
            raise HTTPException(status_code=401, detail="Refresh token not recognized")

        new_access_token = create_access_token({
            "sub": str(user.id), 
            "role": user.role, 
            "user_type": "corporate"
        })

        response = JSONResponse(content={"status": 200, "message": "Token refreshed successfully"})
        
        response.set_cookie(
            key="access_token",
            value=new_access_token,
            max_age=3600,
            httponly=True,
            secure=settings.cookie_secure,
            domain=settings.cookie_domain_value,
            samesite=settings.cookie_samesite_value,
        )

        return response

    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@router.get("/profile")
async def get_profile(request: Request, db: Session = Depends(get_db)):
    """Get current user profile (Fittbot Standard)"""
    return await verify_token(request, db=db)

@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Logout flow (Fittbot Standard)"""
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                user = db.query(CorporateUser).filter(CorporateUser.id == int(user_id)).first()
                if user:
                    user.refresh_token = None
                    db.commit()
        except:
            pass

    response = JSONResponse(content={"status": 200, "message": "Logged out successfully"})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response

@router.post("/send_otp")
async def send_otp(request: SendOTPRequest, db: Session = Depends(get_db)):
    user = db.query(CorporateUser).filter(CorporateUser.mobile_number == request.mobile_number).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    otp = generate_otp()
    user.otp = otp
    user.expires_at = datetime.now() + timedelta(minutes=5)
    db.commit()
    
    # Send SMS
    sms_sent = send_password_reset_sms(user.mobile_number, otp)
    if not sms_sent:
        logger.warning(f"Failed to send OTP SMS to {user.mobile_number}, returning debug OTP")

    return {"status": 200, "message": "OTP sent successfully", "otp_debug": otp}

@router.post("/verify_otp")
async def verify_otp(request: VerifyOTPRequest, db: Session = Depends(get_db)):
    user = db.query(CorporateUser).filter(CorporateUser.mobile_number == request.mobile_number).first()
    if not user or user.otp != request.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    
    if not user.expires_at or datetime.now() > user.expires_at:
        raise HTTPException(status_code=400, detail="OTP expired")
    
    return {"status": 200, "message": "OTP verified successfully"}

@router.post("/change_password")
async def change_password(request: ChangePasswordRequest, db: Session = Depends(get_db)):
    user = db.query(CorporateUser).filter(CorporateUser.mobile_number == request.mobile_number).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password = get_password_hash(request.new_password)
    user.otp = None
    user.expires_at = None
    db.commit()
    return {"status": 200, "message": "Password changed successfully"}
