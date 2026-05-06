"""Business logic for Nutrition Diet templates.

Fetches the latest assigned nutrition diet template for a client,
resolves the template name and full diet_data from the diet_templates table.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException
from ..utils import normalize_meal_title
from .repository import NutritionDietRepository
from .schemas import AddStepRequest, GetNutritionDietResponse, MessageResponse, NutritionDietData


class NutritionDietService:

    def __init__(self, db: AsyncSession):
        self.repo = NutritionDietRepository(db)

    async def get_nutrition_diet(self, client_id: int) -> GetNutritionDietResponse:
        client_template = await self.repo.get_latest_client_template(client_id)
        if not client_template:
            return GetNutritionDietResponse(
                data=None,
                message="No nutrition diet assigned",
            )

        diet_template = await self.repo.get_diet_template(client_template.template_id)
        if not diet_template:
            raise FittbotHTTPException(
                status_code=404,
                detail="Assigned diet template not found",
                error_code="NUTRITION_DIET_TEMPLATE_NOT_FOUND",
                log_data={
                    "client_id": client_id,
                    "template_id": client_template.template_id,
                },
            )

        nutritionist_name = await self.repo.get_nutritionist_name(
            client_template.nutritionist_id
        ) or "Unknown"

        # Stamp is_logged on each meal so the frontend can disable re-adding.
        diet_data = diet_template.diet_data or []
        logged_keys = await self.repo.get_logged_meal_keys(client_template.id)
        self._mark_logged_meals(diet_data, logged_keys)

        return GetNutritionDietResponse(
            data=NutritionDietData(
                id=client_template.id,
                nutritionist_name=nutritionist_name,
                step=client_template.step or 0,
                diet_data=diet_data,
            ),
        )

    @staticmethod
    def _mark_logged_meals(diet_data: list, logged_keys: set) -> None:
        """Mutate diet_data in-place: set is_logged on each meal.

        Expects structure:
            [{"day_number": 1, "meals": [{"title": "...", "foods": [...]}, ...]}, ...]
        Match key is (day_number, normalized_title).
        """
        if not diet_data or not isinstance(diet_data, list):
            return

        for day in diet_data:
            if not isinstance(day, dict):
                continue
            day_number = day.get("day_number")
            meals = day.get("meals") or []
            if day_number is None or not isinstance(meals, list):
                continue
            for meal in meals:
                if not isinstance(meal, dict):
                    continue
                title_norm = normalize_meal_title(meal.get("title", ""))
                meal["is_logged"] = (day_number, title_norm) in logged_keys

    async def add_step(self, client_id: int, req: AddStepRequest) -> MessageResponse:
        updated = await self.repo.update_step(client_id, req.id, req.step)
        if not updated:
            raise FittbotHTTPException(
                status_code=404,
                detail="No nutrition diet assigned",
                error_code="NUTRITION_DIET_NOT_FOUND",
                log_data={"client_id": client_id},
            )
        return MessageResponse(message="Step updated successfully")
