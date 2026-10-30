import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
import db
import icons
import services
from handlers import admin, menu, user
from middleware import GateMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

_tasks: set = set()  # keep references so background tasks aren't garbage-collected


async def init_database():
    """DB init with retries (e.g. Neon waking up from sleep)."""
    delay = 2
    for attempt in range(1, 8):
        try:
            await db.init()
            await services.load_admins()
            await services.load_settings()
            await services.seed_menu()
            await services.load_menu()
            return
        except Exception as e:
            log.warning("Database not ready (attempt %s): %s", attempt, e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)
    await db.init()  # last try – raise the real error
    await services.load_admins()
    await services.load_settings()
    await services.seed_menu()
    await services.load_menu()


async def setup_telegram(bot: Bot):
    """Talk to Telegram with endless retries, so a network hiccup never crashes the service."""
    delay = 3
    while True:
        try:
            me = await bot.get_me()
            if config.WEBHOOK_URL:
                url = config.WEBHOOK_URL + config.WEBHOOK_PATH
                await bot.set_webhook(url, secret_token=config.WEBHOOK_SECRET, allowed_updates=config.ALLOWED_UPDATES)
                log.info("Started as @%s | webhook set: %s | db=%s", me.username, url, config.DATABASE_URL.split("://")[0])
            else:
                await bot.delete_webhook()
                log.info("Started as @%s | long polling | db=%s", me.username, config.DATABASE_URL.split("://")[0])
            return
        except TelegramUnauthorizedError:
            log.error("❌ BOT_TOKEN is invalid. Fix it in Render → Environment and redeploy.")
            return
        except Exception as e:
            log.warning("Telegram not reachable yet (%s). Retrying in %ss…", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def on_startup(bot: Bot):
    await init_database()
    if config.WEBHOOK_URL:
        # don't block the web server: Render needs the port open quickly
        t = asyncio.create_task(setup_telegram(bot))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
    else:
        await setup_telegram(bot)


async def on_shutdown(bot: Bot):
    await db.close()


def build() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    bot.session.middleware(icons.IconFallback())  # retry without premium icons if Telegram refuses them
    dp = Dispatcher()
    gate = GateMiddleware()
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)
    dp.include_router(admin.router)  # admin first so FSM states win
    dp.include_router(menu.router)
    dp.include_router(user.router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return bot, dp


def run_webhook():
    bot, dp = build()
    app = web.Application()

    async def health(_):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=config.WEBHOOK_SECRET).register(
        app, path=config.WEBHOOK_PATH
    )
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=config.PORT)


async def run_polling():
    bot, dp = build()
    await dp.start_polling(bot, allowed_updates=config.ALLOWED_UPDATES)


if __name__ == "__main__":
    if config.WEBHOOK_URL:
        run_webhook()
    else:
        asyncio.run(run_polling())
