import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.config import settings

logger = logging.getLogger(__name__)

router = Router(name="root")


@router.message(CommandStart())
async def start(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id in settings.admin_ids:
        await message.answer(
            "Привет, админ платформы! 👋\n"
            "SaaS-платформа запущена. Каркас MVP готов."
        )
    else:
        await message.answer("Доступ ограничен.")


def create_bot() -> Bot:
    return Bot(token=settings.bot_token)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp
