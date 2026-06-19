import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select as sa_select

from app.bot.bot import create_bot, create_dispatcher
from app.config import settings
from app.db import AsyncSessionLocal, close_db, init_db
from app.api import router as api_router
from app.models import Client
from app.services.client_bot_manager import (
    get_registry_entry,
    start_client_bot,
    stop_all_client_bots,
)
from app.site.routes import get_client_slug_from_host, router as site_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("saas_platform")


class SubdomainMiddleware:
    """ASGI middleware that rewrites paths for client subdomains.

    When a request arrives at e.g. ``apelsin.shopplatform.app/product/5``,
    this rewrites the path to ``/site/apelsin/product/5`` so the existing
    route handlers serve the storefront transparently.

    Mapping rules (slug = extracted subdomain):
        /                   → /site/{slug}
        /product/{id}       → /site/{slug}/product/{id}
        /order              → /site/{slug}/order
        anything else       → /site/{slug}{path}
    """

    def __init__(self, app, platform_domain: str) -> None:
        self.app = app
        self.platform_domain = platform_domain

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            host = headers.get(b"host", b"").decode("latin-1")
            slug = get_client_slug_from_host(host, self.platform_domain)
            _SKIP_PREFIXES = ("/static", "/api", "/dashboard", "/payment", "/health", "/webhook")
            if slug:
                path: str = scope.get("path", "/")
                if any(path == p or path.startswith(p + "/") for p in _SKIP_PREFIXES):
                    await self.app(scope, receive, send)
                    return
                if path == "/" or path == "":
                    new_path = f"/site/{slug}"
                else:
                    new_path = f"/site/{slug}{path}"
                scope = {
                    **scope,
                    "path": new_path,
                    "raw_path": new_path.encode("latin-1"),
                }
                logger.debug("SubdomainMiddleware: %s%s → %s", host, path, new_path)
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")

    bot = create_bot()
    dp = create_dispatcher()
    app.state.bot = bot

    # Cache platform bot username for "Open Telegram CMS" links
    try:
        me = await bot.get_me()
        app.state.platform_bot_username = me.username
        logger.info("Platform bot: @%s (id=%s)", me.username, me.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch platform bot username: %s", exc)
        app.state.platform_bot_username = None

    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False),
        name="bot-polling",
    )
    logger.info("Telegram bot polling started")

    # Start personal client bots (webhook mode)
    _wb_base = settings.client_bot_webhook_base
    if _wb_base:
        try:
            async with AsyncSessionLocal() as _sess:
                _personal_clients = (
                    await _sess.scalars(
                        sa_select(Client)
                        .where(Client.template_name == "technomarket_premium")
                        .where(Client.bot_mode == "personal")
                        .where(Client.telegram_bot_token.isnot(None))
                    )
                ).all()
            for _c in _personal_clients:
                await start_client_bot(_wb_base, _c.slug, _c.telegram_bot_token)
            if _personal_clients:
                logger.info("Started %d personal client bot(s)", len(_personal_clients))
        except Exception as _e:
            logger.warning("Could not start personal client bots: %s", _e)
    else:
        logger.info(
            "CLIENT_BOT_WEBHOOK_BASE not set — personal client bots disabled on startup"
        )

    try:
        yield
    finally:
        logger.info("Shutting down...")
        await stop_all_client_bots()
        await dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except (asyncio.CancelledError, Exception):
            pass
        await bot.session.close()
        await close_db()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="saas_platform", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(site_router)
    app.include_router(api_router)

    @app.post("/webhook/client/{slug}")
    async def client_webhook(slug: str, request: Request) -> JSONResponse:
        """Receive Telegram updates for a personal client bot."""
        entry = get_registry_entry(slug)
        if entry is None:
            return JSONResponse({"ok": False, "error": "bot not registered"}, status_code=404)
        from aiogram.types import Update
        bot_inst, dp_inst = entry
        body = await request.body()
        update = Update.model_validate_json(body)
        await dp_inst.feed_update(bot=bot_inst, update=update)
        return JSONResponse({"ok": True})

    # Subdomain middleware: must be added AFTER routers are registered so that
    # the ASGI app it wraps already has the full route table.
    app.add_middleware(SubdomainMiddleware, platform_domain=settings.platform_domain)
    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
