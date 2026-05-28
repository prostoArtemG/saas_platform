from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    BTN_EXIT_TEST,
    BTN_SUBSCRIPTIONS,
    admin_main_menu,
    client_test_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, Subscription

router = Router(name="admin")
router.message.filter(AdminFilter())


@router.message(CommandStart())
async def admin_start(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if arg.startswith("client_"):
        slug = arg[len("client_"):]
        async with AsyncSessionLocal() as session:
            client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            await message.answer(
                f"❌ Клієнт з slug <code>{slug}</code> не знайдений.",
                parse_mode="HTML",
            )
            return
        await state.update_data(
            selected_client_id=client.id,
            selected_client_slug=client.slug,
        )
        await message.answer(
            f"🔧 <b>Тест-режим</b>: {client.business_name}\n"
            f"Slug: <code>{client.slug}</code>\n\n"
            f"Натисни ⬅️ Выйти из тест-режима щоб повернутися до панелі адміна.",
            parse_mode="HTML",
            reply_markup=client_test_menu(),
        )
        return

    # Normal admin /start — clear any active test mode
    await state.clear()
    await message.answer(
        "Привет, админ платформы! 👋\nВыбери раздел:",
        reply_markup=admin_main_menu(),
    )


@router.message(F.text == BTN_EXIT_TEST)
async def exit_test_mode(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Вышел из тест-режима. Панель администратора:",
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
