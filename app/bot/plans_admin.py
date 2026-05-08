"""Admin plans management: list, card view, create, edit fields, toggle active."""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import BTN_CREATE_PLAN, BTN_PLANS, admin_main_menu
from app.db import AsyncSessionLocal
from app.models import Plan

logger = logging.getLogger(__name__)

router = Router(name="plans_admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# ---------- States ----------------------------------------------------------

class CreatePlan(StatesGroup):
    name = State()
    slug = State()
    price = State()
    currency = State()
    products_limit = State()
    images_per_product_limit = State()
    domains_limit = State()
    users_limit = State()
    analytics_enabled = State()


class EditPlan(StatesGroup):
    waiting_value = State()  # uses state data: plan_id, field


SKIP = "-"

EDITABLE_FIELDS = {
    "name": "Название",
    "slug": "Slug",
    "price": "Цена",
    "currency": "Валюта",
    "products_limit": "Лимит товаров",
    "images_per_product_limit": "Лимит фото на товар",
    "domains_limit": "Лимит доменов",
    "users_limit": "Лимит юзеров",
}


# ---------- Helpers ---------------------------------------------------------

_slug_re = re.compile(r"[^a-z0-9-]+")


def _slugify(name: str) -> str:
    s = name.strip().lower().replace(" ", "-")
    s = _slug_re.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "plan"


def _fmt_limit(v: Optional[int]) -> str:
    return "∞" if v is None else str(v)


def _plan_card_text(p: Plan) -> str:
    status_icon = "✅" if p.active else "🔒"
    price = p.price if p.price is not None else p.price_monthly
    return (
        f"📦 <b>{p.name}</b> {status_icon}\n"
        f"🔗 <code>{p.slug or '—'}</code>\n"
        f"💰 <b>{price} {p.currency}</b>\n"
        f"📦 Товары: <b>{_fmt_limit(p.products_limit)}</b>\n"
        f"🖼 Фото на товар: <b>{_fmt_limit(p.images_per_product_limit)}</b>\n"
        f"🌐 Домены: <b>{_fmt_limit(p.domains_limit)}</b>\n"
        f"👥 Юзеры: <b>{_fmt_limit(p.users_limit)}</b>\n"
        f"📊 Аналитика: <b>{'вкл' if p.analytics_enabled else 'выкл'}</b>\n"
        f"Статус: <b>{'активен' if p.active else 'отключён'}</b>"
    )


def _list_kb(plans: list[Plan]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in plans:
        icon = "✅" if p.active else "🔒"
        price = p.price if p.price is not None else p.price_monthly
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {p.name} — {price} {p.currency}",
                    callback_data=f"pl:open:{p.id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Создать тариф", callback_data="pl:create")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(p: Plan) -> InlineKeyboardMarkup:
    toggle_text = "🔒 Отключить" if p.active else "✅ Включить"
    analytics_text = (
        "📊 Аналитика: выкл" if p.analytics_enabled else "📊 Аналитика: вкл"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏ Редактировать", callback_data=f"pl:edit:{p.id}")],
            [
                InlineKeyboardButton(text=toggle_text, callback_data=f"pl:toggle:{p.id}"),
                InlineKeyboardButton(text=analytics_text, callback_data=f"pl:analytics:{p.id}"),
            ],
            [InlineKeyboardButton(text="« К списку", callback_data="pl:list")],
        ]
    )


def _edit_menu_kb(plan_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"✏ {label}", callback_data=f"pl:setf:{plan_id}:{field}")]
        for field, label in EDITABLE_FIELDS.items()
    ]
    rows.append(
        [InlineKeyboardButton(text="« Назад", callback_data=f"pl:open:{plan_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_card(message_target: Message, plan_id: int, *, edit: bool) -> bool:
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            return False
        text = _plan_card_text(plan)
        kb = _card_kb(plan)
    if edit:
        await message_target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_target.answer(text, parse_mode="HTML", reply_markup=kb)
    return True


# ---------- List ------------------------------------------------------------

@router.message(F.text == BTN_PLANS)
async def list_plans(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        plans = (await session.execute(select(Plan).order_by(Plan.id))).scalars().all()

    if not plans:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать тариф", callback_data="pl:create")]
            ]
        )
        await message.answer("📦 Тарифов пока нет.", reply_markup=kb)
        return

    await message.answer(
        f"📦 <b>Тарифы ({len(plans)})</b>\nВыбери тариф:",
        parse_mode="HTML",
        reply_markup=_list_kb(plans),
    )


@router.callback_query(F.data == "pl:list")
async def cb_list(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        plans = (await session.execute(select(Plan).order_by(Plan.id))).scalars().all()
    if not plans:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать тариф", callback_data="pl:create")]
            ]
        )
        await call.message.edit_text("📦 Тарифов пока нет.", reply_markup=kb)
    else:
        await call.message.edit_text(
            f"📦 <b>Тарифы ({len(plans)})</b>\nВыбери тариф:",
            parse_mode="HTML",
            reply_markup=_list_kb(plans),
        )
    await call.answer()


# ---------- Open card -------------------------------------------------------

@router.callback_query(F.data.startswith("pl:open:"))
async def cb_open(call: CallbackQuery) -> None:
    try:
        plan_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    ok = await _render_card(call.message, plan_id, edit=True)
    if not ok:
        await call.answer("Тариф не найден", show_alert=True)
        return
    await call.answer()


# ---------- Toggle active / analytics ---------------------------------------

@router.callback_query(F.data.startswith("pl:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    try:
        plan_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            await call.answer("Тариф не найден", show_alert=True)
            return
        plan.active = not plan.active
        await session.commit()
    await call.answer("Готово")
    await _render_card(call.message, plan_id, edit=True)


@router.callback_query(F.data.startswith("pl:analytics:"))
async def cb_analytics(call: CallbackQuery) -> None:
    try:
        plan_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            await call.answer("Тариф не найден", show_alert=True)
            return
        plan.analytics_enabled = not plan.analytics_enabled
        await session.commit()
    await call.answer("Готово")
    await _render_card(call.message, plan_id, edit=True)


# ---------- Edit ------------------------------------------------------------

@router.callback_query(F.data.startswith("pl:edit:"))
async def cb_edit_menu(call: CallbackQuery) -> None:
    try:
        plan_id = int(call.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await call.answer("bad id", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            await call.answer("Тариф не найден", show_alert=True)
            return
    await call.message.edit_text(
        f"✏ <b>Редактирование:</b> {plan.name}\nВыбери поле:",
        parse_mode="HTML",
        reply_markup=_edit_menu_kb(plan_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("pl:setf:"))
async def cb_set_field(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    if len(parts) != 4:
        await call.answer("bad data", show_alert=True)
        return
    try:
        plan_id = int(parts[2])
    except ValueError:
        await call.answer("bad id", show_alert=True)
        return
    field = parts[3]
    if field not in EDITABLE_FIELDS:
        await call.answer("unknown field", show_alert=True)
        return

    await state.clear()
    await state.update_data(plan_id=plan_id, field=field)
    await state.set_state(EditPlan.waiting_value)

    hint = ""
    if field in {"products_limit", "images_per_product_limit", "domains_limit", "users_limit"}:
        hint = f"\nЦелое число ≥ 0. Отправь «{SKIP}» — без ограничения."
    elif field == "price":
        hint = "\nЧисло, например 15 или 19.90."
    elif field == "currency":
        hint = "\nНапример: USD, EUR, UAH."
    elif field == "slug":
        hint = "\nЛатиница, цифры, дефисы. Например: start."

    await call.message.answer(
        f"Введи новое значение для <b>{EDITABLE_FIELDS[field]}</b>.{hint}\n"
        f"Для отмены /cancel",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(StateFilter(EditPlan), Command("cancel"))
async def edit_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Редактирование отменено.", reply_markup=admin_main_menu())


@router.message(EditPlan.waiting_value, F.text)
async def edit_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan_id = data.get("plan_id")
    field = data.get("field")
    raw = (message.text or "").strip()

    if plan_id is None or field not in EDITABLE_FIELDS:
        await state.clear()
        await message.answer("Состояние сброшено.", reply_markup=admin_main_menu())
        return

    # Parse value per field
    try:
        value = await _parse_field_value(field, raw, plan_id)
    except ValueError as e:
        await message.answer(f"❌ {e}\nПовтори или /cancel.")
        return

    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            await state.clear()
            await message.answer("Тариф не найден.", reply_markup=admin_main_menu())
            return
        setattr(plan, field, value)
        # Keep legacy column in sync when price changes
        if field == "price" and value is not None:
            plan.price_monthly = value
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ {EDITABLE_FIELDS[field]} обновлено.", reply_markup=admin_main_menu()
    )
    await _render_card(message, plan_id, edit=False)


async def _parse_field_value(field: str, raw: str, plan_id: int):
    if field == "name":
        if not raw or len(raw) > 128:
            raise ValueError("Название от 1 до 128 символов.")
        async with AsyncSessionLocal() as s:
            taken = await s.scalar(
                select(Plan.id).where(Plan.name == raw, Plan.id != plan_id)
            )
        if taken:
            raise ValueError("Такое название уже занято.")
        return raw

    if field == "slug":
        slug = _slugify(raw)
        if not slug or len(slug) > 64:
            raise ValueError("Некорректный slug.")
        async with AsyncSessionLocal() as s:
            taken = await s.scalar(
                select(Plan.id).where(Plan.slug == slug, Plan.id != plan_id)
            )
        if taken:
            raise ValueError("Такой slug уже занят.")
        return slug

    if field == "price":
        try:
            v = Decimal(raw.replace(",", "."))
        except (InvalidOperation, ValueError):
            raise ValueError("Цена должна быть числом.")
        if v < 0:
            raise ValueError("Цена не может быть отрицательной.")
        return v

    if field == "currency":
        c = raw.upper()
        if not (1 <= len(c) <= 8) or not c.isalpha():
            raise ValueError("Валюта — 1-8 латинских букв.")
        return c

    if field in {"products_limit", "images_per_product_limit", "domains_limit", "users_limit"}:
        if raw == SKIP:
            return None
        try:
            n = int(raw)
        except ValueError:
            raise ValueError("Нужно целое число.")
        if n < 0:
            raise ValueError("Должно быть ≥ 0.")
        return n

    raise ValueError("Неизвестное поле.")


# ---------- Create ----------------------------------------------------------

@router.callback_query(F.data == "pl:create")
async def cb_create_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CreatePlan.name)
    await call.message.answer(
        "Шаг 1/9. Введи название тарифа.\nДля отмены /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )
    await call.answer()


@router.message(F.text == BTN_CREATE_PLAN)
async def msg_create_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CreatePlan.name)
    await message.answer(
        "Шаг 1/9. Введи название тарифа.\nДля отмены /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(StateFilter(CreatePlan), Command("cancel"))
async def create_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Создание тарифа отменено.", reply_markup=admin_main_menu())


@router.message(CreatePlan.name, F.text)
async def cp_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        await message.answer("Название от 1 до 128 символов. Повтори.")
        return
    async with AsyncSessionLocal() as s:
        if await s.scalar(select(Plan.id).where(Plan.name == name)):
            await message.answer("Такое название уже занято. Повтори.")
            return

    suggested = _slugify(name)
    await state.update_data(name=name, suggested_slug=suggested)
    await state.set_state(CreatePlan.slug)
    await message.answer(
        f"Шаг 2/9. Slug (латиница/цифры/дефисы).\n"
        f"Предложение: <code>{suggested}</code>. Отправь «{SKIP}» чтобы принять.",
        parse_mode="HTML",
    )


@router.message(CreatePlan.slug, F.text)
async def cp_slug(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    slug = data["suggested_slug"] if raw == SKIP else _slugify(raw)
    if not slug or len(slug) > 64:
        await message.answer("Некорректный slug. Повтори.")
        return
    async with AsyncSessionLocal() as s:
        if await s.scalar(select(Plan.id).where(Plan.slug == slug)):
            await message.answer("Такой slug уже занят. Повтори.")
            return
    await state.update_data(slug=slug)
    await state.set_state(CreatePlan.price)
    await message.answer("Шаг 3/9. Цена (например 15 или 19.90).")


@router.message(CreatePlan.price, F.text)
async def cp_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("Цена должна быть числом. Повтори.")
        return
    if price < 0:
        await message.answer("Цена не может быть отрицательной. Повтори.")
        return
    await state.update_data(price=str(price))
    await state.set_state(CreatePlan.currency)
    await message.answer("Шаг 4/9. Валюта (USD/EUR/UAH). Отправь «-» для USD.")


@router.message(CreatePlan.currency, F.text)
async def cp_currency(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    cur = "USD" if raw == SKIP else raw.upper()
    if not cur.isalpha() or not (1 <= len(cur) <= 8):
        await message.answer("Валюта — 1-8 латинских букв. Повтори.")
        return
    await state.update_data(currency=cur)
    await state.set_state(CreatePlan.products_limit)
    await message.answer(
        f"Шаг 5/9. Лимит товаров (целое ≥ 0). «{SKIP}» — без ограничения."
    )


async def _step_int(message: Message, state: FSMContext, key: str, next_state, prompt: str):
    raw = (message.text or "").strip()
    if raw == SKIP:
        value = None
    else:
        try:
            value = int(raw)
        except ValueError:
            await message.answer("Нужно целое число. Повтори.")
            return
        if value < 0:
            await message.answer("Должно быть ≥ 0. Повтори.")
            return
    await state.update_data(**{key: value})
    await state.set_state(next_state)
    await message.answer(prompt)


@router.message(CreatePlan.products_limit, F.text)
async def cp_products_limit(message: Message, state: FSMContext) -> None:
    await _step_int(
        message,
        state,
        "products_limit",
        CreatePlan.images_per_product_limit,
        f"Шаг 6/9. Лимит фото на товар. «{SKIP}» — без ограничения.",
    )


@router.message(CreatePlan.images_per_product_limit, F.text)
async def cp_images_limit(message: Message, state: FSMContext) -> None:
    await _step_int(
        message,
        state,
        "images_per_product_limit",
        CreatePlan.domains_limit,
        f"Шаг 7/9. Лимит доменов. «{SKIP}» — без ограничения.",
    )


@router.message(CreatePlan.domains_limit, F.text)
async def cp_domains_limit(message: Message, state: FSMContext) -> None:
    await _step_int(
        message,
        state,
        "domains_limit",
        CreatePlan.users_limit,
        f"Шаг 8/9. Лимит юзеров. «{SKIP}» — без ограничения.",
    )


@router.message(CreatePlan.users_limit, F.text)
async def cp_users_limit(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == SKIP:
        value = None
    else:
        try:
            value = int(raw)
        except ValueError:
            await message.answer("Нужно целое число. Повтори.")
            return
        if value < 0:
            await message.answer("Должно быть ≥ 0. Повтори.")
            return
    await state.update_data(users_limit=value)
    await state.set_state(CreatePlan.analytics_enabled)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="cp:an:yes"),
                InlineKeyboardButton(text="Нет", callback_data="cp:an:no"),
            ]
        ]
    )
    await message.answer("Шаг 9/9. Аналитика включена?", reply_markup=kb)


@router.callback_query(CreatePlan.analytics_enabled, F.data.startswith("cp:an:"))
async def cp_analytics(call: CallbackQuery, state: FSMContext) -> None:
    choice = call.data.split(":")[2]
    analytics = choice == "yes"
    await state.update_data(analytics_enabled=analytics)
    await call.message.edit_reply_markup(reply_markup=None)
    await _save_new_plan(call.message, state)
    await call.answer("Готово")


async def _save_new_plan(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        # Re-check uniqueness right before insert
        if await session.scalar(select(Plan.id).where(Plan.name == data["name"])):
            await state.clear()
            await message.answer(
                f"Тариф «{data['name']}» уже существует.",
                reply_markup=admin_main_menu(),
            )
            return
        if await session.scalar(select(Plan.id).where(Plan.slug == data["slug"])):
            await state.clear()
            await message.answer(
                f"Slug «{data['slug']}» уже занят.",
                reply_markup=admin_main_menu(),
            )
            return

        price = Decimal(data["price"])
        plan = Plan(
            name=data["name"],
            slug=data["slug"],
            price=price,
            price_monthly=price,  # legacy column kept in sync
            currency=data["currency"],
            products_limit=data.get("products_limit"),
            images_per_product_limit=data.get("images_per_product_limit"),
            domains_limit=data.get("domains_limit"),
            users_limit=data.get("users_limit"),
            analytics_enabled=bool(data.get("analytics_enabled", False)),
            active=True,
            can_buyout=False,
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

    await state.clear()
    await message.answer(
        "✅ Тариф создан.", reply_markup=admin_main_menu()
    )
    await _render_card(message, plan.id, edit=False)
