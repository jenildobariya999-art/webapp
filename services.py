"""Shared logic: settings, users, wallet helpers, force-join checking."""
import asyncio
import html
import logging
import time
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

import config
import db

log = logging.getLogger(__name__)

DEFAULTS = {
    # switches
    "bot_enabled": "1",
    "force_join": "1",
    "auto_approve": "0",
    "withdraw_on": "1",
    "join_mode": "all",          # all = must join every channel, any = one is enough
    # numbers
    "refer_reward": "5",
    "bonus_amount": "2",
    "bonus_hours": "24",
    "min_withdraw": "50",
    # button labels (plain text). {title} = channel name, {n} = channel number
    "join_btn": "➕ Join",
    "check_btn": "✅ I've joined",
    "btn_balance": "💰 Balance",
    "btn_refer": "👥 Refer & Earn",
    "btn_bonus": "🎁 Bonus",
    "btn_withdraw": "💸 Withdrawal",
    "btn_upi": "🏦 Link UPI",
    # texts (HTML / premium emoji allowed)
    "welcome": (
        "🎉 <b>Welcome, {name}!</b>\n\n"
        "You're verified and ready to go 🚀\n\n"
        "💰 Earn rewards by inviting friends\n"
        "🎁 Claim your bonus regularly\n"
        "💸 Withdraw straight to your UPI\n\n"
        "👇 <i>Pick an option from the menu below.</i>"
    ),
    "force_text": (
        "🔒 <b>Join required</b>\n\n"
        "To unlock the bot, please join all the channels below, "
        "then tap <b>✅ I've joined</b>.\n\n"
        "📨 <i>For request channels, sending a join request is enough!</i>"
    ),
    "force_extra": "",           # optional 2nd message sent right after the force-join message
    "off_text": (
        "🛠 <b>We'll be right back!</b>\n\n"
        "The bot is under maintenance at the moment.\n"
        "Please try again in a little while 🙏"
    ),
    "refer_text": (
        "👥 <b>Refer & Earn</b>\n\n"
        "Invite your friends and earn <b>₹{reward}</b> for every friend who joins using your link! 🎁\n\n"
        "🔗 <b>Your personal link</b>\n{link}\n\n"
        "👤 Total referrals: <b>{refs}</b>\n\n"
        "<i>Tap the button below to share it.</i>"
    ),
}


# ── settings (cached in memory; the DB is the source of truth) ───
_cache: dict[str, str] = {}


async def load_settings():
    rows = await db.fetch("SELECT key, value FROM settings")
    _cache.clear()
    _cache.update({r["key"]: r["value"] for r in rows})


def setting(key: str) -> str:
    """Sync read (used for keyboards). Falls back to the built-in default."""
    return _cache.get(key, DEFAULTS.get(key, ""))


async def get_setting(key: str) -> str:
    return setting(key)


async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key, value) VALUES(:k, :v) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        k=key, v=value,
    )
    _cache[key] = value


async def reset_setting(key: str):
    """Back to the built-in default."""
    await db.execute("DELETE FROM settings WHERE key=:k", k=key)
    _cache.pop(key, None)


async def send_rich(bot: Bot, chat_id: int, key: str, text: str, reply_markup=None):
    """Send `text`; if a photo is attached to `key` (admin set one) send it as a photo with caption."""
    photo = setting(f"media:{key}")
    if photo:
        try:
            return await bot.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup)
        except TelegramBadRequest as e:  # e.g. caption longer than 1024 chars
            log.info("photo+caption failed (%s) – sending photo and text separately", e)
            try:
                await bot.send_photo(chat_id, photo)
            except TelegramBadRequest:
                pass
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


# ── money helpers (balances are stored in paise) ────────────────
def fmt(p) -> str:
    p = int(p or 0)
    sign = "-" if p < 0 else ""
    p = abs(p)
    return f"{sign}{p // 100}" + (f".{p % 100:02d}" if p % 100 else "")


def to_paise(s: str):
    try:
        d = Decimal(str(s).strip().replace("₹", "").replace(",", ""))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * 100).to_integral_value())


async def money_setting(key: str) -> int:
    return to_paise(await get_setting(key)) or 0


async def int_setting(key: str) -> int:
    try:
        return int(float(await get_setting(key)))
    except ValueError:
        return int(float(DEFAULTS[key]))


# ── users ───────────────────────────────────────────────────────
async def upsert_user(user, ref: int | None = None):
    """Create/update a user. `ref` (referrer id) is stored only when the user is new."""
    referred_by = None
    if ref and ref != user.id and await db.fetchone("SELECT 1 AS x FROM users WHERE id=:r", r=ref):
        referred_by = ref
    await db.execute(
        "INSERT INTO users(id, username, first_name, referred_by) VALUES(:i, :u, :f, :r) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, blocked=0",
        i=user.id, u=user.username, f=user.first_name, r=referred_by,
    )


async def get_user(uid: int):
    return await db.fetchone("SELECT * FROM users WHERE id=:i", i=uid)


def render(tpl: str, u: dict | None, **extra) -> str:
    """Fill {placeholders} in admin-editable texts."""
    vals = {
        "name": html.escape((u or {}).get("first_name") or "friend"),
        "id": str(u["id"]) if u else "",
        "balance": fmt(u["balance"]) if u else "0",
        "refs": str(u["refs"]) if u else "0",
    }
    vals.update(extra)
    for k, v in vals.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


async def credit_referral(bot: Bot, uid: int):
    """Pay the referrer once, after the new user passed force-join. Safe to call many times."""
    u = await get_user(uid)
    if not u or not u["referred_by"] or u["ref_paid"]:
        return
    if not await db.execute("UPDATE users SET ref_paid=1 WHERE id=:u AND ref_paid=0", u=uid):
        return
    reward = await money_setting("refer_reward")
    await db.execute(
        "UPDATE users SET balance=balance+:r, refs=refs+1 WHERE id=:ref", r=reward, ref=u["referred_by"]
    )
    try:
        await bot.send_message(
            u["referred_by"],
            "🎉 <b>New referral!</b>\n\n"
            f"{html.escape(u['first_name'] or 'Someone')} joined using your link.\n"
            f"💰 <b>+₹{fmt(reward)}</b> added to your wallet.",
        )
    except Exception:
        pass


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
    channels = await db.fetch("SELECT * FROM channels WHERE unchecked=0 ORDER BY pos, added, chat_id")
    if not channels:
        return []
    rows = await db.fetch("SELECT chat_id FROM join_requests WHERE user_id=:u", u=user_id)
    pending = {r["chat_id"] for r in rows}
    results = await asyncio.gather(*(_satisfied(bot, ch, user_id, pending) for ch in channels))
    if setting("join_mode") == "any" and any(results):
        missing = []  # "any one channel" mode: one joined channel is enough
    else:
        missing = [ch for ch, ok in zip(channels, results) if not ok]
    if not missing:
        mark_ok(user_id)
    return missing


async def unchecked_channels() -> list[dict]:
    """Channels that are only displayed (membership is never verified)."""
    return await db.fetch("SELECT * FROM channels WHERE unchecked=1 ORDER BY pos, added, chat_id")


def merge_channels(*groups) -> list[dict]:
    allc = [c for g in groups for c in g]
    return sorted(allc, key=lambda c: (c["pos"], str(c["added"]), c["chat_id"]))
