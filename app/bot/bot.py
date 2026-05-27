import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.admin import router as admin_router
from app.bot.client_cms import router as client_cms_router
from app.bot.clients_admin import router as clients_admin_router
from app.bot.create_client import router as create_client_router
from app.bot.keyboards import admin_main_menu
from app.bot.middlewares import MenuInterruptMiddleware
from app.bot.payments import router as payments_router
from app.bot.plans_admin import router as plans_admin_router
from app.bot.products import router as products_router
from app.bot.site_request import router as site_request_router
from app.config import settings

logger = logging.getLogger(__name__)

router = Router(name="root")


@router.message(Command("cancel"))
async def global_cancel(message: Message, state: FSMContext) -> None:
    """Fallback /cancel for users not in any FSM state."""
    await message.answer("Ок, отменено.")


@router.message(CommandStart())
async def start_unknown(message: Message) -> None:
    """Catch-all /start for users who are neither platform admins nor clients."""
    user_id = message.from_user.id if message.from_user else None
    if user_id and user_id in settings.admin_ids:
        # Safety net — should be handled by admin_router first.
        return
    await message.answer("⛔ Доступ ограничен.")


# --------------------------------------------------------------------------
# DEBUG fallback. MUST be the very last handler in the very last router.
# Logs the exact text/bytes of any message that no other handler picked up,
# and echoes it back so we can see what reply-keyboard buttons actually send.
# --------------------------------------------------------------------------
@router.message()
async def debug_any_message(message: Message) -> None:
    text = message.text
    if text is not None:
        as_bytes = text.encode("utf-8")
        codepoints = [hex(ord(c)) for c in text]
        logger.warning(
            "DEBUG fallback: text=%r len=%d utf8=%r codepoints=%s",
            text,
            len(text),
            as_bytes,
            codepoints,
        )
        print(
            f"DEBUG TEXT: {text!r} | utf8={as_bytes!r} | codepoints={codepoints}",
            flush=True,
        )
        await message.answer(
            f"DEBUG (no handler matched):\n"
            f"text = {text!r}\n"
            f"len = {len(text)}\n"
            f"codepoints = {codepoints}"
        )
    else:
        logger.warning(
            "DEBUG fallback: non-text message content_type=%s",
            message.content_type,
        )
        await message.answer(f"DEBUG: non-text message ({message.content_type})")


def create_bot() -> Bot:
    return Bot(token=settings.bot_token)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.outer_middleware(MenuInterruptMiddleware())
    # Platform admin routers (AdminFilter guards each one)
    dp.include_router(plans_admin_router)
    dp.include_router(create_client_router)
    dp.include_router(products_router)
    dp.include_router(site_request_router)
    dp.include_router(payments_router)
    dp.include_router(clients_admin_router)
    dp.include_router(admin_router)
    # Client CMS router (ClientFilter guards it; must come before root router)
    dp.include_router(client_cms_router)
    # Root router: fallback handlers (no role filter)
    dp.include_router(router)
    return dp
