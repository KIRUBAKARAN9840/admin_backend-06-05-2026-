"""Pydantic request/response models for Nutrition Diet templates."""

from typing import Any, List, Optional

from pydantic import BaseModel


class NutritionDietData(BaseModel):
    id: int
    nutritionist_name: str
    step: int
    diet_data: List[Any]


class GetNutritionDietResponse(BaseModel):
    status: int = 200
    data: Optional[NutritionDietData] = None
    message: str = "Success"


class AddStepRequest(BaseModel):
    id: int
    step: int


class MessageResponse(BaseModel):
    status: int = 200
    message: str
