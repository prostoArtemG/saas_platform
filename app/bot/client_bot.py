"""Factory for personal client bots (webhook mode).

Each client with bot_mode="personal" gets its own Bot + Dispatcher running
inside the same process.  The Dispatcher reuses the existing client_cms_router
so all CMS features (products, orders, settings, filters) work identically.

A thin /start handler is registered first so the client can connect their
Telegram account to the shop via the personal bot.
"""
from __future__ import annotations

import logging

from aiogram import Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from app.bot.keyboards import client_main_menu
from app.bot.middlewares import MenuInterruptMiddleware
from app.db import AsyncSessionLocal
from app.models import Client

logger = logging.getLogger(__name__)


def _build_start_router(slug: str) -> Router:
    """Return a Router that handles /start for a specific client slug."""
    router = Router(name=f"personal_start_{slug}")

    @router.message(CommandStart())
    async def _start(message: Message, state: FSMContext) -> None:  # noqa: ARG001
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return

        async with AsyncSessionLocal() as session:
            client = await session.scalar(
                select(Client).where(Client.slug == slug)
            )
            if client is None:
                await message.answer("❌ Магазин не знайдено.")
                return

            if client.admin_telegram_id is not None and client.admin_telegram_id != user_id:
                await message.answer(
                    "⛔ Цей магазин вже прив'язаний до іншого Telegram-акаунту.\n"
                    "Зверніться до підтримки, якщо це помилка."
                )
                return

            if client.admin_telegram_id == user_id:
                await message.answer(
                    f"👋 З поверненням, <b>{client.business_name}</b>!\n"
                    "Telegram CMS вже підключено.",
                    parse_mode="HTML",
                    reply_markup=client_main_menu(),
                )
                return

            # Link this Telegram user as the shop admin
            client.admin_telegram_id = user_id
            await session.commit()

        await message.answer(
            f"✅ <b>Telegram CMS підключено!</b>\n\n"
            f"🏪 Магазин: <b>{client.business_name}</b>\n\n"
            "Тепер ти можеш управляти товарами, замовленнями та налаштуваннями "
            "прямо з Telegram.",
            parse_mode="HTML",
            reply_markup=client_main_menu(),
        )

    return router


def create_client_dispatcher(slug: str) -> Dispatcher:
    """Build an isolated Dispatcher for a personal client bot.

    Only the /start handler is included here.  The full CMS router
    (client_cms_router) is intentionally excluded: it is a module-level
    singleton in aiogram and can only be attached to ONE Dispatcher.
    Including it here would raise "Router is already attached" for every
    second personal bot started in the same process.

    Clients using saas_platform webhook mode get CMS access through the
    platform bot (which includes client_cms_router once in its Dispatcher).
    Clients deployed to Railway have their own process and their own CMS.
    """
    dp = Dispatcher()
    dp.message.outer_middleware(MenuInterruptMiddleware())
    # /start must come first — it handles account linking for this slug
    dp.include_router(_build_start_router(slug))
    return dp
