"""Premium (custom) emoji on the Join / Claim / social-link buttons.

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

def icon(key: str) -> str | None:
    return services.setting(f"icon:{key}") or None


async def set_icon(key: str, emoji_id: str):
    await services.set_setting(f"icon:{key}", emoji_id)


async def clear_icon(key: str):
    await services.reset_setting(f"icon:{key}")


STYLES = ("primary", "success", "danger")  # blue / green / red buttons


def style_of(key: str) -> str | None:
    """Colour chosen in the admin panel for the button `key` (None = Telegram's default look)."""
    v = services.setting(f"style:{key}")
    return v if v in STYLES else None


def btn(text: str, *, cb: str | None = None, url: str | None = None,
        icon_id: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    """Inline button, optionally with a premium-emoji icon and a colour style."""
    extra = {}
    if icon_id:
        extra["icon_custom_emoji_id"] = icon_id
    if style in STYLES:
        extra["style"] = style
    return InlineKeyboardButton(text=text, callback_data=cb, url=url, **extra)


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
        dumped = str(markup.model_dump(exclude_none=True))
        return "icon_custom_emoji_id" in dumped or "'style'" in dumped
    except Exception:
        return False


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in ("icon_custom_emoji_id", "style")}
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
                log.warning("Button icon/style rejected by Telegram (%s) – retrying without them", e)
                clean = type(markup).model_validate(_strip(markup.model_dump(exclude_none=True)))
                return await make_request(bot, method.model_copy(update={"reply_markup": clean}))
            raise
