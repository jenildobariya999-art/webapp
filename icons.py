"""Premium (custom) emoji on buttons.

Telegram shows a custom emoji in front of a button's text via `icon_custom_emoji_id`.
Icon ids are stored in the settings table as  icon:<key>  and cached in memory.
If Telegram rejects an icon (e.g. the bot owner has no Premium) the request is
automatically retried WITHOUT icons, so buttons never break.
"""
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, Message

import services

log = logging.getLogger(__name__)

# icons the admin can customise on the channel panel (key -> label shown in the editor)
PANEL_ICONS = {
    "del": "❌ Delete",
    "up": "⬆️ Move up",
    "down": "⬇️ Move down",
    "view": "👀 View",
    "add": "➕ Add channel",
    "social": "🔗 Add social link",
    "unc": "☑️ Add uncheck channel",
    "stats": "📊 Joined stats",
    "cbc": "📡 Channel broadcast",
    "mode": "🎛 Join mode",
    "back": "⬅️ Back",
}


def icon(key: str) -> str | None:
    return services.setting(f"icon:{key}") or None


async def set_icon(key: str, emoji_id: str):
    await services.set_setting(f"icon:{key}", emoji_id)


async def clear_icon(key: str):
    await services.reset_setting(f"icon:{key}")


def btn(text: str, *, cb: str | None = None, url: str | None = None, icon_id: str | None = None) -> InlineKeyboardButton:
    """Inline button, optionally with a premium-emoji icon."""
    extra = {"icon_custom_emoji_id": icon_id} if icon_id else {}
    return InlineKeyboardButton(text=text, callback_data=cb, url=url, **extra)


def pbtn(key: str, emoji: str, text: str = "", *, cb: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    """Panel button. No icon set -> normal emoji + text. Icon set -> premium icon (+ text, or invisible text)."""
    iid = icon(key)
    if iid:
        return btn(text or "\u2800", cb=cb, url=url, icon_id=iid)
    return btn(f"{emoji} {text}".strip(), cb=cb, url=url)


# ── reading premium emoji out of an admin message ───────────────
def custom_emoji_ids(m: Message) -> list[str]:
    ents = m.entities or m.caption_entities or []
    return [e.custom_emoji_id for e in ents if e.type == "custom_emoji"]


def split_label(m: Message) -> tuple[str, str | None]:
    """Return (text without premium emoji, id of the first premium emoji)."""
    text = m.text or m.caption or ""
    ents = sorted(
        (e for e in (m.entities or m.caption_entities or []) if e.type == "custom_emoji"),
        key=lambda e: e.offset,
    )
    if not ents:
        return " ".join(text.split()), None
    raw = text.encode("utf-16-le")  # entity offsets are in UTF-16 code units
    out, last = bytearray(), 0
    for e in ents:
        out += raw[last * 2:e.offset * 2]
        last = e.offset + e.length
    out += raw[last * 2:]
    return " ".join(out.decode("utf-16-le").split()), ents[0].custom_emoji_id


# ── safety net: retry without icons if Telegram refuses them ────
def _has_icons(markup) -> bool:
    try:
        return "icon_custom_emoji_id" in str(markup.model_dump(exclude_none=True))
    except Exception:
        return False


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k != "icon_custom_emoji_id"}
    if isinstance(node, list):
        return [_strip(x) for x in node]
    return node


class IconFallback(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            markup = getattr(method, "reply_markup", None)
            if markup is not None and _has_icons(markup):
                log.warning("Button icon rejected by Telegram (%s) – retrying without icons", e)
                clean = type(markup).model_validate(_strip(markup.model_dump(exclude_none=True)))
                return await make_request(bot, method.model_copy(update={"reply_markup": clean}))
            raise
