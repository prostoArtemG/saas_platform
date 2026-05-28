from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client


class AdminFilter(BaseFilter):
    """Passes ONLY for platform administrators (user_id in ADMIN_IDS)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id in settings.admin_ids)


class ClientFilter(BaseFilter):
    """Passes ONLY for registered shop owners who are NOT platform admins.

    Requirements:
    - Client.admin_telegram_id == user_id
    - user_id NOT in ADMIN_IDS

    Platform admins are NEVER passed by this filter.
    Admins use AdminFilter (admin routers) or CMSFilter (test mode).
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return False
        if user_id in settings.admin_ids:
            return False  # Admins are handled by AdminFilter / CMSFilter
        async with AsyncSessionLocal() as session:
            exists = await session.scalar(
                select(Client.id).where(Client.admin_telegram_id == user_id)
            )
        return exists is not None


class AdminTestModeFilter(BaseFilter):
    """Passes ONLY for platform admins (ADMIN_IDS) who have entered client test mode.

    Requirements:
    - user_id in ADMIN_IDS
    - ``selected_client_id`` present in FSM state

    ``selected_client_id`` can only be set by admin-guarded handlers in
    products.py and admin.py — regular clients can never obtain it.
    """

    async def __call__(
        self, event: Message | CallbackQuery, state: FSMContext
    ) -> bool:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return False
        if user_id not in settings.admin_ids:
            return False
        data = await state.get_data()
        return "selected_client_id" in data


class CMSFilter(BaseFilter):
    """Grants access to the Client CMS router.

    Passes when EITHER:
    - user is a registered client (NOT platform admin) — ClientFilter logic, OR
    - user is a platform admin actively testing a client — AdminTestModeFilter logic

    Security guarantees:
    - Platform admins NOT in test mode → always DENIED (must use admin routers)
    - Regular clients → always see ONLY their own data (_get_effective_client enforces this)
    - ``selected_client_id`` → honoured only for admin_ids inside _get_effective_client
    - Only admin-guarded handlers (products.py, admin.py) can set ``selected_client_id``
    """

    async def __call__(
        self, event: Message | CallbackQuery, state: FSMContext
    ) -> bool:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return False
        if user_id in settings.admin_ids:
            # Admin: only allowed when actively in test mode
            data = await state.get_data()
            return "selected_client_id" in data
        # Regular user: must have a registered Client record
        async with AsyncSessionLocal() as session:
            exists = await session.scalar(
                select(Client.id).where(Client.admin_telegram_id == user_id)
            )
        return exists is not None
