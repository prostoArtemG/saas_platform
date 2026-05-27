from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client


class AdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id in settings.admin_ids)


class ClientFilter(BaseFilter):
    """Passes only for clients that have admin_telegram_id set to the caller's
    user_id and are NOT platform admins (ADMIN_IDS)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return False
        if user_id in settings.admin_ids:
            return False
        async with AsyncSessionLocal() as session:
            exists = await session.scalar(
                select(Client.id).where(Client.admin_telegram_id == user_id)
            )
        return exists is not None
