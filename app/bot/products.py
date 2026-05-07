"""Telegram admin: products management.

Flow:
- BTN_PRODUCTS → list clients (inline buttons)
- pick client → show its products + "Add product" button
- "Add product" → FSM(name → category → description → price → image_url → is_available)
- save Product(client_id=...)
"""
import logging
from decimal import Decimal, InvalidOperation

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
from app.bot.keyboards import BTN_PRODUCTS, admin_main_menu
from app.db import AsyncSessionLocal
from app.models import Client, Product

logger = logging.getLogger(__name__)

router = Router(name="products")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

SKIP = "-"


class AddProduct(StatesGroup):
    name = State()
    category = State()
    description = State()
    price = State()
    image_url = State()
    is_available = State()


def _cancel_hint() -> str:
    return f"Чтобы пропустить поле — отправь <code>{SKIP}</code>. Для отмены: /cancel"


def _clients_kb(clients: list[Client]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{c.business_name} ({c.slug})",
                callback_data=f"prod:client:{c.id}",
            )
        ]
        for c in clients
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _client_actions_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить товар",
                    callback_data=f"prod:add:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« К списку клиентов",
                    callback_data="prod:back",
                )
            ],
        ]
    )


def _availability_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="prod:avail:1"),
                InlineKeyboardButton(text="❌ Нет", callback_data="prod:avail:0"),
            ]
        ]
    )


# ---------- Cancel ----------

@router.message(StateFilter(AddProduct), Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление товара отменено.", reply_markup=admin_main_menu())


# ---------- Entry: show clients ----------

@router.message(F.text == BTN_PRODUCTS)
async def list_clients_for_products(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        clients = (
            await session.scalars(select(Client).order_by(Client.id))
        ).all()

    if not clients:
        await message.answer(
            "📦 Сначала создай хотя бы одного клиента.",
            reply_markup=admin_main_menu(),
        )
        return

    await message.answer(
        "📦 <b>Товары</b>\nВыбери клиента:",
        parse_mode="HTML",
        reply_markup=_clients_kb(list(clients)),
    )


@router.callback_query(F.data == "prod:back")
async def back_to_clients(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        clients = (
            await session.scalars(select(Client).order_by(Client.id))
        ).all()

    if not clients:
        await cb.message.edit_text("📦 Клиентов нет.")
        await cb.answer()
        return

    await cb.message.edit_text(
        "📦 <b>Товары</b>\nВыбери клиента:",
        parse_mode="HTML",
        reply_markup=_clients_kb(list(clients)),
    )
    await cb.answer()


# ---------- Show client's products ----------

@router.callback_query(F.data.startswith("prod:client:"))
async def show_client_products(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        client_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await cb.answer("Клиент не найден", show_alert=True)
            return
        products = (
            await session.scalars(
                select(Product)
                .where(Product.client_id == client_id)
                .order_by(Product.id.desc())
            )
        ).all()

    lines = [
        f"📦 <b>{client.business_name}</b> "
        f"(<code>{client.slug}</code>)\n"
    ]
    if products:
        for p in products:
            mark = "✅" if p.is_available else "❌"
            cat = f" · {p.category}" if p.category else ""
            lines.append(
                f"{mark} #{p.id} <b>{p.name}</b> — {p.price}{cat}"
            )
    else:
        lines.append("<i>Товаров пока нет.</i>")

    lines.append(f"\n🌐 /site/{client.slug}")

    await cb.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_client_actions_kb(client_id),
    )
    await cb.answer()


# ---------- FSM: start add ----------

@router.callback_query(F.data.startswith("prod:add:"))
async def start_add_product(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        client_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ID", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await cb.answer("Клиент не найден", show_alert=True)
            return

    await state.set_state(AddProduct.name)
    await state.update_data(client_id=client_id, client_name=client.business_name)
    await cb.message.answer(
        f"➕ Добавление товара для <b>{client.business_name}</b>\n\n"
        f"Шаг 1/6. Введи <b>название</b> товара.\n{_cancel_hint()}",
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AddProduct.name, F.text)
async def step_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 255:
        await message.answer("Название должно быть от 1 до 255 символов. Повтори.")
        return
    await state.update_data(name=name)
    await state.set_state(AddProduct.category)
    await message.answer(
        f"Шаг 2/6. Введи <b>категорию</b> (или <code>{SKIP}</code> чтобы пропустить).",
        parse_mode="HTML",
    )


@router.message(AddProduct.category, F.text)
async def step_category(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    category = None if raw == SKIP else raw[:128]
    await state.update_data(category=category)
    await state.set_state(AddProduct.description)
    await message.answer(
        f"Шаг 3/6. Введи <b>описание</b> (или <code>{SKIP}</code>).",
        parse_mode="HTML",
    )


@router.message(AddProduct.description, F.text)
async def step_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    description = None if raw == SKIP else raw[:4000]
    await state.update_data(description=description)
    await state.set_state(AddProduct.price)
    await message.answer("Шаг 4/6. Введи <b>цену</b> (число, например 199.90).", parse_mode="HTML")


@router.message(AddProduct.price, F.text)
async def step_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("Это не число. Повтори (например: 199.90).")
        return
    if price < 0:
        await message.answer("Цена не может быть отрицательной. Повтори.")
        return
    await state.update_data(price=str(price))
    await state.set_state(AddProduct.image_url)
    await message.answer(
        f"Шаг 5/6. Введи <b>image_url</b> (или <code>{SKIP}</code>).",
        parse_mode="HTML",
    )


@router.message(AddProduct.image_url, F.text)
async def step_image_url(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    image_url = None
    if raw != SKIP:
        if not (raw.startswith("http://") or raw.startswith("https://")) or len(raw) > 1024:
            await message.answer(
                "URL должен начинаться с http:// или https:// и быть короче 1024 символов. "
                f"Повтори или отправь <code>{SKIP}</code>.",
                parse_mode="HTML",
            )
            return
        image_url = raw
    await state.update_data(image_url=image_url)
    await state.set_state(AddProduct.is_available)
    await message.answer(
        "Шаг 6/6. Товар <b>в наличии</b>?",
        parse_mode="HTML",
        reply_markup=_availability_kb(),
    )


@router.callback_query(AddProduct.is_available, F.data.startswith("prod:avail:"))
async def step_availability(cb: CallbackQuery, state: FSMContext) -> None:
    flag = cb.data.split(":")[2]
    is_available = flag == "1"

    data = await state.get_data()
    client_id = data["client_id"]

    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if client is None:
            await state.clear()
            await cb.answer("Клиент не найден", show_alert=True)
            return

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

        product_name = product.name
        product_id = product.id
        client_slug = client.slug

    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "✅ <b>Товар добавлен</b>\n"
        f"#{product_id} • <b>{product_name}</b>\n"
        f"Статус: {'в наличии' if is_available else 'нет в наличии'}\n"
        f"🌐 /site/{client_slug}",
        parse_mode="HTML",
        reply_markup=_client_actions_kb(client_id),
    )
    await cb.answer("Готово")
