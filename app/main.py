import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bot.bot import create_bot, create_dispatcher
from app.config import settings
from app.db import close_db, init_db
from app.api import router as api_router
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
            _SKIP_PREFIXES = ("/static", "/api", "/dashboard", "/payment", "/health")
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

    try:
        yield
    finally:
        logger.info("Shutting down...")
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
