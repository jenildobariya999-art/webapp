import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
import db
import services
from handlers import admin, menu, user
from middleware import GateMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


async def on_startup(bot: Bot):
    await db.init()
    await services.load_admins()
    me = await bot.get_me()
    log.info("Started as @%s | db=%s", me.username, config.DATABASE_URL.split("://")[0])
    if config.WEBHOOK_URL:
        url = config.WEBHOOK_URL + config.WEBHOOK_PATH
        await bot.set_webhook(url, secret_token=config.WEBHOOK_SECRET, allowed_updates=config.ALLOWED_UPDATES)
        log.info("Webhook set: %s", url)
    else:
        await bot.delete_webhook()
        log.info("No WEBHOOK_URL -> long polling")


async def on_shutdown(bot: Bot):
    await db.close()


def build() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
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
