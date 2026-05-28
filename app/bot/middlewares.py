"""Outer middleware that clears FSM state when the user taps a main-menu reply
button. Without this, FSM handlers using ``F.text`` would consume those button
presses as user input, leaving the bot unresponsive to menu navigation.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Set

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from app.bot.keyboards import (
    BTN_CLIENTS,
    BTN_CMS_ORDERS,
    BTN_CMS_SETTINGS,
    BTN_CMS_SITE,
    BTN_CREATE_CLIENT,
    BTN_CREATE_PLAN,
    BTN_EXIT_TEST,
    BTN_PLANS,
    BTN_PRODUCTS,
    BTN_SUBSCRIPTIONS,
)

logger = logging.getLogger(__name__)

MENU_BUTTONS: Set[str] = {
    BTN_CLIENTS,
    BTN_CREATE_CLIENT,
    BTN_CREATE_PLAN,
    BTN_PLANS,
    BTN_PRODUCTS,       # also serves as BTN_CMS_PRODUCTS (same text)
    BTN_SUBSCRIPTIONS,
    BTN_CMS_SITE,
    BTN_CMS_ORDERS,
    BTN_CMS_SETTINGS,
    BTN_EXIT_TEST,
}


class MenuInterruptMiddleware(BaseMiddleware):
    """If the incoming message text matches a main-menu reply button, clear
    any active FSM state before handlers run. Also resets state on /start
    and /cancel commands."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            text = event.text.strip()
            # DEBUG: log every incoming text + expected menu buttons for comparison
            logger.warning(
                "INCOMING text=%r codepoints=%s | known menu buttons=%s",
                text,
                [hex(ord(c)) for c in text],
                {b: [hex(ord(c)) for c in b] for b in MENU_BUTTONS},
            )
            should_reset = text in MENU_BUTTONS or text in {"/start", "/cancel"}
            if should_reset:
                state: FSMContext | None = data.get("state")
                if state is not None:
                    cur = await state.get_state()
                    if cur is not None:
                        logger.info(
                            "menu interrupt: clearing FSM state %s for text %r",
                            cur,
                            text,
                        )
                        await state.clear()
        return await handler(event, data)
