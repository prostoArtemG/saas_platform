from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

BTN_CREATE_CLIENT = "➕ Создать клиента"
BTN_CLIENTS = "📋 Клиенты"
BTN_CREATE_PLAN = "➕ Создать тариф"
BTN_PLANS = "💳 Тарифы"
BTN_SUBSCRIPTIONS = "🧾 Подписки"
BTN_PRODUCTS = "📦 Товары"

# ── Client CMS menu ───────────────────────────────────────────────────────────
# BTN_CMS_PRODUCTS shares the same text as BTN_PRODUCTS intentionally;
# routing is differentiated by AdminFilter vs ClientFilter.
BTN_CMS_PRODUCTS = "📦 Товары"
BTN_CMS_SITE = "🌐 Мой сайт"
BTN_CMS_ORDERS = "📊 Заказы"
BTN_CMS_SETTINGS = "⚙️ Настройки"
BTN_EXIT_TEST = "⬅️ Выйти из тест-режима"


def admin_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CREATE_CLIENT)],
            [KeyboardButton(text=BTN_CLIENTS)],
            [KeyboardButton(text=BTN_CREATE_PLAN)],
            [KeyboardButton(text=BTN_PLANS)],
            [KeyboardButton(text=BTN_SUBSCRIPTIONS)],
            [KeyboardButton(text=BTN_PRODUCTS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def client_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CMS_PRODUCTS)],
            [KeyboardButton(text=BTN_CMS_SITE), KeyboardButton(text=BTN_CMS_ORDERS)],
            [KeyboardButton(text=BTN_CMS_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def client_test_menu() -> ReplyKeyboardMarkup:
    """CMS menu shown to platform admins in test mode — includes exit button."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CMS_PRODUCTS)],
            [KeyboardButton(text=BTN_CMS_SITE), KeyboardButton(text=BTN_CMS_ORDERS)],
            [KeyboardButton(text=BTN_CMS_SETTINGS)],
            [KeyboardButton(text=BTN_EXIT_TEST)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
