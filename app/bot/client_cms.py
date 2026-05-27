"""Client CMS: Telegram interface for shop owners.

Only users whose Telegram ID matches Client.admin_telegram_id can use this
router. Platform admins (ADMIN_IDS) are explicitly excluded so they always
get the platform admin experience instead.

Menu sections:
  📦 Товары   — list & add products for their own store
  🌐 Мой сайт — link to their storefront
  📊 Заказы   — placeholder (order notifications arrive via bot already)
  ⚙️ Настройки — show their client info
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.filters import ClientFilter
from app.bot.keyboards import (
    BTN_CMS_ORDERS,
    BTN_CMS_PRODUCTS,
    BTN_CMS_SETTINGS,
    BTN_CMS_SITE,
    client_main_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, Product

logger = logging.getLogger(__name__)

router = Router(name="client_cms")
router.message.filter(ClientFilter())
router.callback_query.filter(ClientFilter())

SKIP = "-"


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_client(user_id: int) -> Client | None:
    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(Client).where(Client.admin_telegram_id == user_id)
        )


def _products_actions_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить товар",
                    callback_data=f"cms:prod:add:{client_id}",
                )
            ],
        ]
    )


def _availability_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="cms:avail:1"),
                InlineKeyboardButton(text="❌ Нет", callback_data="cms:avail:0"),
            ]
        ]
    )


# ── FSM states ────────────────────────────────────────────────────────────────

class CmsAddProduct(StatesGroup):
    name = State()
    category = State()
    description = State()
    price = State()
    image_url = State()
    is_available = State()


# ── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def client_start(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None:
        return
    await message.answer(
        f"👋 Привет, <b>{client.business_name}</b>!\n"
        "Выбери раздел в меню ниже:",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )


# ── /cancel (FSM) ────────────────────────────────────────────────────────────

@router.message(StateFilter(CmsAddProduct), Command("cancel"))
async def cms_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление отменено.", reply_markup=client_main_menu())


# ── 📦 Товары ─────────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_PRODUCTS)
async def cms_products(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None:
        return

    async with AsyncSessionLocal() as session:
        products = (
            await session.scalars(
                select(Product)
                .where(Product.client_id == client.id)
                .order_by(Product.id.desc())
            )
        ).all()

    lines = [f"📦 <b>{client.business_name}</b> — Товары\n"]
    if products:
        for p in products:
            mark = "✅" if p.is_available else "❌"
            cat = f" · {p.category}" if p.category else ""
            lines.append(
                f"{mark} #{p.id} <b>{p.name}</b> — {p.price} грн{cat}"
            )
    else:
        lines.append("<i>Товаров пока нет. Добавьте первый!</i>")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_products_actions_kb(client.id),
    )


# ── 🌐 Мой сайт ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_SITE)
async def cms_site(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None:
        return

    from app.config import settings as app_settings

    # Derive base URL from payment_webhook_base_url if configured,
    # otherwise fall back to showing the relative path.
    base = (app_settings.payment_webhook_base_url or "").rstrip("/")
    site_url = f"{base}/site/{client.slug}" if base else f"/site/{client.slug}"

    await message.answer(
        f"🌐 <b>Ваш сайт:</b>\n"
        f"<code>{site_url}</code>\n\n"
        f"Slug: <code>{client.slug}</code>",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )


# ── 📊 Заказы ─────────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_ORDERS)
async def cms_orders(message: Message) -> None:
    await message.answer(
        "📊 <b>Заказы</b>\n\n"
        "Уведомления о новых заказах приходят прямо в этот чат.\n"
        "Полноценный раздел заказов скоро появится.",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )


# ── ⚙️ Настройки ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_SETTINGS)
async def cms_settings(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None:
        return

    await message.answer(
        f"⚙️ <b>Настройки магазина</b>\n\n"
        f"🏪 Название: <b>{client.business_name}</b>\n"
        f"🔗 Slug: <code>{client.slug}</code>\n"
        f"📋 Шаблон: <code>{client.template_name}</code>\n"
        f"📌 Статус: <code>{client.status}</code>",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )


# ── FSM: add product ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:prod:add:"))
async def cms_start_add(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        client_id = int(cb.data.split(":")[3])
    except (ValueError, IndexError):
        await cb.answer("Ошибка", show_alert=True)
        return

    # Verify the client belongs to this user
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None or client.id != client_id:
        await cb.answer("Нет доступа", show_alert=True)
        return

    await state.update_data(client_id=client_id)
    await state.set_state(CmsAddProduct.name)
    await cb.message.answer(  # type: ignore[union-attr]
        "📦 <b>Новый товар</b>\n\n"
        f"Шаг 1/6 — Название:\n"
        f"<i>(отправь /cancel для отмены)</i>",
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.name))
async def cms_add_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Название не может быть пустым. Введите название:")
        return
    await state.update_data(name=text)
    await state.set_state(CmsAddProduct.category)
    await message.answer(
        f"Шаг 2/6 — Категория (или <code>{SKIP}</code> чтобы пропустить):",
        parse_mode="HTML",
    )


@router.message(StateFilter(CmsAddProduct.category))
async def cms_add_category(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(category=None if val == SKIP else val)
    await state.set_state(CmsAddProduct.description)
    await message.answer(
        f"Шаг 3/6 — Описание (или <code>{SKIP}</code>):",
        parse_mode="HTML",
    )


@router.message(StateFilter(CmsAddProduct.description))
async def cms_add_description(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(description=None if val == SKIP else val)
    await state.set_state(CmsAddProduct.price)
    await message.answer("Шаг 4/6 — Цена (например: 150 или 99.99):")


@router.message(StateFilter(CmsAddProduct.price))
async def cms_add_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw)
        if price < 0:
            raise ValueError("negative price")
    except (InvalidOperation, ValueError):
        await message.answer("Некорректная цена. Введите число (например: 150):")
        return
    await state.update_data(price=str(price))
    await state.set_state(CmsAddProduct.image_url)
    await message.answer(
        f"Шаг 5/6 — URL изображения (или <code>{SKIP}</code>):",
        parse_mode="HTML",
    )


@router.message(StateFilter(CmsAddProduct.image_url))
async def cms_add_image_url(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(image_url=None if val == SKIP else val)
    await state.set_state(CmsAddProduct.is_available)
    await message.answer(
        "Шаг 6/6 — Товар доступен для заказа?",
        reply_markup=_availability_kb(),
    )


@router.callback_query(F.data.startswith("cms:avail:"), StateFilter(CmsAddProduct.is_available))
async def cms_add_availability(cb: CallbackQuery, state: FSMContext) -> None:
    is_available = cb.data.endswith(":1")
    data = await state.get_data()
    client_id: int | None = data.get("client_id")

    # Re-verify ownership
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_client(user_id)
    if client is None or client.id != client_id:
        await cb.answer("Нет доступа", show_alert=True)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        product = Product(
            client_id=client_id,
            name=data["name"],
            category=data.get("category"),
            description=data.get("description"),
            price=Decimal(data["price"]),
            image_url=data.get("image_url"),
            is_available=is_available,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

    await state.clear()
    avail_label = "✅ Да" if is_available else "❌ Нет"
    await cb.message.answer(  # type: ignore[union-attr]
        f"✅ Товар <b>{data['name']}</b> добавлен!\n"
        f"Цена: {data['price']} грн · Доступен: {avail_label}",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )
    await cb.answer("Товар добавлен")
