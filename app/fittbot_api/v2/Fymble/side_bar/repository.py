"""Database queries for Sidebar."""

from typing import Optional, Tuple

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client
from app.fittbot_api.v1.payments.models.credits import CreditBalance


class SidebarRepository:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def fetch_client_info(self, client_id: int) -> Tuple[Optional[str], Optional[str]]:
        """Return (name, contact) for a client."""
        stmt = select(Client.name, Client.contact).where(
            Client.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None, None
        return row[0], row[1]

    async def fetch_credit_balance(self, client_id: int) -> int:
        """Return current credit balance."""
        stmt = select(CreditBalance.balance).where(
            CreditBalance.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.scalar()
        return row if row is not None else 0
