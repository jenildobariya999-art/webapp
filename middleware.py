import asyncio
import logging

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
import db
import icons
import services

log = logging.getLogger(__name__)
_bg: set = set()  # keeps background tasks alive


async def send_join_prompt(bot: Bot, user_id: int, channels: list[dict]):
    """Join buttons only say 'Join' (label + premium icon are editable) — the channel type is never shown.
    The message can carry a photo, and an optional extra message is sent right after it."""
    join_tpl = services.setting("join_btn")
    join_btns = []
    for i, ch in enumerate(channels, 1):
        if ch["kind"] == "social":
            label = ch["title"] or "Link"
            iid = ch["icon"]
        else:
            label = join_tpl.replace("{title}", ch["title"] or "Channel").replace("{n}", str(i))
            iid = ch["icon"] or icons.icon("join_btn")
        join_btns.append(icons.btn(label[:60], url=services.join_url(ch), icon_id=iid, style=icons.style_of("join_btn")))
    rows = [join_btns[i:i + 2] for i in range(0, len(join_btns), 2)]  # 2 Join buttons per line
    rows.append([icons.btn(services.setting("check_btn")[:60], cb="check",
                           icon_id=icons.icon("check_btn"), style=icons.style_of("check_btn"))])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    sent = await services.send_rich(bot, user_id, "force_text", services.setting("force_text"), markup)
    extra = services.setting("force_extra")
    if extra.strip():
        e = await services.send_rich(bot, user_id, "force_extra", extra)
        # remember the pair, so pressing Claim on THIS prompt can delete exactly these two messages
        await db.execute(
            "INSERT INTO prompt_extras(user_id, prompt_id, extra_id) VALUES(:u, :p, :e) "
            "ON CONFLICT(user_id, prompt_id) DO UPDATE SET extra_id=excluded.extra_id",
            u=user_id, p=sent.message_id, e=e.message_id,
        )


class GateMiddleware(BaseMiddleware):
    """Registers users, drops banned users, handles bot ON/OFF and force-join."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        if isinstance(event, Message) and event.chat.type != "private" and user.id not in config.ADMINS:
            return  # ignore groups for non-admins

        is_new = await services.upsert_user(user)
        if is_new and user.id not in config.ADMINS:
            t = asyncio.create_task(services.notify_new_user(data["bot"], user))
            _bg.add(t)
            t.add_done_callback(_bg.discard)

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

        await send_join_prompt(bot, user.id, await services.prompt_channels(missing))
        if isinstance(event, CallbackQuery):
            await event.answer()
