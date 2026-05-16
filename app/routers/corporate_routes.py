from fastapi import APIRouter
from app.corporate_dashboard_api.auth.authentication import router as auth_router

router = APIRouter()

# Include sub-routers for corporate dashboard
router.include_router(auth_router)
