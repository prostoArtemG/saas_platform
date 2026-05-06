from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

from app.bot.filters import AdminFilter
from app.bot.keyboards import BTN_CREATE_PLAN, admin_main_menu
from app.db import AsyncSessionLocal
from app.models import Plan

router = Router(name="create_plan")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


class CreatePlan(StatesGroup):
    name = State()
    price = State()
    can_buyout = State()
    buyout_months = State()


def _cancel_hint() -> str:
    return "Для отмены отправь /cancel"


@router.message(StateFilter(CreatePlan), Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Создание тарифа отменено.", reply_markup=admin_main_menu())


@router.message(F.text == BTN_CREATE_PLAN)
async def start_create(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CreatePlan.name)
    await message.answer(
        f"Шаг 1/4. Введи название тарифа.\n{_cancel_hint()}",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(CreatePlan.name, F.text)
async def step_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        await message.answer("Название должно быть от 1 до 128 символов. Повтори.")
        return

    async with AsyncSessionLocal() as session:
        exists = await session.scalar(select(Plan.id).where(Plan.name == name))
    if exists:
        await message.answer(f"Тариф «{name}» уже существует. Введи другое название.")
        return

    await state.update_data(name=name)
    await state.set_state(CreatePlan.price)
    await message.answer("Шаг 2/4. Введи цену в месяц (например: 499 или 1990.00).")


@router.message(CreatePlan.price, F.text)
async def step_price(message: Message, state: FSMContext) -> None:
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
    await state.set_state(CreatePlan.can_buyout)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="cp:buyout:yes"),
                InlineKeyboardButton(text="Нет", callback_data="cp:buyout:no"),
            ]
        ]
    )
    await message.answer("Шаг 3/4. Возможен выкуп (can_buyout)?", reply_markup=kb)


@router.callback_query(CreatePlan.can_buyout, F.data.startswith("cp:buyout:"))
async def step_can_buyout(cb: CallbackQuery, state: FSMContext) -> None:
    choice = cb.data.split(":")[2]
    can_buyout = choice == "yes"
    await state.update_data(can_buyout=can_buyout)

    await cb.message.edit_reply_markup(reply_markup=None)

    if can_buyout:
        await state.set_state(CreatePlan.buyout_months)
        await cb.message.answer("Шаг 4/4. Введи количество месяцев выкупа (целое число).")
        await cb.answer()
    else:
        await state.update_data(buyout_months=None)
        await _save_plan(cb.message, state)
        await cb.answer("Готово")


@router.message(CreatePlan.buyout_months, F.text)
async def step_buyout_months(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        months = int(raw)
    except ValueError:
        await message.answer("Это должно быть целое число. Повтори.")
        return
    if months <= 0:
        await message.answer("Месяцев должно быть больше 0. Повтори.")
        return

    await state.update_data(buyout_months=months)
    await _save_plan(message, state)


async def _save_plan(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

    async with AsyncSessionLocal() as session:
        # Re-check uniqueness right before insert.
        taken = await session.scalar(select(Plan.id).where(Plan.name == data["name"]))
        if taken:
            await state.clear()
            await message.answer(
                f"Тариф «{data['name']}» уже существует. Создание отменено.",
                reply_markup=admin_main_menu(),
            )
            return

        plan = Plan(
            name=data["name"],
            price_monthly=Decimal(data["price"]),
            can_buyout=bool(data["can_buyout"]),
            buyout_months=data.get("buyout_months"),
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

    await state.clear()
    buyout = "да" if plan.can_buyout else "нет"
    months_line = (
        f"\n• Месяцев выкупа: <b>{plan.buyout_months}</b>"
        if plan.can_buyout and plan.buyout_months
        else ""
    )
    await message.answer(
        "✅ <b>Тариф создан</b>\n"
        f"• ID: <code>{plan.id}</code>\n"
        f"• Название: <b>{plan.name}</b>\n"
        f"• Цена/мес: <b>{plan.price_monthly}</b>\n"
        f"• Выкуп: <b>{buyout}</b>"
        f"{months_line}",
        parse_mode="HTML",
        reply_markup=admin_main_menu(),
    )
