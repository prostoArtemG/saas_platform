"""Telegram admin: products management.

Flow:
- BTN_PRODUCTS → list clients (inline buttons)
- pick client → enter test mode (show client_test_menu)
- Product add/edit is handled by client_cms router (CmsAddProduct FSM)
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import BTN_PRODUCTS, admin_main_menu, client_test_menu
from app.db import AsyncSessionLocal
from app.models import Client, Product

logger = logging.getLogger(__name__)

router = Router(name="products")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


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

    await state.update_data(
        selected_client_id=client.id,
        selected_client_slug=client.slug,
    )
    await cb.message.answer(
        f"🔧 <b>Тест-режим</b>: {client.business_name}\n"
        f"Slug: <code>{client.slug}</code>\n\n"
        f"Натисни ⬅️ Выйти из тест-режима щоб вернуться к панели админа.",
        parse_mode="HTML",
        reply_markup=client_test_menu(),
    )
    await cb.answer()
