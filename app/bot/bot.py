import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.admin import router as admin_router
from app.bot.clients_admin import router as clients_admin_router
from app.bot.create_client import router as create_client_router
from app.bot.create_plan import router as create_plan_router
from app.bot.payments import router as payments_router
from app.bot.products import router as products_router
from app.bot.site_request import router as site_request_router
from app.config import settings

logger = logging.getLogger(__name__)

router = Router(name="root")


@router.message(CommandStart())
async def start_non_admin(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id in settings.admin_ids:
        # Handled by admin_router; this branch is a safety net.
        return
    await message.answer("Доступ ограничен.")


def create_bot() -> Bot:
    return Bot(token=settings.bot_token)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(create_plan_router)
    dp.include_router(create_client_router)
    dp.include_router(products_router)
    dp.include_router(site_request_router)
    dp.include_router(payments_router)
    dp.include_router(clients_admin_router)
    dp.include_router(admin_router)
    dp.include_router(router)
    return dp
