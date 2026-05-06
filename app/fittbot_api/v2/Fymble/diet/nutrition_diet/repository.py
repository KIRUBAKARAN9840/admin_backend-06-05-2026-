"""Database queries for Nutrition Diet templates.

Fetches the latest assigned diet template for a client from the nutrition schema.
"""

from typing import Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nutrition_models import (
    ClientDietTemplate,
    DietTemplate,
    NutritionDietMealLog,
    Nutritionist,
)


class NutritionDietRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_latest_client_template(self, client_id: int) -> Optional[ClientDietTemplate]:

        stmt = (
            select(ClientDietTemplate)
            .where(ClientDietTemplate.client_id == client_id)
            .order_by(ClientDietTemplate.id.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_diet_template(self, template_id: int) -> Optional[DietTemplate]:
        """Get the full diet template by ID."""
        stmt = select(DietTemplate).where(DietTemplate.id == template_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_nutritionist_name(self, nutritionist_id: int) -> Optional[str]:
        """Get nutritionist full_name by ID."""
        stmt = select(Nutritionist.full_name).where(Nutritionist.id == nutritionist_id)
        result = await self.db.execute(stmt)
        return result.scalar()

    async def get_logged_meal_keys(
        self, client_template_id: int
    ) -> Set[Tuple[int, str]]:
        """Return set of (day_number, title_norm) already logged for this template."""
        stmt = select(
            NutritionDietMealLog.day_number,
            NutritionDietMealLog.title_norm,
        ).where(
            NutritionDietMealLog.client_diet_template_id == client_template_id
        )
        result = await self.db.execute(stmt)
        return {(row[0], row[1]) for row in result.all()}

    async def update_step(self, client_id: int, row_id: int, step: int) -> bool:
        """Update step on a specific client diet template row."""
        stmt = (
            select(ClientDietTemplate)
            .where(
                ClientDietTemplate.id == row_id,
                ClientDietTemplate.client_id == client_id,
            )
        )
        result = await self.db.execute(stmt)
        template = result.scalars().first()
        if not template:
            return False
        template.step = step
        await self.db.commit()
        return True

