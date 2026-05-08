"""Admin client management: card view + actions (block/activate/delete/payments/domain)."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import BTN_CLIENTS
from app.db import AsyncSessionLocal
from app.models import Client, Domain, Payment, Plan, Subscription

logger = logging.getLogger(__name__)

router = Router(name="clients_admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# ----- helpers ----------------------------------------------------------------

def _list_kb(clients: list[Client]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for c in clients:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏢 {c.business_name} ({c.slug})",
                    callback_data=f"cli:open:{c.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Платежи", callback_data=f"cli:payments:{client_id}"),
                InlineKeyboardButton(text="🌐 Домен", callback_data=f"cli:domain:{client_id}"),
            ],
            [
                InlineKeyboardButton(text="🔒 Заблокировать", callback_data=f"cli:block:{client_id}"),
                InlineKeyboardButton(text="✅ Активировать", callback_data=f"cli:activate:{client_id}"),
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"cli:delete:{client_id}")],
            [InlineKeyboardButton(text="« К списку", callback_data="cli:list")],
        ]
    )


def _back_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к карточке", callback_data=f"cli:open:{client_id}")]
        ]
    )


def _confirm_delete_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"cli:del_confirm:{client_id}"),
                InlineKeyboardButton(text="« Отмена", callback_data=f"cli:open:{client_id}"),
            ]
        ]
    )


def _fmt_dt(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def _build_card_text(session, client: Client) -> str:
    # Latest subscription (with plan)
    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    plan_name = "—"
    sub_status = "—"
    expires = "—"
    if sub is not None:
        sub_status = sub.status
        expires = _fmt_dt(sub.expires_at)
        if sub.plan_id is not None:
            plan = await session.get(Plan, sub.plan_id)
            if plan is not None:
                plan_name = plan.name

    # Latest domain
    domain = (
        await session.execute(
            select(Domain)
            .where(Domain.client_id == client.id)
            .order_by(Domain.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    domain_str = domain.domain if domain else "—"

    # Last payment
    last_payment = (
        await session.execute(
            select(Payment)
            .where(Payment.client_id == client.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_payment is None:
        last_payment_str = "—"
    else:
        last_payment_str = (
            f"{last_payment.amount} {last_payment.currency} "
            f"({last_payment.status}) • {_fmt_dt(last_payment.created_at)}"
        )

    lines = [
        f"🏢 <b>{client.business_name}</b>",
        f"🌐 <code>{client.slug}</code> • {domain_str}",
        f"📦 Тариф: {plan_name}",
        f"✅ Статус: <b>{client.status}</b> • подписка: {sub_status}",
        f"📅 Истекает: {expires}",
        f"💳 Последний платёж: {last_payment_str}",
    ]
    return "\n".join(lines)


async def _send_card(message_target, client_id: int, *, edit: bool = False) -> bool:
    """Render and send/edit the client card. Returns False if client not found."""
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            return False
        text = await _build_card_text(session, client)

    kb = _card_kb(client_id)
    if edit and isinstance(message_target, Message):
        await message_target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_target.answer(text, parse_mode="HTML", reply_markup=kb)
    return True


# ----- list -------------------------------------------------------------------

@router.message(F.text == BTN_CLIENTS)
async def list_clients(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Client).order_by(Client.id))
        clients = result.scalars().all()

    if not clients:
        await message.answer("📋 Клиентов пока нет.")
        return

    await message.answer(
        f"📋 <b>Клиенты ({len(clients)}):</b>\nВыберите клиента:",
        parse_mode="HTML",
        reply_markup=_list_kb(clients),
    )


@router.callback_query(F.data == "cli:list")
async def cb_list(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Client).order_by(Client.id))
        clients = result.scalars().all()

    if not clients:
        await call.message.edit_text("📋 Клиентов пока нет.")
        await call.answer()
        return

    await call.message.edit_text(
        f"📋 <b>Клиенты ({len(clients)}):</b>\nВыберите клиента:",
        parse_mode="HTML",
        reply_markup=_list_kb(clients),
    )
    await call.answer()


# ----- open card --------------------------------------------------------------

@router.callback_query(F.data.startswith("cli:open:"))
async def cb_open(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    ok = await _send_card(call.message, client_id, edit=True)
    if not ok:
        await call.answer("Клиент не найден", show_alert=True)
        return
    await call.answer()


# ----- payments ---------------------------------------------------------------

@router.callback_query(F.data.startswith("cli:payments:"))
async def cb_payments(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await call.answer("Клиент не найден", show_alert=True)
            return
        rows = (
            await session.execute(
                select(Payment)
                .where(Payment.client_id == client_id)
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .limit(10)
            )
        ).scalars().all()

    header = f"💳 <b>Платежи: {client.business_name}</b>"
    if not rows:
        text = header + "\n\nПока нет платежей."
    else:
        lines = [header, ""]
        for p in rows:
            paid = _fmt_dt(p.paid_at) if p.paid_at else "—"
            lines.append(
                f"#{p.id} • {p.payment_type} • <b>{p.amount} {p.currency}</b> "
                f"• {p.status}\n   создан: {_fmt_dt(p.created_at)} • оплачен: {paid}"
            )
        text = "\n".join(lines)

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb(client_id))
    await call.answer()


# ----- domain -----------------------------------------------------------------

@router.callback_query(F.data.startswith("cli:domain:"))
async def cb_domain(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await call.answer("Клиент не найден", show_alert=True)
            return
        domain = (
            await session.execute(
                select(Domain)
                .where(Domain.client_id == client_id)
                .order_by(Domain.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if domain is None:
        text = (
            f"🌐 <b>Домен: {client.business_name}</b>\n\n"
            f"Домен не подключен."
        )
    else:
        dns = "✅ подключен" if domain.dns_connected else "❌ не подключен"
        text = (
            f"🌐 <b>Домен: {client.business_name}</b>\n\n"
            f"Домен: <code>{domain.domain}</code>\n"
            f"Статус: <b>{domain.status}</b>\n"
            f"DNS: {dns}\n"
            f"Истекает: {_fmt_dt(domain.expires_at)}\n"
            f"Создан: {_fmt_dt(domain.created_at)}"
        )

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb(client_id))
    await call.answer()


# ----- block / activate -------------------------------------------------------

async def _set_status(client_id: int, new_status: str) -> Optional[Client]:
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            return None
        client.status = new_status
        await session.commit()
        await session.refresh(client)
    return client


@router.callback_query(F.data.startswith("cli:block:"))
async def cb_block(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    client = await _set_status(client_id, "blocked")
    if client is None:
        await call.answer("Клиент не найден", show_alert=True)
        return
    await call.answer("🔒 Клиент заблокирован")
    await _send_card(call.message, client_id, edit=True)


@router.callback_query(F.data.startswith("cli:activate:"))
async def cb_activate(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    client = await _set_status(client_id, "active")
    if client is None:
        await call.answer("Клиент не найден", show_alert=True)
        return
    await call.answer("✅ Клиент активирован")
    await _send_card(call.message, client_id, edit=True)


# ----- delete -----------------------------------------------------------------

@router.callback_query(F.data.startswith("cli:delete:"))
async def cb_delete(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
    if client is None:
        await call.answer("Клиент не найден", show_alert=True)
        return

    text = (
        f"🗑 <b>Удалить клиента?</b>\n\n"
        f"🏢 {client.business_name}\n"
        f"🌐 <code>{client.slug}</code>\n\n"
        f"Будут удалены подписки, домены, товары и платежные записи "
        f"(каскадно). Railway-проект пока не удаляется."
    )
    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=_confirm_delete_kb(client_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("cli:del_confirm:"))
async def cb_delete_confirm(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await call.answer("Клиент не найден", show_alert=True)
            return
        slug = client.slug
        name = client.business_name
        await session.delete(client)
        await session.commit()

    logger.info("admin deleted client id=%s slug=%s", client_id, slug)
    await call.message.edit_text(
        f"🗑 Клиент <b>{name}</b> (<code>{slug}</code>) удалён.",
        parse_mode="HTML",
    )
    await call.answer("Удалено")
