from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    BTN_SUBSCRIPTIONS,
    admin_main_menu,
)
from app.db import AsyncSessionLocal
from app.models import Subscription

router = Router(name="admin")
router.message.filter(AdminFilter())


@router.message(CommandStart())
async def admin_start(message: Message) -> None:
    await message.answer(
        "Привет, админ платформы! 👋\nВыбери раздел:",
        reply_markup=admin_main_menu(),
    )


@router.message(F.text == BTN_SUBSCRIPTIONS)
async def list_subscriptions(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Subscription).order_by(Subscription.id)
        )
        subs = result.scalars().all()

    if not subs:
        await message.answer("🧾 Подписок пока нет.")
        return

    lines = ["🧾 <b>Подписки:</b>"]
    for s in subs:
        expires = s.expires_at.strftime("%Y-%m-%d") if s.expires_at else "—"
        lines.append(
            f"#{s.id} • client={s.client_id} • plan={s.plan_id} • "
            f"{s.status} • до {expires}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
