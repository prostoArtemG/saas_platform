"""Admin client management: card view + actions (block/activate/delete/payments/domain/connect bot)."""
from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import BTN_CLIENTS, admin_main_menu
from app.db import AsyncSessionLocal
from app.models import Client, Domain, Payment, Plan, Subscription
from app.services.railway_api import redeploy_technomarket_client

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


def _card_kb(client_id: int, bot_mode: str = "shared", has_railway: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="💳 Платежи", callback_data=f"cli:payments:{client_id}"),
            InlineKeyboardButton(text="🌐 Домен", callback_data=f"cli:domain:{client_id}"),
        ],
    ]
    if bot_mode == "personal":
        rows.append([
            InlineKeyboardButton(text="🤖 Підключити бота", callback_data=f"cli:connect_bot:{client_id}"),
            InlineKeyboardButton(text="🔍 Перевірити бота", callback_data=f"cli:check_bot:{client_id}"),
        ])
        rows.append([
            InlineKeyboardButton(text="▶️ Запустити вебхук", callback_data=f"cli:start_bot:{client_id}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🤖 Підключити бота", callback_data=f"cli:connect_bot:{client_id}"),
            InlineKeyboardButton(text="🔍 Перевірити бота", callback_data=f"cli:check_bot:{client_id}"),
        ])
    if has_railway:
        rows.append([
            InlineKeyboardButton(text="🔄 Оновити сайт клієнта", callback_data=f"cli:redeploy:{client_id}"),
        ])
    rows += [
        [
            InlineKeyboardButton(text="🔒 Заблокировать", callback_data=f"cli:block:{client_id}"),
            InlineKeyboardButton(text="✅ Активировать", callback_data=f"cli:activate:{client_id}"),
        ],
        [InlineKeyboardButton(text="📦 Сменить тариф", callback_data=f"cli:plan:{client_id}")],
        [InlineKeyboardButton(text="🗑 Видалити", callback_data=f"client:delete:{client_id}")],
        [InlineKeyboardButton(text="« К списку", callback_data="cli:list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _mask_token(token: Optional[str]) -> str:
    """Return safe display form of a bot token, e.g. '7993832476:AA...BQDYs'."""
    if not token:
        return "—"
    t = token.strip()
    if ":" not in t:
        # Unknown shape — fall back to length-based mask
        if len(t) <= 6:
            return "•" * len(t)
        return f"{t[:2]}...{t[-3:]}"
    head, secret = t.split(":", 1)
    if len(secret) <= 7:
        secret_masked = secret[:2] + "..." + secret[-2:] if len(secret) > 4 else "•••"
    else:
        secret_masked = f"{secret[:2]}...{secret[-5:]}"
    return f"{head}:{secret_masked}"


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

    # Bot connection status
    token_masked = _mask_token(client.telegram_bot_token)
    if client.telegram_bot_token:
        if client.bot_username:
            bot_str = f"connected • @{client.bot_username}"
        else:
            bot_str = "connected"
    else:
        bot_str = "not connected"

    bot_mode = getattr(client, "bot_mode", "shared") or "shared"
    if bot_mode == "personal":
        bot_mode_str = "🔵 Особистий (personal)"
    else:
        bot_mode_str = "🌐 Загальний платформи (shared)"

    # Deployment info (personal bot Railway deploy)
    deployment_status = getattr(client, "deployment_status", None)
    railway_url = getattr(client, "railway_url", None)
    deployment_error = getattr(client, "deployment_error", None)
    if deployment_status == "ready":
        deploy_str = f"✅ ready — {railway_url or '—'}"
    elif deployment_status == "failed":
        err_short = (deployment_error or "")[:120]
        deploy_str = f"❌ failed — {err_short}"
    elif deployment_status:
        deploy_str = deployment_status
    else:
        deploy_str = "—"

    admin_tg = (
        f"<code>{client.admin_telegram_id}</code>"
        if client.admin_telegram_id
        else "—"
    )
    bot_username_str = f"@{client.bot_username}" if client.bot_username else "—"
    bot_id_str = f"<code>{client.bot_id}</code>" if client.bot_id else "—"
    if client.bot_admin_ids:
        bot_admins_str = client.bot_admin_ids
    else:
        bot_admins_str = "недоступно через API"

    lines = [
        f"🏢 <b>{client.business_name}</b>",
        f"🆔 ID: <code>{client.id}</code>",
        f"🌐 Slug: <code>{client.slug}</code> • {domain_str}",
        f"🤖 Bot username: {bot_username_str}",
        f"🆔 Bot ID: {bot_id_str}",
        f"🔑 Bot token: <code>{token_masked}</code>",
        f"👤 Client admin TG ID: {admin_tg}",
        f"👥 Bot admins: {bot_admins_str}",
        f"📦 Тариф: {plan_name}",
        f"✅ Статус: <b>{client.status}</b> • подписка: {sub_status}",
        f"📅 Истекает: {expires}",
        f"💳 Последний платёж: {last_payment_str}",
        f"🤖 Bot: <b>{bot_str}</b>",
        f"📲 Режим бота: {bot_mode_str}",
        f"🚀 Деплой: {deploy_str}",
    ]
    return "\n".join(lines)


async def _send_card(message_target, client_id: int, *, edit: bool = False) -> bool:
    """Render and send/edit the client card. Returns False if client not found."""
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            return False
        text = await _build_card_text(session, client)

    bot_mode = getattr(client, "bot_mode", "shared") or "shared"
    has_railway = bool(
        getattr(client, "railway_project_id", None)
        and getattr(client, "railway_service_id", None)
    )
    kb = _card_kb(client_id, bot_mode=bot_mode, has_railway=has_railway)
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


# ----- delete (Ukrainian flow: client:delete:) --------------------------------

@router.callback_query(F.data.startswith("client:view:"))
async def cb_client_view(call: CallbackQuery) -> None:
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    ok = await _send_card(call.message, client_id, edit=True)
    if not ok:
        await call.answer("Клієнт не знайдений", show_alert=True)
        return
    await call.answer()


@router.callback_query(F.data.startswith("client:delete:"))
async def cb_delete_client_confirm(cb: CallbackQuery) -> None:
    try:
        client_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("bad id", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if not client:
            await cb.answer("Клієнт не знайдений", show_alert=True)
            return
        name = client.business_name

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, видалити", callback_data=f"client:delete_confirm:{client_id}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data=f"client:view:{client_id}"),
        ]
    ])
    await cb.message.edit_text(
        f"⚠️ <b>Видалити клієнта?</b>\n\n"
        f"Це видалить всі дані клієнта включаючи підписки та домени.\n\n"
        f"Клієнт: <b>{name}</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("client:delete_confirm:"))
async def cb_delete_client(cb: CallbackQuery) -> None:
    try:
        client_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("bad id", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if not client:
            await cb.answer("Клієнт не знайдений", show_alert=True)
            return
        name = client.business_name
        await session.delete(client)
        await session.commit()

    logger.info("admin deleted client id=%s name=%s", client_id, name)
    await cb.message.edit_text(
        f"✅ Клієнт <b>{name}</b> видалений.",
        parse_mode="HTML",
    )
    await cb.answer("Видалено")


# ----- connect bot ------------------------------------------------------------

class ConnectBot(StatesGroup):
    waiting_token = State()


_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


async def _telegram_get_me(token: str) -> dict:
    """Call Telegram getMe. Returns the ``result`` dict on ok=True.
    Raises ValueError with a human-readable description otherwise."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(url) as resp:
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as e:
        raise ValueError(f"network error: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"unexpected error: {e}") from e

    if not isinstance(data, dict) or not data.get("ok"):
        desc = (data or {}).get("description", "invalid token")
        raise ValueError(desc)
    return data["result"]


@router.callback_query(F.data.startswith("cli:check_bot:"))
async def cb_check_bot(call: CallbackQuery) -> None:
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

    if not client.telegram_bot_token:
        await call.answer("Бот не подключён", show_alert=True)
        return

    try:
        me = await _telegram_get_me(client.telegram_bot_token)
    except ValueError as e:
        await call.answer(f"❌ Token invalid: {e}", show_alert=True)
        return

    username = me.get("username") or "—"
    bot_id = me.get("id")

    # Persist username + bot_id snapshot (best-effort)
    async with AsyncSessionLocal() as session:
        c = await session.get(Client, client_id)
        if c is not None:
            changed = False
            if username and username != "—" and c.bot_username != username:
                c.bot_username = username
                changed = True
            if bot_id and c.bot_id != bot_id:
                c.bot_id = bot_id
                changed = True
            if changed:
                await session.commit()

    await call.answer(
        f"✅ Bot: @{username}\nID: {bot_id}",
        show_alert=True,
    )
    # Refresh card to reflect saved username/bot_id
    await _send_card(call.message, client_id, edit=True)


@router.callback_query(F.data.startswith("cli:start_bot:"))
async def cb_start_bot(call: CallbackQuery) -> None:
    """Manually re-register the webhook for a personal client bot."""
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
    if client is None:
        await call.answer("Клієнта не знайдено", show_alert=True)
        return
    if not client.telegram_bot_token:
        await call.answer("❌ BOT_TOKEN не вказаний", show_alert=True)
        return

    from app.config import settings as _s
    from app.services.client_bot_manager import start_client_bot, is_running

    wb_base = _s.client_bot_webhook_base
    if not wb_base:
        await call.answer("❌ CLIENT_BOT_WEBHOOK_BASE не налаштований", show_alert=True)
        return

    if is_running(client.slug):
        await call.answer("ℹ️ Вебхук вже активний", show_alert=True)
        return

    ok = await start_client_bot(wb_base, client.slug, client.telegram_bot_token)
    if ok:
        await call.answer("✅ Вебхук запущено", show_alert=True)
    else:
        await call.answer("❌ Не вдалось запустити — перевірте BOT_TOKEN", show_alert=True)


# ----- redeploy ---------------------------------------------------------------

@router.callback_query(F.data.startswith("cli:redeploy:"))
async def cb_redeploy(call: CallbackQuery) -> None:
    """Refresh env vars and trigger a new Railway deploy for the client project."""
    try:
        client_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
    if client is None:
        await call.answer("Клієнта не знайдено", show_alert=True)
        return

    project_id = getattr(client, "railway_project_id", None)
    service_id = getattr(client, "railway_service_id", None)
    if not project_id or not service_id:
        await call.answer("❌ Railway project/service ID не знайдено", show_alert=True)
        return

    from app.config import settings as _s

    # Resolve ADMIN_IDS: prefer client's own Telegram ID, fallback to platform admin
    admin_ids = str(client.admin_telegram_id) if client.admin_telegram_id else ""
    if not admin_ids and _s.admin_ids:
        admin_ids = str(_s.admin_ids[0])
        logger.warning(
            "cb_redeploy: admin_telegram_id empty for slug=%s — fallback to platform admin %s",
            client.slug, admin_ids,
        )
    logger.info("cb_redeploy: admin_ids=%s slug=%s", admin_ids, client.slug)

    saas_url = (
        _s.client_bot_webhook_base.rstrip("/")
        if _s.client_bot_webhook_base
        else f"https://{_s.platform_domain}"
    )

    try:
        await redeploy_technomarket_client(
            project_id=project_id,
            service_id=service_id,
            slug=client.slug,
            admin_ids=admin_ids,
            saas_platform_url=saas_url,
            railway_url=getattr(client, "railway_url", "") or "",
            cloudinary_cloud=_s.cloudinary_cloud_name,
            cloudinary_key=_s.cloudinary_api_key,
            cloudinary_secret=_s.cloudinary_api_secret,
        )
        async with AsyncSessionLocal() as session:
            c = await session.get(Client, client_id)
            if c:
                c.deployment_status = "updating"
                c.deployment_error = None
                await session.commit()
        await call.answer("✅ Оновлення запущено", show_alert=True)
    except Exception as exc:
        logger.warning("cb_redeploy failed for slug=%s: %s", client.slug, exc, exc_info=True)
        async with AsyncSessionLocal() as session:
            c = await session.get(Client, client_id)
            if c:
                c.deployment_status = "failed"
                c.deployment_error = str(exc)[:1000]
                await session.commit()
        await call.answer(f"❌ Помилка: {str(exc)[:120]}", show_alert=True)

    await _send_card(call.message, client_id, edit=True)


@router.callback_query(F.data.startswith("cli:connect_bot:"))
async def cb_connect_bot(call: CallbackQuery, state: FSMContext) -> None:
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

    await state.clear()
    await state.update_data(client_id=client_id)
    await state.set_state(ConnectBot.waiting_token)
    await call.message.answer(
        f"🔗 <b>Подключение бота</b> для <b>{client.business_name}</b>\n\n"
        f"Отправь <b>BOT_TOKEN</b> (формат <code>123456789:AA...</code>).\n"
        f"Токен будет проверен через Telegram API getMe.\n"
        f"Для отмены /cancel",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(StateFilter(ConnectBot), Command("cancel"))
async def cb_connect_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Подключение бота отменено.", reply_markup=admin_main_menu())


@router.message(ConnectBot.waiting_token, F.text)
async def cb_connect_token(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    client_id = data.get("client_id")
    if client_id is None:
        await state.clear()
        await message.answer("Состояние сброшено.", reply_markup=admin_main_menu())
        return

    if not _TOKEN_RE.match(raw):
        await message.answer(
            "❌ Похоже на некорректный токен. Формат: <code>123456789:AA...</code>\n"
            "Повтори или /cancel.",
            parse_mode="HTML",
        )
        return

    # Validate via Telegram getMe
    try:
        me = await _telegram_get_me(raw)
    except ValueError as e:
        await message.answer(
            f"❌ Telegram отверг токен: <code>{e}</code>\n"
            f"Повтори или /cancel.",
            parse_mode="HTML",
        )
        return

    username = me.get("username")
    bot_id = me.get("id")
    if not username:
        await message.answer("❌ Telegram не вернул username. Повтори или /cancel.")
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await state.clear()
            await message.answer("Клиент не найден.", reply_markup=admin_main_menu())
            return
        client.telegram_bot_token = raw
        client.bot_username = username
        if bot_id:
            client.bot_id = bot_id
        await session.commit()

    await state.clear()
    logger.info(
        "admin connected bot for client_id=%s @%s (id=%s)", client_id, username, bot_id
    )
    await message.answer(
        f"✅ <b>Бот подключён</b>\n\n"
        f"@{username} (id: <code>{bot_id}</code>)\n"
        f"Токен сохранён.",
        parse_mode="HTML",
        reply_markup=admin_main_menu(),
    )
    await _send_card(message, client_id, edit=False)


# ──────────────────────────────────────────────────────────────────────────────
# Plan change
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.regexp(r"^cli:plan:(\d+)$"))
async def plan_change_menu(callback: CallbackQuery) -> None:
    """Show a list of plans to switch the client to."""
    await callback.answer()
    client_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await callback.message.answer("Клиент не найден.")
            return
        plans = (await session.scalars(select(Plan).where(Plan.is_active == True).order_by(Plan.price))).all()  # noqa: E712

    if not plans:
        await callback.message.answer(
            "Нет активных тарифов.",
            reply_markup=_back_kb(client_id),
        )
        return

    current_mark = ""
    rows: list[list[InlineKeyboardButton]] = []
    for p in plans:
        is_current = p.id == client.plan_id
        label = f"{'✅ ' if is_current else ''}{p.name} — {p.price} грн/мес"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"cli:plan:set:{client_id}:{p.id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"cli:open:{client_id}")])

    await callback.message.edit_text(
        f"📦 <b>Смена тарифа</b>\n"
        f"Клиент: <b>{client.business_name}</b>\n\n"
        f"Текущий тариф ID: <code>{client.plan_id or '—'}</code>\n"
        f"Выберите новый тариф:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^cli:plan:set:(\d+):(\d+)$"))
async def plan_change_set(callback: CallbackQuery) -> None:
    """Apply the selected plan to client and their active subscription."""
    await callback.answer()
    parts = callback.data.split(":")
    client_id = int(parts[3])
    plan_id = int(parts[4])

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await callback.message.answer("Клиент не найден.")
            return
        plan = await session.get(Plan, plan_id)
        if plan is None:
            await callback.message.answer("Тариф не найден.")
            return

        client.plan_id = plan_id

        # Update active/trial subscription if exists
        subscription: Optional[Subscription] = await session.scalar(
            select(Subscription)
            .where(Subscription.client_id == client_id)
            .where(Subscription.status.in_(["active", "trial"]))
            .order_by(Subscription.created_at.desc())
        )
        if subscription is not None:
            subscription.plan_id = plan_id

        await session.commit()

    logger.info("admin changed plan for client_id=%s to plan_id=%s", client_id, plan_id)
    await callback.message.edit_text(
        f"✅ <b>Тариф обновлён</b>\n\n"
        f"Клиент: <b>{client.business_name}</b>\n"
        f"Новый тариф: <b>{plan.name}</b>",
        parse_mode="HTML",
        reply_markup=_back_kb(client_id),
    )

# ── 🔄 Redeploy client site ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cli:redeploy:"))
async def cli_redeploy(cb: CallbackQuery) -> None:
    """Refresh env vars and trigger a new Railway deployment for the client."""
    await cb.answer()
    try:
        client_id = int(cb.data.split(":")[-1])
    except ValueError:
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await cb.message.answer("❌ Клієнта не знайдено.")
            return
        if not client.railway_project_id or not client.railway_service_id:
            await cb.message.answer(
                "❌ У клієнта немає Railway project/service ID — redeploy недоступний."
            )
            return

        from app.config import settings as _s

        # Resolve ADMIN_IDS
        admin_ids = str(client.admin_telegram_id) if client.admin_telegram_id else ""
        if not admin_ids and _s.admin_ids:
            admin_ids = str(_s.admin_ids[0])
            logger.warning(
                "cli_redeploy: admin_telegram_id empty for client_id=%s slug=%s"
                " — falling back to platform admin %s",
                client_id, client.slug, admin_ids,
            )
        logger.info("cli_redeploy: ADMIN_IDS=%s slug=%s", admin_ids, client.slug)

        client.deployment_status = "updating"
        client.deployment_error = None
        await session.commit()

    try:
        from app.services.railway_api import redeploy_technomarket_client
        await redeploy_technomarket_client(
            project_id=client.railway_project_id,
            service_id=client.railway_service_id,
            slug=client.slug,
            admin_ids=admin_ids,
            saas_platform_url="",
            railway_url=client.railway_url or "",
            cloudinary_cloud=_s.cloudinary_cloud_name,
            cloudinary_key=_s.cloudinary_api_key,
            cloudinary_secret=_s.cloudinary_api_secret,
        )
        async with AsyncSessionLocal() as session:
            upd = await session.get(Client, client_id)
            if upd:
                upd.deployment_status = "updating"
                upd.deployment_error = None
                await session.commit()
        await cb.message.answer(
            f"✅ <b>Оновлення запущено</b>\n\n"
            f"Клієнт: <b>{client.business_name}</b>\n"
            f"ADMIN_IDS: <code>{admin_ids}</code>\n"
            f"Railway перезапускає сервіс — зачекайте 1–2 хвилини.",
            parse_mode="HTML",
            reply_markup=_back_kb(client_id),
        )
    except Exception as exc:
        logger.warning("cli_redeploy failed for client_id=%s: %s", client_id, exc, exc_info=True)
        async with AsyncSessionLocal() as session:
            upd = await session.get(Client, client_id)
            if upd:
                upd.deployment_status = "failed"
                upd.deployment_error = str(exc)[:500]
                await session.commit()
        await cb.message.answer(
            f"❌ <b>Помилка redeploy</b>\n\n<code>{str(exc)[:400]}</code>",
            parse_mode="HTML",
            reply_markup=_back_kb(client_id),
        )
