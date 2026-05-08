"""Telegram admin: payment request approval/rejection.

Webhook from tech_bot creates PaymentRequest + sends message to admins
with inline keyboard. This module handles callbacks pay:approve / pay:reject.

On approve:
- type=subscription  → extend subscription by 1 month, status=active
- type=domain        → set client.domain_status="active"

On reject:
- mark request status="rejected"
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.db import AsyncSessionLocal
from app.models import Client, PaymentRequest, Subscription

logger = logging.getLogger(__name__)

router = Router(name="payments")
router.callback_query.filter(AdminFilter())


def payment_actions_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплата получена",
                    callback_data=f"pay:approve:{req_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"pay:reject:{req_id}",
                ),
            ]
        ]
    )


def format_payment_message(req: PaymentRequest) -> str:
    parts = [
        "💳 <b>Запрос на оплату</b>",
        f"#<code>{req.id}</code>",
        f"• Клиент: <code>{req.client_slug}</code>",
        f"• Тип: <b>{req.type}</b>",
    ]
    if req.amount is not None:
        cur = f" {req.currency}" if req.currency else ""
        parts.append(f"• Сумма: <b>{req.amount}{cur}</b>")
    if req.external_id:
        parts.append(f"• Ext ID: <code>{req.external_id}</code>")
    if req.note:
        parts.append(f"• Примечание: {req.note}")
    return "\n".join(parts)


# ---------- Approve ----------

@router.callback_query(F.data.startswith("pay:approve:"))
async def approve_payment(cb: CallbackQuery) -> None:
    try:
        req_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        req = await session.get(PaymentRequest, req_id)
        if req is None:
            await cb.answer("Запрос не найден", show_alert=True)
            return
        if req.status != "pending":
            await cb.answer(f"Уже обработан ({req.status})", show_alert=True)
            return

        client = await session.scalar(
            select(Client).where(Client.slug == req.client_slug)
        )
        if client is None:
            await cb.answer("Клиент не найден", show_alert=True)
            return

        result_lines: list[str] = []

        if req.type == "subscription":
            sub = await session.scalar(
                select(Subscription)
                .where(Subscription.client_id == client.id)
                .order_by(Subscription.id.desc())
                .limit(1)
            )
            if sub is None:
                await cb.answer(
                    "У клиента нет подписки. Создай её сначала.", show_alert=True
                )
                return

            now = datetime.now(timezone.utc)
            base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
            sub.expires_at = base + timedelta(days=30)
            sub.status = "active"
            result_lines.append(f"• Подписка продлена до <b>{sub.expires_at.strftime('%Y-%m-%d')}</b>")
            result_lines.append("• Статус подписки: <b>active</b>")

        elif req.type == "domain":
            client.domain_status = "active"
            result_lines.append("• Домен: <b>active</b>")

        else:
            result_lines.append(f"• Тип <code>{req.type}</code> — без автоматических действий")

        req.status = "approved"
        await session.commit()

        client_name = client.business_name
        client_slug = client.slug

    text = (
        "✅ <b>Оплата подтверждена</b>\n"
        f"#<code>{req_id}</code>\n"
        f"• Клиент: <b>{client_name}</b> (<code>{client_slug}</code>)\n"
        f"• Тип: {req.type}\n"
        + "\n".join(result_lines)
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML")
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, parse_mode="HTML")
    await cb.answer("Подтверждено")


# ---------- Reject ----------

@router.callback_query(F.data.startswith("pay:reject:"))
async def reject_payment(cb: CallbackQuery) -> None:
    try:
        req_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        req = await session.get(PaymentRequest, req_id)
        if req is None:
            await cb.answer("Запрос не найден", show_alert=True)
            return
        if req.status != "pending":
            await cb.answer(f"Уже обработан ({req.status})", show_alert=True)
            return
        req.status = "rejected"
        await session.commit()

    try:
        await cb.message.edit_text(
            "❌ <b>Запрос отклонён</b>\n"
            f"#<code>{req_id}</code>",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        await cb.message.answer("❌ Запрос отклонён")
    await cb.answer("Отклонено")
