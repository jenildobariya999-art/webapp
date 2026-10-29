"""Shared logic: settings, users, force-join checking."""
import asyncio
import logging
import time

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

import config
import db

log = logging.getLogger(__name__)

DEFAULTS = {
    "force_join": "1",
    "auto_approve": "0",
    "welcome": "👋 Welcome! You're all set to use the bot.",
    "force_text": "🔒 To use this bot, please join the channels below, then press <b>✅ I've joined</b>.",
}


async def get_setting(key: str) -> str:
    row = await db.fetchone("SELECT value FROM settings WHERE key=:k", k=key)
    return row["value"] if row else DEFAULTS[key]


async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key, value) VALUES(:k, :v) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        k=key, v=value,
    )


async def upsert_user(user):
    await db.execute(
        "INSERT INTO users(id, username, first_name) VALUES(:i, :u, :f) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, blocked=0",
        i=user.id, u=user.username, f=user.first_name,
    )


async def load_admins():
    rows = await db.fetch("SELECT user_id FROM admins")
    config.ADMINS.clear()
    config.ADMINS.update(config.OWNERS)
    config.ADMINS.update(r["user_id"] for r in rows)


# ── force-join checking ─────────────────────────────────────────
_OK = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
_ok_until: dict[int, float] = {}
CACHE_TTL = 15  # seconds; only "all joined" results are cached


def invalidate(user_id: int | None = None):
    if user_id is None:
        _ok_until.clear()
    else:
        _ok_until.pop(user_id, None)


def mark_ok(user_id: int):
    _ok_until[user_id] = time.monotonic() + CACHE_TTL


async def _satisfied(bot: Bot, ch: dict, user_id: int, pending: set[int]) -> bool:
    try:
        m = await bot.get_chat_member(ch["chat_id"], user_id)
    except Exception as e:  # bot kicked / not admin / network -> fail open, never lock users out
        log.warning("Cannot check %s (%s): %s", ch["chat_id"], ch["title"], e)
        return True
    if m.status in _OK or (m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False)):
        return True
    return bool(ch["request_mode"] and ch["chat_id"] in pending)


async def missing_channels(bot: Bot, user_id: int, use_cache: bool = True) -> list[dict]:
    if use_cache and _ok_until.get(user_id, 0) > time.monotonic():
        return []
    channels = await db.fetch("SELECT * FROM channels ORDER BY added, chat_id")
    if not channels:
        return []
    rows = await db.fetch("SELECT chat_id FROM join_requests WHERE user_id=:u", u=user_id)
    pending = {r["chat_id"] for r in rows}
    results = await asyncio.gather(*(_satisfied(bot, ch, user_id, pending) for ch in channels))
    missing = [ch for ch, ok in zip(channels, results) if not ok]
    if not missing:
        mark_ok(user_id)
    return missing
