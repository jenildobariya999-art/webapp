import logging

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import services

log = logging.getLogger(__name__)


async def send_join_prompt(bot: Bot, user_id: int, missing: list[dict]):
    kb = InlineKeyboardBuilder()
    for ch in missing:
        prefix = "📨 Request to join: " if ch["request_mode"] else "📢 Join: "
        kb.button(text=prefix + (ch["title"] or "Channel"), url=ch["link"])
    kb.button(text="✅ I've joined", callback_data="check")
    kb.adjust(1)
    await bot.send_message(user_id, await services.get_setting("force_text"), reply_markup=kb.as_markup())


class GateMiddleware(BaseMiddleware):
    """Registers users, drops banned users and enforces force-join."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        if isinstance(event, Message) and event.chat.type != "private" and user.id not in config.ADMINS:
            return  # ignore groups for non-admins

        await services.upsert_user(user)

        if user.id in config.ADMINS:
            return await handler(event, data)

        row = await db.fetchone("SELECT banned FROM users WHERE id=:i", i=user.id)
        if row and row["banned"]:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned.", show_alert=True)
            return

        if isinstance(event, CallbackQuery) and event.data == "check":
            return await handler(event, data)

        if await services.get_setting("force_join") != "1":
            return await handler(event, data)

        bot: Bot = data["bot"]
        missing = await services.missing_channels(bot, user.id)
        if not missing:
            return await handler(event, data)

        await send_join_prompt(bot, user.id, missing)
        if isinstance(event, CallbackQuery):
            await event.answer()
