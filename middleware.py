import logging

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import services

log = logging.getLogger(__name__)


async def send_join_prompt(bot: Bot, user_id: int, channels: list[dict]):
    """Buttons only say 'Join' (label is editable) — the channel type is never shown."""
    join_tpl = await services.get_setting("join_btn")
    check_label = await services.get_setting("check_btn")
    kb = InlineKeyboardBuilder()
    for i, ch in enumerate(channels, 1):
        label = join_tpl.replace("{title}", ch["title"] or "Channel").replace("{n}", str(i))
        kb.button(text=label[:60], url=ch["link"])
    kb.button(text=check_label[:60], callback_data="check")
    kb.adjust(1)
    await bot.send_message(user_id, await services.get_setting("force_text"), reply_markup=kb.as_markup())


def _referrer(event) -> int | None:
    """Extract the referrer id from '/start ref_123'."""
    if isinstance(event, Message) and event.text and event.text.startswith("/start"):
        parts = event.text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
            return int(parts[1][4:])
    return None


class GateMiddleware(BaseMiddleware):
    """Registers users (+referrals), drops banned users, handles bot ON/OFF and force-join."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        if isinstance(event, Message) and event.chat.type != "private" and user.id not in config.ADMINS:
            return  # ignore groups for non-admins

        await services.upsert_user(user, _referrer(event))

        if user.id in config.ADMINS:
            return await handler(event, data)

        row = await db.fetchone("SELECT banned, verified FROM users WHERE id=:i", i=user.id)
        if row and row["banned"]:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned.", show_alert=True)
            return

        # bot OFF -> maintenance message
        if await services.get_setting("bot_enabled") != "1":
            if isinstance(event, CallbackQuery):
                await event.answer("🛠 Bot is under maintenance. Try again later.", show_alert=True)
            else:
                await event.answer(await services.get_setting("off_text"))
            return

        if isinstance(event, CallbackQuery) and event.data == "check":
            return await handler(event, data)

        if await services.get_setting("force_join") != "1":
            return await handler(event, data)

        bot: Bot = data["bot"]
        missing = await services.missing_channels(bot, user.id)          # verified channels
        unchecked = await services.unchecked_channels()                  # display-only channels
        # unchecked channels are shown once (until the user presses "I've joined")
        if not missing and not (unchecked and not (row and row["verified"])):
            return await handler(event, data)

        await send_join_prompt(bot, user.id, services.merge_channels(missing, unchecked))
        if isinstance(event, CallbackQuery):
            await event.answer()
