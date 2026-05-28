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
import os
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import Bot, F, Router
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
    client_test_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, Product

logger = logging.getLogger(__name__)

router = Router(name="client_cms")
router.message.filter(ClientFilter())
router.callback_query.filter(ClientFilter())


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_client(user_id: int) -> Client | None:
    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(Client).where(Client.admin_telegram_id == user_id)
        )


async def _get_effective_client(user_id: int, state: FSMContext) -> Client | None:
    """Return the client for this user.

    If the user is a platform admin in test mode (``selected_client_id`` set in
    FSM state), return that client directly.  Otherwise fall back to the normal
    lookup by admin_telegram_id.
    """
    from app.config import settings as app_settings
    data = await state.get_data()
    selected_id = data.get("selected_client_id")
    if selected_id is not None and user_id in app_settings.admin_ids:
        async with AsyncSessionLocal() as session:
            return await session.get(Client, selected_id)
    return await _get_client(user_id)


async def _clear_fsm_keep_test(state: FSMContext) -> None:
    """Clear FSM state but preserve ``selected_client_id`` for admin test mode."""
    data = await state.get_data()
    selected_id = data.get("selected_client_id")
    await state.clear()
    if selected_id is not None:
        await state.update_data(selected_client_id=selected_id)


async def _upload_to_cloudinary(bot: Bot, file_id: str) -> str | None:
    """Download a Telegram file and upload to Cloudinary. Returns secure_url or None."""
    from app.config import settings as app_settings  # avoid circular at module level
    if not (
        app_settings.cloudinary_cloud_name
        and app_settings.cloudinary_api_key
        and app_settings.cloudinary_api_secret
    ):
        return None
    try:
        import cloudinary  # type: ignore[import]
        import cloudinary.uploader  # type: ignore[import]
        cloudinary.config(
            cloud_name=app_settings.cloudinary_cloud_name,
            api_key=app_settings.cloudinary_api_key,
            api_secret=app_settings.cloudinary_api_secret,
            secure=True,
        )
        tg_file = await bot.get_file(file_id)
        tmp = f"/tmp/{uuid4()}.jpg"
        try:
            await bot.download_file(tg_file.file_path, tmp)  # type: ignore[arg-type]
            result = cloudinary.uploader.upload(tmp, folder="shop_products")
            return result["secure_url"]
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception as exc:
        logger.error("Cloudinary upload failed: %s", exc)
        return None


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


def _groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=g, callback_data=f"cms:group:pick:{i}")]
        for i, g in enumerate(groups)
    ]
    rows.append([
        InlineKeyboardButton(text="✏️ Нова група", callback_data="cms:group:new"),
        InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:group:skip"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _categories_kb(cats: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=c, callback_data=f"cms:cat:pick:{i}")]
        for i, c in enumerate(cats)
    ]
    rows.append([InlineKeyboardButton(text="✏️ Нова категорія", callback_data="cms:cat:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _brands_kb(brands: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=b, callback_data=f"cms:brand:pick:{i}")]
        for i, b in enumerate(brands)
    ]
    rows.append([
        InlineKeyboardButton(text="✏️ Новий бренд", callback_data="cms:brand:new"),
        InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:brand:skip"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Пропустити", callback_data=f"cms:skip:{field}")
        ]]
    )


# ── FSM states ────────────────────────────────────────────────────────────────────────────────

class CmsAddProduct(StatesGroup):
    group          = State()  # inline KB: pick existing, new, or skip
    group_input    = State()  # text input: new group name
    category       = State()  # inline KB: pick existing or enter new
    category_input = State()  # text input: new category name
    brand          = State()  # inline KB: pick existing, new, or skip
    brand_input    = State()  # text input: new brand name
    name           = State()  # text input: model / product name
    specs          = State()  # text input or skip: tech specs
    price          = State()  # text input: price
    old_price      = State()  # text input or skip: original price (for discount display)
    image_url      = State()  # text input or skip: photo URL → saves product


# ── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def client_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
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
    await _clear_fsm_keep_test(state)
    data_after = await state.get_data()
    menu = client_test_menu() if data_after.get("selected_client_id") else client_main_menu()
    await message.answer("Додавання скасовано.", reply_markup=menu)


# ── 📦 Товары ─────────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_PRODUCTS)
async def cms_products(message: Message, state: FSMContext) -> None:
    await _clear_fsm_keep_test(state)
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
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
            brand = f" [{p.brand}]" if p.brand else ""
            lines.append(
                f"{mark} #{p.id} <b>{p.name}</b>{brand} — {p.price} грн{cat}"
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
async def cms_site(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
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
async def cms_settings(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
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
        await cb.answer("Помилка", show_alert=True)
        return

    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None or client.id != client_id:
        await cb.answer("Немає доступу", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        groups = list(
            await session.scalars(
                select(Product.group_name)
                .where(Product.client_id == client_id)
                .where(Product.group_name.isnot(None))
                .distinct()
                .order_by(Product.group_name)
            )
        )

    await state.update_data(client_id=client_id, possible_groups=groups)
    await state.set_state(CmsAddProduct.group)
    await cb.message.answer(  # type: ignore[union-attr]
        "📦 <b>Новий товар</b>\n\n"
        "Крок 1 — Виберіть або введіть групу товарів:\n"
        "<i>(відправ /cancel для скасування)</i>",
        parse_mode="HTML",
        reply_markup=_groups_kb(groups),
    )
    await cb.answer()


# ── FSM helpers: go to next step ─────────────────────────────────────────────

async def _go_to_category(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    client_id: int = data["client_id"]
    async with AsyncSessionLocal() as session:
        cats = list(await session.scalars(
            select(Product.category)
            .where(Product.client_id == client_id)
            .where(Product.category.isnot(None))
            .distinct().order_by(Product.category)
        ))
    await state.update_data(possible_categories=cats)
    await state.set_state(CmsAddProduct.category)
    await msg.answer(
        "Крок 2 — Виберіть або введіть категорію:",
        reply_markup=_categories_kb(cats),
    )


async def _go_to_brand(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    client_id: int = data["client_id"]
    async with AsyncSessionLocal() as session:
        brands = list(await session.scalars(
            select(Product.brand)
            .where(Product.client_id == client_id)
            .where(Product.brand.isnot(None))
            .distinct().order_by(Product.brand)
        ))
    await state.update_data(possible_brands=brands)
    await state.set_state(CmsAddProduct.brand)
    await msg.answer(
        "Крок 3 — Виберіть або введіть бренд:",
        reply_markup=_brands_kb(brands),
    )


# ── group state ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:group:pick:"), StateFilter(CmsAddProduct.group))
async def cms_group_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    groups = data.get("possible_groups", [])
    group = groups[idx] if 0 <= idx < len(groups) else None
    await state.update_data(group_name=group)
    await _go_to_category(cb.message, state)  # type: ignore[arg-type]
    await cb.answer()


@router.callback_query(F.data == "cms:group:new", StateFilter(CmsAddProduct.group))
async def cms_group_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.group_input)
    await cb.message.answer("Введіть нову групу товарів:")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data == "cms:group:skip", StateFilter(CmsAddProduct.group))
async def cms_group_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(group_name=None)
    await _go_to_category(cb.message, state)  # type: ignore[arg-type]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.group))
async def cms_group_typed(message: Message, state: FSMContext) -> None:
    group = (message.text or "").strip()
    await state.update_data(group_name=group or None)
    await _go_to_category(message, state)


@router.message(StateFilter(CmsAddProduct.group_input))
async def cms_group_input(message: Message, state: FSMContext) -> None:
    group = (message.text or "").strip()
    await state.update_data(group_name=group or None)
    await _go_to_category(message, state)


# ── category state ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:cat:pick:"), StateFilter(CmsAddProduct.category))
async def cms_cat_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    cats = data.get("possible_categories", [])
    cat = cats[idx] if 0 <= idx < len(cats) else None
    await state.update_data(category=cat)
    await _go_to_brand(cb.message, state)  # type: ignore[arg-type]
    await cb.answer()


@router.callback_query(F.data == "cms:cat:new", StateFilter(CmsAddProduct.category))
async def cms_cat_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.category_input)
    await cb.message.answer("Введіть нову категорію:")  # type: ignore[union-attr]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.category))
async def cms_cat_typed(message: Message, state: FSMContext) -> None:
    cat = (message.text or "").strip()
    await state.update_data(category=cat or None)
    await _go_to_brand(message, state)


@router.message(StateFilter(CmsAddProduct.category_input))
async def cms_cat_input(message: Message, state: FSMContext) -> None:
    cat = (message.text or "").strip()
    await state.update_data(category=cat or None)
    await _go_to_brand(message, state)


# ── name state ────────────────────────────────────────────────────────────────

@router.message(StateFilter(CmsAddProduct.name))
async def cms_add_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Назва не може бути порожньою. Введіть назву:")
        return
    await state.update_data(name=text)
    await state.set_state(CmsAddProduct.specs)
    await message.answer(
        "Крок 5 — Характеристики товару:",
        reply_markup=_skip_kb("specs"),
    )


# ── brand state ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:brand:pick:"), StateFilter(CmsAddProduct.brand))
async def cms_brand_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    brands = data.get("possible_brands", [])
    brand = brands[idx] if 0 <= idx < len(brands) else None
    await state.update_data(brand=brand)
    await state.set_state(CmsAddProduct.name)
    await cb.message.answer("Крок 4 — Введіть модель / назву товару:")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data == "cms:brand:new", StateFilter(CmsAddProduct.brand))
async def cms_brand_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.brand_input)
    await cb.message.answer("Введіть новий бренд:")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data == "cms:brand:skip", StateFilter(CmsAddProduct.brand))
async def cms_brand_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(brand=None)
    await state.set_state(CmsAddProduct.name)
    await cb.message.answer("Крок 4 — Введіть модель / назву товару:")  # type: ignore[union-attr]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.brand))
async def cms_brand_typed(message: Message, state: FSMContext) -> None:
    brand = (message.text or "").strip()
    await state.update_data(brand=brand or None)
    await state.set_state(CmsAddProduct.name)
    await message.answer("Крок 4 — Введіть модель / назву товару:")


@router.message(StateFilter(CmsAddProduct.brand_input))
async def cms_brand_input(message: Message, state: FSMContext) -> None:
    brand = (message.text or "").strip()
    await state.update_data(brand=brand or None)
    await state.set_state(CmsAddProduct.name)
    await message.answer("Крок 4 — Введіть модель / назву товару:")


# ── specs state ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cms:skip:specs", StateFilter(CmsAddProduct.specs))
async def cms_skip_specs(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(specs=None)
    await state.set_state(CmsAddProduct.price)
    await cb.message.answer("Крок 6 — Ціна (наприклад: 150):")  # type: ignore[union-attr]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.specs))
async def cms_add_specs(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(specs=val or None)
    await state.set_state(CmsAddProduct.price)
    await message.answer("Крок 6 — Ціна (наприклад: 150):")


# ── price state ───────────────────────────────────────────────────────────────

@router.message(StateFilter(CmsAddProduct.price))
async def cms_add_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw)
        if price < 0:
            raise ValueError("negative price")
    except (InvalidOperation, ValueError):
        await message.answer("Некоректна ціна. Введіть число (наприклад: 150):")
        return
    await state.update_data(price=str(price))
    await state.set_state(CmsAddProduct.old_price)
    await message.answer(
        "Крок 7 — Стара ціна (для відображення знижки):",
        reply_markup=_skip_kb("old_price"),
    )


# ── old_price state ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "cms:skip:old_price", StateFilter(CmsAddProduct.old_price))
async def cms_skip_old_price(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(old_price=None)
    await state.set_state(CmsAddProduct.image_url)
    await cb.message.answer(  # type: ignore[union-attr]
        "Крок 8 — Надішліть фото товару, URL посилання або ‘-’ щоб пропустити:",
        reply_markup=_skip_kb("image_url"),
    )
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.old_price))
async def cms_add_old_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        old_price = Decimal(raw)
        if old_price < 0:
            raise ValueError("negative")
    except (InvalidOperation, ValueError):
        await message.answer("Некоректна ціна. Введіть число або натисніть «Пропустити»:")
        return
    await state.update_data(old_price=str(old_price))
    await state.set_state(CmsAddProduct.image_url)
    await message.answer(
        "Крок 8 — Надішліть фото товару, URL посилання або ‘-’ щоб пропустити:",
        reply_markup=_skip_kb("image_url"),
    )


# ── image_url state & save ────────────────────────────────────────────────────

@router.callback_query(F.data == "cms:skip:image_url", StateFilter(CmsAddProduct.image_url))
async def cms_skip_image_url(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(image_url=None)
    await _do_save_product(cb.message, state)  # type: ignore[arg-type]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.image_url), F.photo)
async def cms_add_photo(message: Message, state: FSMContext) -> None:
    """User sent a photo directly from Telegram — try Cloudinary upload."""
    from app.config import settings as app_settings
    if not (
        app_settings.cloudinary_cloud_name
        and app_settings.cloudinary_api_key
        and app_settings.cloudinary_api_secret
    ):
        await message.answer(
            "📷 Cloudinary не налаштований.\n"
            "Надішліть посилання (URL) на фото товару або натисніть «Пропустити»:",
            reply_markup=_skip_kb("image_url"),
        )
        return
    photo = message.photo[-1]  # largest available size
    url = await _upload_to_cloudinary(message.bot, photo.file_id)  # type: ignore[arg-type]
    if url:
        await state.update_data(image_url=url)
    else:
        await message.answer(
            "⚠️ Не вдалось завантажити фото. Спробуйте надіслати URL або пропустіть:",
            reply_markup=_skip_kb("image_url"),
        )
        return
    await _do_save_product(message, state)


@router.message(StateFilter(CmsAddProduct.image_url))
async def cms_add_image_url(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(image_url=val or None)
    await _do_save_product(message, state)


async def _do_save_product(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    client_id: int = data["client_id"]
    old_price_val = Decimal(data["old_price"]) if data.get("old_price") else None

    async with AsyncSessionLocal() as session:
        product = Product(
            client_id=client_id,
            name=data["name"],
            group_name=data.get("group_name"),
            category=data.get("category"),
            brand=data.get("brand"),
            specs=data.get("specs"),
            price=Decimal(data["price"]),
            old_price=old_price_val,
            image_url=data.get("image_url"),
            is_available=True,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

    await _clear_fsm_keep_test(state)
    data_after = await state.get_data()
    menu = client_test_menu() if data_after.get("selected_client_id") else client_main_menu()
    group_label = f" [{data['group_name']}]" if data.get("group_name") else ""
    cat_label = f" · {data['category']}" if data.get("category") else ""
    brand_label = f" [{data['brand']}]" if data.get("brand") else ""
    old_price_label = f" (знижка з {data['old_price']} грн)" if data.get("old_price") else ""
    await message.answer(
        f"✅ Товар <b>{data['name']}</b>{brand_label} додано!{group_label}{cat_label}\n"
        f"Ціна: {data['price']} грн{old_price_label}",
        parse_mode="HTML",
        reply_markup=menu,
    )
