from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client


class AdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id in settings.admin_ids)


class ClientFilter(BaseFilter):
    """Passes for:
    - Regular clients whose Telegram ID matches Client.admin_telegram_id.
    - Platform admins (ADMIN_IDS) who have entered client test mode
      (i.e. ``selected_client_id`` is present in their FSM state).
    """

    async def __call__(self, event: Message | CallbackQuery, state: FSMContext) -> bool:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return False
        if user_id in settings.admin_ids:
            # Platform admin passes only when actively testing a client
            data = await state.get_data()
            return "selected_client_id" in data
        async with AsyncSessionLocal() as session:
            exists = await session.scalar(
                select(Client.id).where(Client.admin_telegram_id == user_id)
            )
        return exists is not None
