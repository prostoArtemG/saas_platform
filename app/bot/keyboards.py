from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

BTN_CREATE_CLIENT = "➕ Создать клиента"
BTN_CLIENTS = "📋 Клиенты"
BTN_CREATE_PLAN = "➕ Создать тариф"
BTN_PLANS = "💳 Тарифы"
BTN_SUBSCRIPTIONS = "🧾 Подписки"


def admin_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CREATE_CLIENT)],
            [KeyboardButton(text=BTN_CLIENTS)],
            [KeyboardButton(text=BTN_CREATE_PLAN)],
            [KeyboardButton(text=BTN_PLANS)],
            [KeyboardButton(text=BTN_SUBSCRIPTIONS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
