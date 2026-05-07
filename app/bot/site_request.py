import logging
import re

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.db import AsyncSessionLocal
from app.models import Client, Plan, SiteRequest, Subscription

logger = logging.getLogger(__name__)

router = Router(name="site_request")
router.callback_query.filter(AdminFilter())


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")


def request_actions_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать клиента",
                    callback_data=f"req:approve:{req_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"req:reject:{req_id}",
                ),
            ]
        ]
    )


def _make_slug(business_name: str, req_id: int) -> str:
    base = (business_name or "").strip().lower()
    base = _SLUG_CLEAN_RE.sub("-", base).strip("-_")
    if not base or not base[0].isalnum():
        base = f"client-{req_id}"
    base = base[:55]
    return f"{base}-{req_id}"


def _parse_admin_tg_id(telegram: str) -> int | None:
    raw = (telegram or "").strip().lstrip("@")
    if raw.isdigit():
        try:
            return int(raw)
        except ValueError:
            return None
    return None


async def _pick_plan(session, requested_plan: str) -> Plan | None:
    if requested_plan:
        plan = await session.scalar(
            select(Plan).where(Plan.name.ilike(requested_plan.strip()))
        )
        if plan is not None:
            return plan
    return await session.scalar(select(Plan).order_by(Plan.id).limit(1))


@router.callback_query(F.data.startswith("req:approve:"))
async def approve_request(cb: CallbackQuery) -> None:
    try:
        req_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        req = await session.get(SiteRequest, req_id)
        if req is None:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if req.status != "new":
            await cb.answer(f"Заявка уже обработана ({req.status})", show_alert=True)
            return

        plan = await _pick_plan(session, req.plan)
        if plan is None:
            await cb.answer(
                "В системе нет тарифов. Создай тариф и повтори.", show_alert=True
            )
            return

        slug = _make_slug(req.business_name, req.id)
        # Ensure unique slug
        exists = await session.scalar(select(Client.id).where(Client.slug == slug))
        if exists:
            slug = f"{slug}-{req.id}x"

        client = Client(
            business_name=req.business_name,
            slug=slug,
            telegram_bot_token=None,
            admin_telegram_id=_parse_admin_tg_id(req.telegram),
            status="active",
        )
        session.add(client)
        await session.flush()

        sub = Subscription(
            client_id=client.id,
            plan_id=plan.id,
            status="trial",
        )
        session.add(sub)

        req.status = "approved"
        await session.commit()

        client_name = client.business_name
        client_slug = client.slug
        plan_name = plan.name
        sub_status = sub.status

    text = (
        "✅ <b>Клиент создан</b>\n"
        f"• Бизнес: <b>{client_name}</b>\n"
        f"• Slug: <code>{client_slug}</code>\n"
        f"• Telegram: <code>{req.telegram}</code>\n"
        f"• Тариф: <b>{plan_name}</b>\n"
        f"• Статус подписки: <b>{sub_status}</b>"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML")
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, parse_mode="HTML")
    await cb.answer("Клиент создан")


@router.callback_query(F.data.startswith("req:reject:"))
async def reject_request(cb: CallbackQuery) -> None:
    try:
        req_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        req = await session.get(SiteRequest, req_id)
        if req is None:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if req.status != "new":
            await cb.answer(f"Заявка уже обработана ({req.status})", show_alert=True)
            return
        req.status = "rejected"
        await session.commit()

    try:
        await cb.message.edit_text(
            "❌ <b>Заявка отклонена</b>\n"
            f"#<code>{req_id}</code>",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        await cb.message.answer("❌ Заявка отклонена")
    await cb.answer("Отклонено")
