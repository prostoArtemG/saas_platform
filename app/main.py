import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bot.bot import create_bot, create_dispatcher
from app.config import settings
from app.db import close_db, init_db
from app.api import router as api_router
from app.site.routes import router as site_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("saas_platform")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")

    bot = create_bot()
    dp = create_dispatcher()
    app.state.bot = bot

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
