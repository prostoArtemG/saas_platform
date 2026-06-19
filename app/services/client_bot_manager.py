"""Registry and lifecycle management for personal client bots.

Kept in a separate service module so both app/main.py (lifespan) and
app/site/routes.py (self-service onboarding) can call start/stop without
creating a circular import.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import Update

from app.bot.client_bot import create_client_dispatcher

logger = logging.getLogger(__name__)

# slug → (Bot, Dispatcher)
_registry: dict[str, tuple] = {}


async def start_client_bot(base_url: str, slug: str, token: str) -> bool:
    """Register webhook and add the bot to the registry.

    Safe to call for an already-running slug (no-op, returns True).

    Args:
        base_url: Platform base URL, e.g. ``https://shopplatform.app``
        slug:     Client slug, e.g. ``mybrand``
        token:    Telegram BOT_TOKEN for this client

    Returns:
        True on success, False on failure.
    """
    if slug in _registry:
        logger.debug("Client bot already running for slug=%s", slug)
        return True

    bot: Optional[Bot] = None
    try:
        bot = Bot(token=token)
        dp = create_client_dispatcher(slug)
        webhook_url = f"{base_url.rstrip('/')}/webhook/client/{slug}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        _registry[slug] = (bot, dp)
        logger.info("Personal bot started: slug=%s webhook=%s", slug, webhook_url)
        return True
    except Exception as exc:
        logger.warning("Failed to start personal bot for slug=%s: %s", slug, exc)
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass
        return False


async def stop_client_bot(slug: str) -> None:
    """Remove webhook and close the bot session for the given slug."""
    entry = _registry.pop(slug, None)
    if entry is None:
        return
    bot, _ = entry
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        logger.warning("Could not delete webhook for slug=%s: %s", slug, exc)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
    logger.info("Personal bot stopped: slug=%s", slug)


async def stop_all_client_bots() -> None:
    """Gracefully stop all running personal bots (called on platform shutdown)."""
    for slug in list(_registry.keys()):
        await stop_client_bot(slug)


def get_bot_for_slug(slug: str) -> Optional[Bot]:
    """Return the Bot instance for a running personal bot, or None."""
    entry = _registry.get(slug)
    return entry[0] if entry else None


def get_registry_entry(slug: str):
    """Return (Bot, Dispatcher) for a running personal bot, or None."""
    return _registry.get(slug)


def is_running(slug: str) -> bool:
    return slug in _registry
