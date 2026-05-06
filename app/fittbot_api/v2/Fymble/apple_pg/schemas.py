"""
Schemas for the Apple Payment Gateway status endpoint.

Surfaces only what the user asked for: AI credits balance and a single
boolean for whether the user has purchased the nutrition package.
"""

from pydantic import BaseModel


class NutritionCreditStatusResponse(BaseModel):
    status: int = 200
    credits_balance: int = 0
    nutrition_purchased: bool = False
