from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    BTN_CLIENTS,
    BTN_CREATE_CLIENT,
    BTN_PLANS,
    BTN_SUBSCRIPTIONS,
    admin_main_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, Plan, Subscription

router = Router(name="admin")
router.message.filter(AdminFilter())


@router.message(CommandStart())
async def admin_start(message: Message) -> None:
    await message.answer(
        "Привет, админ платформы! 👋\nВыбери раздел:",
        reply_markup=admin_main_menu(),
    )


@router.message(F.text == BTN_CREATE_CLIENT)
async def create_client_stub(message: Message) -> None:
    await message.answer("Скоро добавим создание клиента")


@router.message(F.text == BTN_CLIENTS)
async def list_clients(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Client).order_by(Client.id))
        clients = result.scalars().all()

    if not clients:
        await message.answer("📋 Клиентов пока нет.")
        return

    lines = ["📋 <b>Клиенты:</b>"]
    for c in clients:
        lines.append(
            f"#{c.id} • <b>{c.business_name}</b> "
            f"(<code>{c.slug}</code>) — {c.status}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text == BTN_PLANS)
async def list_plans(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Plan).order_by(Plan.id))
        plans = result.scalars().all()

    if not plans:
        await message.answer("💳 Тарифов пока нет.")
        return

    lines = ["💳 <b>Тарифы:</b>"]
    for p in plans:
        buyout = "да" if p.can_buyout else "нет"
        months = f", {p.buyout_months} мес." if p.can_buyout and p.buyout_months else ""
        lines.append(
            f"#{p.id} • <b>{p.name}</b> — {p.price_monthly}/мес "
            f"(выкуп: {buyout}{months})"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


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
