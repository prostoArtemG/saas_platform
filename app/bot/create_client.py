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
from app.bot.keyboards import BTN_CREATE_CLIENT, admin_main_menu
from app.db import AsyncSessionLocal
from app.models import Client, Plan, Subscription
from app.services.onboarding import TRIAL_DAYS, onboard_client

router = Router(name="create_client")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


SLUG_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class CreateClient(StatesGroup):
    business_name = State()
    slug = State()
    bot_token = State()
    admin_tg_id = State()
    plan = State()
    template = State()


def _cancel_hint() -> str:
    return "Для отмены отправь /cancel"


@router.message(StateFilter(CreateClient), Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Создание клиента отменено.", reply_markup=admin_main_menu())


@router.message(F.text == BTN_CREATE_CLIENT)
async def start_create(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CreateClient.business_name)
    await message.answer(
        f"Шаг 1/5. Введи название бизнеса.\n{_cancel_hint()}",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(CreateClient.business_name, F.text)
async def step_business_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 255:
        await message.answer("Название должно быть от 1 до 255 символов. Повтори.")
        return
    await state.update_data(business_name=name)
    await state.set_state(CreateClient.slug)
    await message.answer(
        "Шаг 2/5. Введи slug (латиница, цифры, `_`, `-`), например: <code>technovlada</code>",
        parse_mode="HTML",
    )


@router.message(CreateClient.slug, F.text)
async def step_slug(message: Message, state: FSMContext) -> None:
    slug = (message.text or "").strip().lower()
    if not SLUG_RE.match(slug):
        await message.answer(
            "Slug должен быть 2–63 символа: a–z, 0–9, `_`, `-`, начинаться с буквы/цифры. Повтори."
        )
        return

    async with AsyncSessionLocal() as session:
        exists = await session.scalar(select(Client.id).where(Client.slug == slug))
    if exists:
        await message.answer(f"Slug <code>{slug}</code> уже занят. Введи другой.", parse_mode="HTML")
        return

    await state.update_data(slug=slug)
    await state.set_state(CreateClient.bot_token)
    await message.answer("Шаг 3/5. Введи telegram_bot_token клиента.")


@router.message(CreateClient.bot_token, F.text)
async def step_bot_token(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    if ":" not in token or len(token) < 20:
        await message.answer("Похоже на некорректный токен. Повтори.")
        return
    creator_id = message.from_user.id
    await state.update_data(bot_token=token, creator_id=creator_id)
    await state.set_state(CreateClient.admin_tg_id)
    await message.answer(
        f"Шаг 4/5. Введи admin_telegram_id клиента (число).\n\n"
        f"Или используй свой ID:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"👤 Мой ID: {creator_id}",
                callback_data=f"create:use_my_id:{creator_id}",
            ),
        ]]),
    )


@router.message(CreateClient.admin_tg_id, F.text)
async def step_admin_tg_id(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        admin_id = int(raw)
    except ValueError:
        await message.answer("Это должно быть число. Повтори.")
        return
    await _proceed_after_admin_id(message, state, admin_id)


@router.callback_query(F.data.startswith("create:use_my_id:"), StateFilter(CreateClient.admin_tg_id))
async def use_my_id(cb: CallbackQuery, state: FSMContext) -> None:
    """Admin taps the ‘My ID’ button — use their own Telegram ID as client’s admin."""
    await cb.answer()
    try:
        admin_id = int(cb.data.split(":")[-1])
    except ValueError:
        return
    await _proceed_after_admin_id(cb.message, state, admin_id)


async def _proceed_after_admin_id(msg: Message, state: FSMContext, admin_id: int) -> None:
    """Shared logic after admin_tg_id is resolved: save and move to plan."""
    await state.update_data(admin_tg_id=admin_id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Plan).order_by(Plan.id))
        plans = result.scalars().all()

    if not plans:
        await state.clear()
        await msg.answer(
            "В системе нет тарифов. Сначала добавь хотя бы один тариф в таблицу plans.",
            reply_markup=admin_main_menu(),
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{p.name} — {p.price_monthly}/мес",
                    callback_data=f"cc:plan:{p.id}",
                )
            ]
            for p in plans
        ]
    )
    await state.set_state(CreateClient.plan)
    await msg.answer("Шаг 5/5. Выбери тариф:", reply_markup=kb)


@router.callback_query(CreateClient.plan, F.data.startswith("cc:plan:"))
async def step_plan(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        plan_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный тариф", show_alert=True)
        return

    await state.update_data(plan_id=plan_id)
    await state.set_state(CreateClient.template)

    templates_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🛍 ТехноМаркет (магазин техніки)",
            callback_data="cc:tmpl:shop_bot"
        )],
        [InlineKeyboardButton(
            text="🌐 Technovlada (бізнес-сайт)",
            callback_data="cc:tmpl:technovlada"
        )],
    ])
    await cb.message.answer(
        "Шаг 6/6. Выбери шаблон сайта:",
        reply_markup=templates_kb
    )
    await cb.answer()


@router.callback_query(CreateClient.template, F.data.startswith("cc:tmpl:"))
async def step_template(cb: CallbackQuery, state: FSMContext) -> None:
    template_name = cb.data.split(":")[2]
    data = await state.get_data()
    plan_id = data["plan_id"]

    async with AsyncSessionLocal() as session:
        try:
            plan = await session.get(Plan, plan_id)
            if plan is None:
                await cb.answer("Тариф не найден", show_alert=True)
                return

            slug_taken = await session.scalar(
                select(Client.id).where(Client.slug == data["slug"])
            )
            if slug_taken:
                await state.clear()
                await cb.message.answer(
                    f"Slug <code>{data['slug']}</code> уже занят. Начни заново.",
                    parse_mode="HTML",
                    reply_markup=admin_main_menu(),
                )
                await cb.answer()
                return

            client = Client(
                business_name=data["business_name"],
                slug=data["slug"],
                telegram_bot_token=data["bot_token"],
                admin_telegram_id=data["admin_tg_id"],
                template_name=template_name,
                status="active",
            )
            session.add(client)
            await session.flush()

            result = await onboard_client(session, client, plan, trial_days=TRIAL_DAYS)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await state.clear()
            await cb.message.answer(
                f"❌ Не удалось создать клиента: <code>{exc}</code>",
                parse_mode="HTML",
                reply_markup=admin_main_menu(),
            )
            await cb.answer("Ошибка", show_alert=True)
            return

    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)

    template_labels = {
        "shop_bot": "🛍 ТехноМаркет",
        "technovlada": "🌐 Technovlada",
    }
    template_label = template_labels.get(template_name, template_name)

    def _lim(v):
        return "∞" if v is None else str(v)

    expires_str = result.trial_expires_at.strftime("%Y-%m-%d %H:%M UTC")
    await cb.message.answer(
        "✅ <b>Клиент создан!</b>\n\n"
        f"🏢 <b>{result.business_name}</b>\n"
        f"🔗 slug: <code>{result.slug}</code>\n"
        f"🎨 Шаблон: {template_label}\n"
        f"📦 Тариф: <b>{result.plan_name}</b>\n"
        f"⏳ Trial до: <b>{expires_str}</b>\n\n"
        f"<b>Лимиты:</b>\n"
        f"• 📦 Товары: {_lim(result.products_limit)}\n"
        f"• 🖼 Фото на товар: {_lim(result.images_per_product_limit)}\n"
        f"• 🌐 Домены: {_lim(result.domains_limit)}\n\n"
        f"🌐 Сайт: /site/{result.slug}",
        parse_mode="HTML",
        reply_markup=admin_main_menu(),
    )
    await cb.answer("Готово")
