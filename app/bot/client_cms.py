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
from sqlalchemy import func, or_, select

from app.bot.filters import CMSFilter
from app.bot.keyboards import (
    BTN_CMS_ORDERS,
    BTN_CMS_PRODUCTS,
    BTN_CMS_SETTINGS,
    BTN_CMS_SITE,
    client_main_menu,
    client_test_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, ClientSettings, Order, Product

logger = logging.getLogger(__name__)

router = Router(name="client_cms")
router.message.filter(CMSFilter())
router.callback_query.filter(CMSFilter())


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


THEMES: dict[str, str] = {
    "light_red":   "🔴 Червоне світло (світла)",
    "navy_teal":   "🌊 Темно-синя + бірюза",
    "purple_lime": "🟣 Фіолетова + лайм",
}
VALID_THEMES: frozenset[str] = frozenset(THEMES)


def _themes_kb(current: str | None) -> InlineKeyboardMarkup:
    current = current or "light_red"
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if key == current else "") + label,
                callback_data=f"cms:theme:{key}",
            )
        ]
        for key, label in THEMES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Settings helpers ─────────────────────────────────────────────────────────

SETTINGS_PROMPTS: dict[str, str] = {
    "shop_title":    "🏪 <b>Назва магазину</b>\n\nВведіть назву, яка буде відображатись в шапці сайту:",
    "phone":         "📞 <b>Телефон</b>\n\nВведіть контактний номер телефону:",
    "address":       "📍 <b>Адреса</b>\n\nВведіть адресу магазину:",
    "telegram_url":  "✈️ <b>Telegram</b>\n\nВведіть посилання на Telegram\n(наприклад: <code>https://t.me/myshop</code>):",
    "instagram_url": "📸 <b>Instagram</b>\n\nВведіть посилання на Instagram\n(наприклад: <code>https://instagram.com/myshop</code>):",
    "logo":          "🖼 <b>Логотип</b>\n\nНадішліть фото логотипу або URL посилання на зображення:",
}
VALID_SETTINGS_FIELDS: frozenset[str] = frozenset(SETTINGS_PROMPTS)
URL_SETTINGS_FIELDS: frozenset[str] = frozenset({"telegram_url", "instagram_url"})
FIELD_ATTR: dict[str, str] = {
    "shop_title":    "shop_title",
    "phone":         "phone",
    "address":       "address",
    "telegram_url":  "telegram_url",
    "instagram_url": "instagram_url",
    "logo":          "logo_url",
}


def _settings_text(client: Client, cs: ClientSettings | None) -> str:
    def _v(val: str | None) -> str:
        return val if val else "<i>не вказано</i>"

    theme = (cs.theme_name if cs else None) or "light_red"
    return (
        f"⚙️ <b>Налаштування магазину</b>\n\n"
        f"🏪 Назва на сайті: <b>{_v(cs.shop_title if cs else None)}</b>\n"
        f"📞 Телефон: {_v(cs.phone if cs else None)}\n"
        f"📍 Адреса: {_v(cs.address if cs else None)}\n"
        f"✈️ Telegram: {_v(cs.telegram_url if cs else None)}\n"
        f"📸 Instagram: {_v(cs.instagram_url if cs else None)}\n"
        f"🖼 Логотип: {'✅ є' if (cs and cs.logo_url) else '<i>немає</i>'}\n"
        f"🎨 Тема: {THEMES.get(theme, theme)}\n\n"
        f"Натисніть кнопку, щоб змінити:"
    )


def _settings_overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Назва магазину",  callback_data="cms:set:shop_title")],
            [InlineKeyboardButton(text="📞 Телефон",          callback_data="cms:set:phone")],
            [InlineKeyboardButton(text="📍 Адреса",           callback_data="cms:set:address")],
            [InlineKeyboardButton(text="✈️ Telegram",         callback_data="cms:set:telegram_url")],
            [InlineKeyboardButton(text="📸 Instagram",        callback_data="cms:set:instagram_url")],
            [InlineKeyboardButton(text="🖼 Логотип",          callback_data="cms:set:logo")],
            [InlineKeyboardButton(text="🎨 Тема сайту",       callback_data="cms:set:theme")],
        ]
    )


def _cancel_input_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Очистити",  callback_data=f"cms:clr:{field}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cms:set:cancel"),
        ]]
    )


async def _save_settings_field(
    client_id: int, field: str, value: str | None
) -> ClientSettings:
    attr = FIELD_ATTR.get(field, field)
    async with AsyncSessionLocal() as session:
        cs = await session.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client_id)
        )
        if cs is None:
            cs = ClientSettings(client_id=client_id)
            session.add(cs)
        setattr(cs, attr, value)
        await session.commit()
        await session.refresh(cs)
        return cs


# ── Orders: constants & helpers ───────────────────────────────────────────────

ORDER_STATUS_LABELS: dict[str, str] = {
    "new":         "🆕 Нові",
    "in_progress": "🔄 В роботі",
    "done":        "✅ Виконані",
}
_ORDER_NEXT_STATUS: dict[str, str] = {"new": "in_progress", "in_progress": "done"}
_ORDER_BTN_LABEL:   dict[str, str] = {"new": "✅ В роботу",  "in_progress": "✅ Виконано"}


def _order_card(order: Order) -> str:
    import json as _json
    dt = order.created_at.strftime("%d.%m %H:%M") if order.created_at else "?"
    try:
        items = _json.loads(order.items_json or "[]")
        parts = [f"{i.get('name', '?')} × {i.get('qty', 1)}" for i in items[:3]]
        items_str = ", ".join(parts)
        if len(items) > 3:
            items_str += f" (+{len(items) - 3})"
    except Exception:
        items_str = "—"
    city_part = f" · 🏙 {order.customer_city}" if order.customer_city else ""
    comment_part = f"\n💬 {order.comment}" if order.comment else ""
    return (
        f"<b>#{order.id}</b> · {dt}\n"
        f"👤 {order.customer_name} · 📞 {order.customer_phone}{city_part}\n"
        f"📦 {items_str} · 💰 {int(order.total):,} грн{comment_part}"
    )


def _order_list_text(orders: list, status: str) -> str:
    label = ORDER_STATUS_LABELS.get(status, status)
    if not orders:
        return f"📋 <b>{label}</b>\n\nЗамовлень немає."
    parts = [f"📋 <b>{label}</b> ({len(orders)})"]
    for order in orders:
        parts.append("")
        parts.append(_order_card(order))
    if len(orders) >= 10:
        parts.append("\n<i>Показано перші 10</i>")
    return "\n".join(parts)


def _order_list_kb(orders: list, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    ns = _ORDER_NEXT_STATUS.get(status)
    lbl = _ORDER_BTN_LABEL.get(status)
    if ns and lbl:
        for order in orders:
            rows.append([InlineKeyboardButton(
                text=f"{lbl} #{order.id}",
                callback_data=f"cms:ord:status:{order.id}:{ns}",
            )])
    rows.append([InlineKeyboardButton(text="← Зведення", callback_data="cms:ord:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _order_counts(client_id: int) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as session:
        new_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.client_id == client_id, Order.status == "new")
        ) or 0
        ip_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.client_id == client_id, Order.status == "in_progress")
        ) or 0
        done_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.client_id == client_id, Order.status == "done")
        ) or 0
    return new_cnt, ip_cnt, done_cnt


def _order_summary_text(new: int, ip: int, done: int) -> str:
    return (
        f"📊 <b>Замовлення</b>\n\n"
        f"🆕 Нові: <b>{new}</b>\n"
        f"🔄 В роботі: <b>{ip}</b>\n"
        f"✅ Виконані: <b>{done}</b>"
    )


def _order_summary_kb(new: int, ip: int, done: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🆕 Нові ({new})",       callback_data="cms:ord:list:new"),
        InlineKeyboardButton(text=f"🔄 В роботі ({ip})",   callback_data="cms:ord:list:in_progress"),
        InlineKeyboardButton(text=f"✅ Виконані ({done})",  callback_data="cms:ord:list:done"),
    ]])


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


def _specs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово",     callback_data="cms:done:specs"),
            InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:skip:specs"),
        ]]
    )


def _specs_list_text(items: list) -> str:
    lines = "\n".join(f"• {it}" for it in items)
    return f"Поточні характеристики:\n{lines}\n\nДодайте ще або натисніть кнопку:"


# ── Products: paginated list helpers ─────────────────────────────────────────

PROD_PAGE_SIZE = 10

_PROD_EDIT_PROMPTS: dict[str, str] = {
    "name":       "✏️ Введіть нову назву/модель товару:",
    "brand":      "🏢 Введіть новий бренд (або «-» щоб очистити):",
    "category":   "📂 Введіть нову категорію (або «-» щоб очистити):",
    "group_name": "📁 Введіть нову групу (або «-» щоб очистити):",
    "price":      "💰 Введіть нову ціну (наприклад: 150):",
    "old_price":  "🏷 Введіть стару ціну (або «-» щоб очистити):",
    "specs":      "📋 Введіть нові характеристики (або «-» щоб очистити):",
}
_PROD_EDIT_VALID: frozenset[str] = frozenset(_PROD_EDIT_PROMPTS) | {"image"}


def _pfmt(price: object) -> str:
    """Format Decimal/float → '1 500' or '150'."""
    try:
        return f"{float(price):,.0f}".replace(",", "\u00a0")
    except Exception:
        return str(price)


async def _prod_page_data(client_id: int, page: int) -> tuple[list, int, int]:
    """Return (products_on_page, clamped_page, total_pages)."""
    async with AsyncSessionLocal() as session:
        total: int = await session.scalar(
            select(func.count(Product.id)).where(Product.client_id == client_id)
        ) or 0
        total_pages = max(1, (total + PROD_PAGE_SIZE - 1) // PROD_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        prods = list(await session.scalars(
            select(Product)
            .where(Product.client_id == client_id)
            .order_by(Product.id.desc())
            .offset(page * PROD_PAGE_SIZE)
            .limit(PROD_PAGE_SIZE)
        ))
    return prods, page, total_pages


def _prod_row_btn(p: "Product") -> str:
    brand = f"{p.brand} " if p.brand else ""
    flag = "✅" if p.is_available else "❌"
    return f"#{p.id} · {brand}{p.name} · {_pfmt(p.price)} грн · {flag}"


def _prod_list_text_header(page: int, total_pages: int, count: int, biz: str) -> str:
    if count == 0:
        return (
            f"📦 <b>{biz}</b> — Товари\n\n"
            "<i>Товарів поки немає. Додайте перший!</i>"
        )
    return f"📦 <b>{biz}</b> — Товари\nСторінка {page + 1} / {total_pages} · Показано {count}"


def _prod_list_kb(
    prods: list, page: int, total_pages: int, client_id: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in prods:
        rows.append([InlineKeyboardButton(
            text=_prod_row_btn(p),
            callback_data=f"cms:pv:{p.id}:{page}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"cms:pl:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="cms:noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"cms:pl:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="🔍 Пошук",  callback_data="cms:psearch"),
        InlineKeyboardButton(text="➕ Додати", callback_data=f"cms:prod:add:{client_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prod_card_text(p: "Product") -> str:
    def _v(val: object) -> str:
        s = str(val) if val is not None else ""
        return s if s else "<i>—</i>"

    lines = [f"<b>#{p.id} · {p.name}</b>", ""]
    lines.append(f"📁 Група:      {_v(p.group_name)}")
    lines.append(f"📂 Категорія:  {_v(p.category)}")
    lines.append(f"🏢 Бренд:      {_v(p.brand)}")
    lines.append(f"💰 Ціна:       <b>{_pfmt(p.price)} грн</b>")
    if p.old_price:
        lines.append(f"🏷 Стара ціна: {_pfmt(p.old_price)} грн")
    if p.specs:
        lines.append(f"\n📋 <b>Характеристики:</b>\n{p.specs}")
    lines.append("")
    lines.append(f"👁 Статус: {'✅ В наявності' if p.is_available else '❌ Прихований'}")
    lines.append(f"🖼 Фото:   {'✅ є' if p.image_url else '<i>немає</i>'}")
    return "\n".join(lines)


def _prod_card_kb(
    p: "Product", page: int = 0, site_url: str = ""
) -> InlineKeyboardMarkup:
    toggle_text = "👁 Приховати" if p.is_available else "👁 Показати"
    pid = p.id
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✏️ Назва/модель",   callback_data=f"cms:pe:{pid}:name:{page}"),
            InlineKeyboardButton(text="🏢 Бренд",          callback_data=f"cms:pe:{pid}:brand:{page}"),
        ],
        [
            InlineKeyboardButton(text="📂 Категорія",      callback_data=f"cms:pe:{pid}:category:{page}"),
            InlineKeyboardButton(text="📁 Група",          callback_data=f"cms:pe:{pid}:group_name:{page}"),
        ],
        [
            InlineKeyboardButton(text="💰 Ціна",           callback_data=f"cms:pe:{pid}:price:{page}"),
            InlineKeyboardButton(text="🏷 Стара ціна",     callback_data=f"cms:pe:{pid}:old_price:{page}"),
        ],
        [
            InlineKeyboardButton(text="📋 Характеристики", callback_data=f"cms:pe:{pid}:specs:{page}"),
            InlineKeyboardButton(text="🖼 Фото",           callback_data=f"cms:pe:{pid}:image:{page}"),
        ],
        [
            InlineKeyboardButton(text=toggle_text,         callback_data=f"cms:ptog:{pid}:{page}"),
            InlineKeyboardButton(text="🗑 Видалити товар", callback_data=f"cms:pdc:{pid}:{page}"),
        ],
    ]
    if site_url:
        rows.append([InlineKeyboardButton(text="🌐 Відкрити на сайті", url=site_url)])
    rows.append([InlineKeyboardButton(text="← Список", callback_data=f"cms:pl:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


class CmsSettings(StatesGroup):
    shop_title    = State()
    phone         = State()
    address       = State()
    telegram_url  = State()
    instagram_url = State()
    logo          = State()


class CmsEditProduct(StatesGroup):
    edit_field = State()   # text / numeric field
    edit_image = State()   # photo or URL


class CmsProductSearch(StatesGroup):
    query = State()


# ── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def client_start(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        return
    data = await state.get_data()
    in_test_mode = (
        user_id in app_settings.admin_ids
        and data.get("selected_client_id") is not None
    )
    await message.answer(
        f"👋 Привет, <b>{client.business_name}</b>!\n"
        "Выбери раздел в меню ниже:",
        parse_mode="HTML",
        reply_markup=client_test_menu() if in_test_mode else client_main_menu(),
    )


# ── /cancel (FSM) ────────────────────────────────────────────────────────────

@router.message(StateFilter(CmsAddProduct, CmsSettings, CmsEditProduct, CmsProductSearch), Command("cancel"))
async def cms_cancel(message: Message, state: FSMContext) -> None:
    await _clear_fsm_keep_test(state)
    data_after = await state.get_data()
    menu = client_test_menu() if data_after.get("selected_client_id") else client_main_menu()
    await message.answer("Скасовано.", reply_markup=menu)


# ── 📦 Товары (paginated) ────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_PRODUCTS)
async def cms_products(message: Message, state: FSMContext) -> None:
    await _clear_fsm_keep_test(state)
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        return
    prods, page, total_pages = await _prod_page_data(client.id, 0)
    await message.answer(
        _prod_list_text_header(page, total_pages, len(prods), client.business_name),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages, client.id),
    )


@router.callback_query(F.data.startswith("cms:pl:"))
async def cms_prod_page(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(cb.data[len("cms:pl:"):])
    except ValueError:
        await cb.answer()
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    prods, page, total_pages = await _prod_page_data(client.id, page)
    await cb.message.edit_text(  # type: ignore[union-attr]
        _prod_list_text_header(page, total_pages, len(prods), client.business_name),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages, client.id),
    )
    await cb.answer()


@router.callback_query(F.data == "cms:noop")
async def cms_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data.startswith("cms:pv:"))
async def cms_prod_view(cb: CallbackQuery, state: FSMContext) -> None:
    # Format: cms:pv:{id}:{page}
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None or product.client_id != client.id:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    from app.config import settings as app_settings
    base = (app_settings.payment_webhook_base_url or "").rstrip("/")
    site_url = f"{base}/site/{client.slug}/product/{product.id}" if base else ""
    await cb.message.edit_text(  # type: ignore[union-attr]
        _prod_card_text(product),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(product, page, site_url),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:pe:"))
async def cms_prod_edit_start(cb: CallbackQuery, state: FSMContext) -> None:
    # Format: cms:pe:{id}:{field}:{page}
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        field = parts[3]
        page = int(parts[4]) if len(parts) > 4 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return
    if field not in _PROD_EDIT_VALID:
        await cb.answer("Невідоме поле", show_alert=True)
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None or product.client_id != client.id:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    await state.update_data(edit_prod_id=prod_id, edit_prod_page=page)
    if field == "image":
        await state.set_state(CmsEditProduct.edit_image)
        await cb.message.answer(  # type: ignore[union-attr]
            "🖼 Надішліть нове фото або URL зображення. «-» щоб очистити.\n"
            "<i>/cancel для скасування</i>",
            parse_mode="HTML",
        )
    else:
        await state.update_data(edit_prod_field=field)
        await state.set_state(CmsEditProduct.edit_field)
        prompt = _PROD_EDIT_PROMPTS.get(field, f"Введіть нове значення для «{field}»:")
        await cb.message.answer(  # type: ignore[union-attr]
            prompt + "\n<i>/cancel для скасування</i>",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(StateFilter(CmsEditProduct.edit_field))
async def cms_prod_edit_field_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    data = await state.get_data()
    prod_id: int = data["edit_prod_id"]
    field: str = data["edit_prod_field"]
    page: int = data.get("edit_prod_page", 0)
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None or product.client_id != client.id:
            await _clear_fsm_keep_test(state)
            await message.answer("Товар не знайдено.")
            return
        clear = (val == "-")
        if field == "name":
            if not val or clear:
                await message.answer("Назва не може бути порожньою або «-». Введіть ще раз:")
                return
            product.name = val
        elif field in ("price", "old_price"):
            if clear and field == "old_price":
                product.old_price = None
            else:
                try:
                    parsed = Decimal(val.replace(",", "."))
                    if parsed < 0:
                        raise ValueError("negative")
                    setattr(product, field, parsed)
                except (InvalidOperation, ValueError):
                    await message.answer("❌ Некоректна ціна. Введіть число або «-»:")
                    return
        else:
            setattr(product, field, None if clear else (val or None))
        await session.commit()
        await session.refresh(product)
        fresh = product
    await _clear_fsm_keep_test(state)
    from app.config import settings as app_settings
    base = (app_settings.payment_webhook_base_url or "").rstrip("/")
    site_url = f"{base}/site/{client.slug}/product/{fresh.id}" if base else ""
    await message.answer(
        "✅ Збережено\n\n" + _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )


@router.message(StateFilter(CmsEditProduct.edit_image), F.photo)
async def cms_prod_edit_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    if not (
        app_settings.cloudinary_cloud_name
        and app_settings.cloudinary_api_key
        and app_settings.cloudinary_api_secret
    ):
        await message.answer(
            "📷 Cloudinary не налаштований.\n"
            "Надішліть URL або «-» щоб очистити:",
        )
        return
    photo = message.photo[-1]
    url = await _upload_to_cloudinary(message.bot, photo.file_id)  # type: ignore[arg-type]
    if not url:
        await message.answer("⚠️ Не вдалось завантажити фото. Спробуйте URL:")
        return
    await _save_prod_photo(message, state, url)


@router.message(StateFilter(CmsEditProduct.edit_image))
async def cms_prod_edit_image_url(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if val == "-":
        await _save_prod_photo(message, state, None)
        return
    if not (val.startswith("https://") or val.startswith("http://")):
        await message.answer("❌ URL має починатись з https:// або http://:")
        return
    await _save_prod_photo(message, state, val)


async def _save_prod_photo(message: Message, state: FSMContext, url: str | None) -> None:
    data = await state.get_data()
    prod_id: int = data["edit_prod_id"]
    page: int = data.get("edit_prod_page", 0)
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None or product.client_id != client.id:
            await _clear_fsm_keep_test(state)
            await message.answer("Товар не знайдено.")
            return
        product.image_url = url
        await session.commit()
        await session.refresh(product)
        fresh = product
    await _clear_fsm_keep_test(state)
    from app.config import settings as app_settings
    base = (app_settings.payment_webhook_base_url or "").rstrip("/")
    site_url = f"{base}/site/{client.slug}/product/{fresh.id}" if base else ""
    await message.answer(
        "✅ Фото оновлено\n\n" + _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )


@router.callback_query(F.data.startswith("cms:ptog:"))
async def cms_prod_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    # Format: cms:ptog:{id}:{page}
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None or product.client_id != client.id:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        product.is_available = not product.is_available
        await session.commit()
        await session.refresh(product)
        fresh = product
    from app.config import settings as app_settings
    base = (app_settings.payment_webhook_base_url or "").rstrip("/")
    site_url = f"{base}/site/{client.slug}/product/{fresh.id}" if base else ""
    await cb.message.edit_text(  # type: ignore[union-attr]
        _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )
    await cb.answer("✅ Показано" if fresh.is_available else "❌ Приховано")


@router.callback_query(F.data.startswith("cms:pdc:"))
async def cms_prod_del_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    # Format: cms:pdc:{id}:{page}
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None or product.client_id != client.id:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Так, видалити",
            callback_data=f"cms:pdo:{prod_id}:{page}",
        ),
        InlineKeyboardButton(
            text="❌ Скасувати",
            callback_data=f"cms:pv:{prod_id}:{page}",
        ),
    ]])
    await cb.message.edit_text(  # type: ignore[union-attr]
        f"🗑 Видалити товар <b>#{product.id} · {product.name}</b>?\n\n"
        f"<i>Товар буде видалено назавжди.</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:pdo:"))
async def cms_prod_del_do(cb: CallbackQuery, state: FSMContext) -> None:
    # Format: cms:pdo:{id}:{page}
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None or product.client_id != client.id:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        prod_name = product.name
        await session.delete(product)
        await session.commit()
    prods, page, total_pages = await _prod_page_data(client.id, page)
    await cb.message.edit_text(  # type: ignore[union-attr]
        f"🗑 Товар <b>{prod_name}</b> видалено.\n\n"
        + _prod_list_text_header(page, total_pages, len(prods), client.business_name),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages, client.id),
    )
    await cb.answer("✅ Видалено")


@router.callback_query(F.data == "cms:psearch")
async def cms_prod_search_start(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    await state.set_state(CmsProductSearch.query)
    await cb.message.answer(  # type: ignore[union-attr]
        "🔍 <b>Пошук товарів</b>\n\n"
        "Введіть ID (або #ID), назву, бренд або категорію:\n"
        "<i>/cancel для скасування</i>",
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(StateFilter(CmsProductSearch.query))
async def cms_prod_search_input(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введіть пошуковий запит:")
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return
    raw = query.lstrip("#")
    async with AsyncSessionLocal() as session:
        if raw.isdigit():
            results = list(await session.scalars(
                select(Product)
                .where(Product.client_id == client.id, Product.id == int(raw))
                .limit(20)
            ))
        else:
            q = f"%{query}%"
            results = list(await session.scalars(
                select(Product)
                .where(
                    Product.client_id == client.id,
                    or_(
                        Product.name.ilike(q),
                        Product.brand.ilike(q),
                        Product.category.ilike(q),
                        Product.group_name.ilike(q),
                    ),
                )
                .order_by(Product.id.desc())
                .limit(20)
            ))
    await _clear_fsm_keep_test(state)
    if not results:
        await message.answer(
            f"🔍 За запитом «{query}» нічого не знайдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="← Список товарів", callback_data="cms:pl:0"),
            ]]),
        )
        return
    rows = [[InlineKeyboardButton(
        text=_prod_row_btn(p),
        callback_data=f"cms:pv:{p.id}:0",
    )] for p in results]
    rows.append([InlineKeyboardButton(text="← Список товарів", callback_data="cms:pl:0")])
    await message.answer(
        f"🔍 Знайдено за «{query}»: {len(results)} товар(ів)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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

    # Dashboard URL with token (if set)
    dashboard_url: str | None = None
    if base:
        if client.dashboard_token:
            dashboard_url = f"{base}/dashboard/{client.slug}?token={client.dashboard_token}"
        else:
            dashboard_url = f"{base}/dashboard/{client.slug}"

    dashboard_line = f"\n\n🖥 <b>Дашборд:</b>\n<code>{dashboard_url}</code>" if dashboard_url else ""

    await message.answer(
        f"🌐 <b>Ваш сайт:</b>\n"
        f"<code>{site_url}</code>\n\n"
        f"Slug: <code>{client.slug}</code>{dashboard_line}",
        parse_mode="HTML",
        reply_markup=client_main_menu(),
    )


# ── 📊 Замовлення ────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_ORDERS)
async def cms_orders(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        return
    new, ip, done = await _order_counts(client.id)
    await message.answer(
        _order_summary_text(new, ip, done),
        parse_mode="HTML",
        reply_markup=_order_summary_kb(new, ip, done),
    )


@router.callback_query(F.data.startswith("cms:ord:list:"))
async def cms_orders_list(cb: CallbackQuery, state: FSMContext) -> None:
    status = cb.data[len("cms:ord:list:"):]
    if status not in ORDER_STATUS_LABELS:
        await cb.answer("Невідомий статус", show_alert=True)
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        orders = list(await session.scalars(
            select(Order)
            .where(Order.client_id == client.id, Order.status == status)
            .order_by(Order.id.desc())
            .limit(10)
        ))
    await cb.message.edit_text(  # type: ignore[union-attr]
        _order_list_text(orders, status),
        parse_mode="HTML",
        reply_markup=_order_list_kb(orders, status),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:ord:status:"))
async def cms_ord_set_status(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")  # cms:ord:status:{id}:{new_status}
    if len(parts) != 5:
        await cb.answer("Помилка", show_alert=True)
        return
    try:
        order_id = int(parts[3])
    except ValueError:
        await cb.answer("Помилка", show_alert=True)
        return
    new_status = parts[4]
    if new_status not in ("in_progress", "done"):
        await cb.answer("Невідомий статус", show_alert=True)
        return
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order is None or order.client_id != client.id:
            await cb.answer("Замовлення не знайдено", show_alert=True)
            return
        old_status = order.status
        order.status = new_status
        await session.commit()
    async with AsyncSessionLocal() as session:
        orders = list(await session.scalars(
            select(Order)
            .where(Order.client_id == client.id, Order.status == old_status)
            .order_by(Order.id.desc())
            .limit(10)
        ))
    await cb.message.edit_text(  # type: ignore[union-attr]
        _order_list_text(orders, old_status),
        parse_mode="HTML",
        reply_markup=_order_list_kb(orders, old_status),
    )
    await cb.answer("✅ Статус змінено")


@router.callback_query(F.data == "cms:ord:back")
async def cms_ord_back(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return
    new, ip, done = await _order_counts(client.id)
    await cb.message.edit_text(  # type: ignore[union-attr]
        _order_summary_text(new, ip, done),
        parse_mode="HTML",
        reply_markup=_order_summary_kb(new, ip, done),
    )
    await cb.answer()


# ── ⚙️ Настройки ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_SETTINGS)
async def cms_settings(message: Message, state: FSMContext) -> None:
    await _clear_fsm_keep_test(state)
    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        return

    async with AsyncSessionLocal() as session:
        cs = await session.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client.id)
        )
    await message.answer(
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
    )


# ── Settings: callbacks ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:set:"))
async def cms_settings_start_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data[len("cms:set:"):]
    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return

    # ── Cancel: clear FSM, show overview ──
    if field == "cancel":
        await _clear_fsm_keep_test(state)
        async with AsyncSessionLocal() as session:
            cs = await session.scalar(
                select(ClientSettings).where(ClientSettings.client_id == client.id)
            )
        await cb.message.answer(  # type: ignore[union-attr]
            _settings_text(client, cs),
            parse_mode="HTML",
            reply_markup=_settings_overview_kb(),
        )
        await cb.answer()
        return

    # ── Theme: inline edit, no FSM ──
    if field == "theme":
        async with AsyncSessionLocal() as session:
            cs = await session.scalar(
                select(ClientSettings).where(ClientSettings.client_id == client.id)
            )
        theme = (cs.theme_name if cs else None) or "light_red"
        await cb.message.edit_text(  # type: ignore[union-attr]
            f"🎨 <b>Тема сайту</b>\n\nПоточна: {THEMES.get(theme, theme)}\nОберіть:",
            parse_mode="HTML",
            reply_markup=_themes_kb(theme),
        )
        await cb.answer()
        return

    if field not in VALID_SETTINGS_FIELDS:
        await cb.answer("Невідома дія", show_alert=True)
        return

    await state.set_state(getattr(CmsSettings, field))
    await cb.message.answer(  # type: ignore[union-attr]
        SETTINGS_PROMPTS[field],
        parse_mode="HTML",
        reply_markup=_cancel_input_kb(field),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:theme:"))
async def cms_set_theme(cb: CallbackQuery, state: FSMContext) -> None:
    theme_key = cb.data.split(":", 2)[2]
    if theme_key not in VALID_THEMES:
        await cb.answer("Невідома тема", show_alert=True)
        return

    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        cs = await session.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client.id)
        )
        if cs is None:
            cs = ClientSettings(client_id=client.id, theme_name=theme_key)
            session.add(cs)
        else:
            cs.theme_name = theme_key
        await session.commit()
        await session.refresh(cs)

    await cb.message.edit_text(  # type: ignore[union-attr]
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
    )
    await cb.answer("✅ Тему збережено!")


@router.callback_query(F.data.startswith("cms:clr:"))
async def cms_settings_clear_field(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data[len("cms:clr:"):]
    if field not in VALID_SETTINGS_FIELDS:
        await cb.answer("Невідома дія", show_alert=True)
        return

    user_id = cb.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await cb.answer("Немає доступу", show_alert=True)
        return

    cs = await _save_settings_field(client.id, field, None)
    await _clear_fsm_keep_test(state)
    await cb.message.answer(  # type: ignore[union-attr]
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
    )
    await cb.answer("🗑 Очищено")


@router.message(
    StateFilter(
        CmsSettings.shop_title,
        CmsSettings.phone,
        CmsSettings.address,
        CmsSettings.telegram_url,
        CmsSettings.instagram_url,
    )
)
async def cms_settings_text_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Значення не може бути порожнім. Спробуйте ще раз або скасуйте:")
        return

    current = await state.get_state()
    field = current.split(":")[-1] if current else ""

    if field in URL_SETTINGS_FIELDS:
        if not (val.startswith("https://") or val.startswith("http://")):
            await message.answer(
                "❌ Некоректний URL. Має починатись з <code>https://</code>",
                parse_mode="HTML",
                reply_markup=_cancel_input_kb(field),
            )
            return

    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return

    cs = await _save_settings_field(client.id, field, val)
    await _clear_fsm_keep_test(state)
    await message.answer(
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
    )


@router.message(StateFilter(CmsSettings.logo), F.photo)
async def cms_logo_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    if not (
        app_settings.cloudinary_cloud_name
        and app_settings.cloudinary_api_key
        and app_settings.cloudinary_api_secret
    ):
        await message.answer(
            "📷 Cloudinary не налаштований.\n"
            "Надішліть URL посилання на логотип або натисніть «Скасувати»:",
            reply_markup=_cancel_input_kb("logo"),
        )
        return

    photo = message.photo[-1]
    url = await _upload_to_cloudinary(message.bot, photo.file_id)  # type: ignore[arg-type]
    if not url:
        await message.answer(
            "⚠️ Не вдалось завантажити фото. Спробуйте URL або скасуйте:",
            reply_markup=_cancel_input_kb("logo"),
        )
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return

    cs = await _save_settings_field(client.id, "logo", url)
    await _clear_fsm_keep_test(state)
    await message.answer(
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
    )


@router.message(StateFilter(CmsSettings.logo))
async def cms_logo_url_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Введіть URL або надішліть фото:")
        return
    if not (val.startswith("https://") or val.startswith("http://")):
        await message.answer(
            "❌ URL має починатись з <code>https://</code> або <code>http://</code>",
            parse_mode="HTML",
            reply_markup=_cancel_input_kb("logo"),
        )
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    client = await _get_effective_client(user_id, state)
    if client is None:
        await _clear_fsm_keep_test(state)
        return

    cs = await _save_settings_field(client.id, "logo", val)
    await _clear_fsm_keep_test(state)
    await message.answer(
        _settings_text(client, cs),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(),
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
    await state.update_data(name=text, specs_items=[])
    await state.set_state(CmsAddProduct.specs)
    await message.answer(
        "Крок 5 — Характеристики товару:\n\nВводьте по одній (наприклад: Площа: 35 м²).\nКоли закінчите — натисніть ✅ Готово.",
        reply_markup=_specs_kb(),
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
    await state.update_data(specs=None, specs_items=[])
    await state.set_state(CmsAddProduct.price)
    await cb.message.answer("Крок 6 — Ціна (наприклад: 150):")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data == "cms:done:specs", StateFilter(CmsAddProduct.specs))
async def cms_done_specs(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    items: list = data.get("specs_items", [])
    specs_text = "\n".join(items) if items else None
    await state.update_data(specs=specs_text, specs_items=[])
    await state.set_state(CmsAddProduct.price)
    await cb.message.answer("Крок 6 — Ціна (наприклад: 150):")  # type: ignore[union-attr]
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.specs))
async def cms_add_specs(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Введіть характеристику або натисніть кнопку:", reply_markup=_specs_kb())
        return
    data = await state.get_data()
    items: list = list(data.get("specs_items", []))
    items.append(val)
    await state.update_data(specs_items=items)
    await message.answer(
        _specs_list_text(items),
        reply_markup=_specs_kb(),
    )


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
