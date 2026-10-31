"""main.py – starts the web server (dashboard + webhooks) and runs every bot from the database."""
import asyncio
import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import Update
from aiohttp import web

import bot as botlogic
import core
import dashboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

DATA_TABLES = ("bot_users", "bot_settings", "bot_channels", "bot_requests", "bot_menu", "bot_prompts")


class Manager:
    """Holds every running Bot. ONE Dispatcher (shared handlers) serves all of them."""

    def __init__(self):
        self.bots: dict[int, Bot] = {}
        self.state: dict[int, str] = {}      # bot id -> "connecting" | "online" | "error: …"
        self.dp = Dispatcher()
        botlogic.register(self.dp)
        self._tasks: set = set()
        self.polling = not core.WEBHOOK_URL

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    @staticmethod
    def _make_bot(token: str) -> Bot:
        b = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
        b.session.middleware(core.IconFallback())  # retry without premium icons / colours if Telegram refuses
        return b

    # ── start / stop one bot ──
    async def activate(self, row: dict) -> Bot:
        bid = row["id"]
        if bid in self.bots:
            return self.bots[bid]
        bot = self._make_bot(row["token"])
        self.bots[bid] = bot
        self.state[bid] = "connecting"
        await core.load_bot_cache(bid)
        if row["token"] == core.BOT_TOKEN:
            await core.migrate_legacy(bid)  # old single-bot data -> this bot
        await core.seed_menu(bid)
        self._spawn(self._connect(bot, row))
        return bot

    async def _connect(self, bot: Bot, row: dict):
        """Talk to Telegram with endless retries, so a network hiccup never breaks a bot."""
        bid, delay = bot.id, 3
        while bid in self.bots:
            try:
                me = await bot.get_me()
                await core.execute("UPDATE bots SET username=:u, name=:n WHERE id=:i",
                                   u=me.username, n=me.full_name, i=bid)
                if core.WEBHOOK_URL:
                    await bot.set_webhook(f"{core.WEBHOOK_URL}/wh/{bid}", secret_token=core.webhook_secret(bid),
                                          allowed_updates=core.ALLOWED_UPDATES)
                    self.state[bid] = "online"
                else:
                    await bot.delete_webhook()
                    self.state[bid] = "online (polling)" if self.polling_started else "added – restart to start polling"
                log.info("Bot @%s ready", me.username)
                return
            except TelegramUnauthorizedError:
                self.state[bid] = "error: invalid token"
                log.error("Bot %s: invalid token", bid)
                return
            except Exception as e:
                self.state[bid] = "connecting"
                log.warning("Bot %s: Telegram not reachable (%s). Retrying in %ss…", bid, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    polling_started = False

    async def deactivate(self, bid: int):
        bot = self.bots.pop(bid, None)
        self.state.pop(bid, None)
        if bot:
            try:
                if core.WEBHOOK_URL:
                    await bot.delete_webhook()
            except Exception:
                pass
            await bot.session.close()

    async def add_token(self, token: str) -> dict:
        """Validate a token with Telegram, store it and start the bot."""
        bid = int(token.split(":")[0])
        if await core.fetchone("SELECT 1 AS x FROM bots WHERE id=:i", i=bid):
            raise ValueError("This bot is already added.")
        tmp = self._make_bot(token)
        try:
            me = await tmp.get_me(request_timeout=20)
        except TelegramUnauthorizedError:
            raise ValueError("Telegram rejected this token — copy it again from @BotFather.")
        except Exception as e:
            raise ValueError(f"Couldn't reach Telegram: {str(e)[:100]}")
        finally:
            await tmp.session.close()
        await core.execute("INSERT INTO bots(id, token, username, name, active) VALUES(:i, :t, :u, :n, 1)",
                           i=bid, t=token, u=me.username, n=me.full_name)
        row = await core.fetchone("SELECT * FROM bots WHERE id=:i", i=bid)
        await self.activate(row)
        return {"id": bid, "username": me.username, "name": me.full_name}

    async def remove(self, bid: int):
        """Stop the bot and delete it together with ALL its data."""
        await self.deactivate(bid)
        for t in DATA_TABLES:  # fixed list, never user input
            await core.execute(f"DELETE FROM {t} WHERE bot_id=:b", b=bid)
        await core.execute("DELETE FROM bots WHERE id=:b", b=bid)
        core.S.pop(bid, None)
        core.MENU.pop(bid, None)

    # ── Telegram → us ──
    async def webhook(self, request: web.Request) -> web.Response:
        try:
            bid = int(request.match_info["bid"])
        except ValueError:
            return web.Response(status=404)
        bot = self.bots.get(bid)
        if bot is None:
            return web.Response(status=404)
        if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), core.webhook_secret(bid)):
            return web.Response(status=403)
        try:
            update = Update.model_validate(await request.json(), context={"bot": bot})
        except Exception:
            return web.Response(status=400)
        self._spawn(self._feed(bot, update))
        return web.Response(text="ok")  # answer fast, process in the background

    async def _feed(self, bot: Bot, update: Update):
        try:
            await self.dp.feed_update(bot, update)
        except Exception:
            log.exception("update handling failed (bot %s)", bot.id)

    # ── boot ──
    async def start_all(self):
        delay = 2
        for attempt in range(1, 9):
            try:
                await core.init()
                await core.load_admins()
                break
            except Exception as e:
                log.warning("Database not ready (attempt %s): %s", attempt, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 20)
        else:
            await core.init()
            await core.load_admins()
        if core.BOT_TOKEN:  # the bot from the environment is always registered
            bid = int(core.BOT_TOKEN.split(":")[0])
            await core.execute(
                "INSERT INTO bots(id, token, active) VALUES(:i, :t, 1) ON CONFLICT(id) DO UPDATE SET token=excluded.token",
                i=bid, t=core.BOT_TOKEN)
        for row in await core.fetch("SELECT * FROM bots WHERE active=1 ORDER BY created, id"):
            await self.activate(row)
        log.info("Running %s bot(s)", len(self.bots))


async def main():
    mgr = Manager()
    app = web.Application(client_max_size=8 * 1024 ** 2)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    app.router.add_post("/wh/{bid}", mgr.webhook)
    dashboard.setup(app, mgr)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", core.PORT).start()  # open the port first (Render health check)
    log.info("Web server on :%s  (dashboard: %s)", core.PORT, "enabled" if core.DASHBOARD_PASSWORD else "DISABLED – set DASHBOARD_PASSWORD")

    await mgr.start_all()
    try:
        if mgr.polling and mgr.bots:
            mgr.polling_started = True
            for bid in mgr.bots:
                mgr.state[bid] = "online (polling)"
            await mgr.dp.start_polling(*mgr.bots.values(), allowed_updates=core.ALLOWED_UPDATES)
        else:
            await asyncio.Event().wait()  # webhook mode: just keep serving
    finally:
        await runner.cleanup()
        await core.close()


if __name__ == "__main__":
    asyncio.run(main())
