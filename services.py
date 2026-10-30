"""Shared logic: settings, users, wallet helpers, force-join checking."""
import asyncio
import html
import logging
import time

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
    "join_mode": "all",          # all = show every channel, not_joined = show only channels the user hasn't joined
    "notify_new_user": "1",      # tell admins when a new user starts the bot
    # button colours: "", "primary" (blue), "success" (green), "danger" (red)
    "style:join_btn": "primary",
    "style:check_btn": "success",
    # button labels (plain text). {title} = channel name, {n} = channel number
    "join_btn": "➕ Join",
    "check_btn": "✅ Claim",
    # texts (HTML / premium emoji allowed)
    "welcome": (
        "🎉 <b>Welcome, {name}!</b>\n\n"
        "You're verified and ready to go 🚀\n\n"
        "👇 <i>Pick an option from the menu below.</i>"
    ),
    "force_text": (
        "🔒 <b>Join required</b>\n\n"
        "To unlock the bot, please join all the channels below, "
        "then tap <b>✅ Claim</b>.\n\n"
        "📨 <i>For request channels, sending a join request is enough!</i>"
    ),
    "force_extra": "",           # optional 2nd message sent right after the force-join message
    "off_text": (
        "🛠 <b>We'll be right back!</b>\n\n"
        "The bot is under maintenance at the moment.\n"
        "Please try again in a little while 🙏"
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


async def send_photo_text(bot: Bot, chat_id: int, photo: str | None, text: str, reply_markup=None):
    """Send `text`, as a photo caption when `photo` is given (falls back to two messages if the caption is too long)."""
    if photo:
        try:
            return await bot.send_photo(chat_id, photo, caption=text or None, reply_markup=reply_markup)
        except TelegramBadRequest as e:  # e.g. caption longer than 1024 chars
            log.info("photo+caption failed (%s) – sending photo and text separately", e)
            try:
                await bot.send_photo(chat_id, photo)
            except TelegramBadRequest:
                pass
    return await bot.send_message(chat_id, text or "👍", reply_markup=reply_markup)


async def send_rich(bot: Bot, chat_id: int, key: str, text: str, reply_markup=None):
    """Send the text of setting `key`; if the admin attached a photo to it, send it as a photo with caption."""
    return await send_photo_text(bot, chat_id, setting(f"media:{key}"), text, reply_markup)


# ── custom menu buttons (label + reply, managed from the admin panel) ──
MENU: list[dict] = []  # cache, so the keyboard can be built synchronously

DEFAULT_MENU = [
    # (label, reply, starts a new line?)
    ("New Yono App",
     "🎮 <b>New Yono App</b>\n\nThe download link will be shared here very soon. Stay tuned! 🔔", 1),
    ("Lucky Gullak",
     "🍀 <b>Lucky Gullak</b>\n\nDetails will be shared here very soon. Stay tuned! 🔔", 1),
    ("Big Promo Code",
     "🎁 <b>Big Promo Code</b>\n\nYour promo code will be shared here very soon. Stay tuned! 🔔", 0),  # same line as Lucky Gullak
]
OLD_DEFAULT_LABELS = ["New yono app", "Lucky Gullak | Big Promo code"]  # the previous 2-button default menu
MENU_MAX_PER_LINE = 3


def layout_rows(buttons: list[dict], cap: int = MENU_MAX_PER_LINE) -> list[list[dict]]:
    """Group menu buttons into lines: a button with newline=1 starts a new line, newline=0 joins the previous one."""
    rows: list[list[dict]] = []
    for b in buttons:
        if not rows or b["newline"] or len(rows[-1]) >= cap:
            rows.append([b])
        else:
            rows[-1].append(b)
    return rows


async def load_menu():
    rows = await db.fetch("SELECT * FROM menu_buttons ORDER BY pos, id")
    MENU[:] = rows


async def seed_menu():
    """First start: create the 3 default buttons. Upgrade: turn the old 2-button default into the new 3 buttons."""
    ver = setting("menu_seeded")
    if ver == "2":
        return
    rows = await db.fetch("SELECT * FROM menu_buttons ORDER BY pos, id")
    if not rows and ver != "1":  # brand-new install
        for i, (label, reply, nl) in enumerate(DEFAULT_MENU, 1):
            await db.execute(
                "INSERT INTO menu_buttons(label, reply, pos, newline) VALUES(:l, :r, :p, :n)",
                l=label, r=reply, p=i, n=nl,
            )
    elif ver == "1" and [r["label"] for r in rows] == OLD_DEFAULT_LABELS:
        # untouched old default menu -> New Yono App / Lucky Gullak / Big Promo Code (keeps any reply you edited)
        await db.execute("UPDATE menu_buttons SET label=:l, newline=1 WHERE id=:i", l="New Yono App", i=rows[0]["id"])
        await db.execute("UPDATE menu_buttons SET label=:l, newline=1 WHERE id=:i", l="Lucky Gullak", i=rows[1]["id"])
        label, reply, nl = DEFAULT_MENU[2]
        await db.execute(
            "INSERT INTO menu_buttons(label, reply, pos, newline) VALUES(:l, :r, :p, :n)",
            l=label, r=reply, p=3, n=nl,
        )
    await set_setting("menu_seeded", "2")


async def delete_prompt(bot: Bot, uid: int, prompt_id: int):
    """Delete ONE force-join message and the extra message that was sent with it (nothing else)."""
    row = await db.fetchone(
        "SELECT extra_id FROM prompt_extras WHERE user_id=:u AND prompt_id=:p", u=uid, p=prompt_id
    )
    for mid in ([row["extra_id"]] if row else []) + [prompt_id]:
        try:
            await bot.delete_message(uid, int(mid))
        except Exception:
            pass  # already deleted / too old
    await db.execute("DELETE FROM prompt_extras WHERE user_id=:u AND prompt_id=:p", u=uid, p=prompt_id)


# ── users ───────────────────────────────────────────────────────
async def upsert_user(user) -> bool:
    """Create/update a user. Returns True the first time we see this user."""
    existed = await db.fetchone("SELECT 1 AS x FROM users WHERE id=:i", i=user.id)
    await db.execute(
        "INSERT INTO users(id, username, first_name) VALUES(:i, :u, :f) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, blocked=0",
        i=user.id, u=user.username, f=user.first_name,
    )
    return existed is None


async def notify_new_user(bot: Bot, user):
    """Tell every admin that someone new started the bot (switchable in Settings)."""
    if setting("notify_new_user") != "1":
        return
    total = await db.scalar("SELECT COUNT(*) FROM users")
    uname = f" (@{user.username})" if user.username else ""
    text = (
        "🆕 <b>New user!</b>\n\n"
        f"👤 {html.escape(user.full_name)}{uname}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"👥 Total users: <b>{total}</b>"
    )
    for aid in list(config.ADMINS):
        if aid == user.id:
            continue
        try:
            await bot.send_message(aid, text)
        except Exception:
            pass


async def get_user(uid: int):
    return await db.fetchone("SELECT * FROM users WHERE id=:i", i=uid)


def render(tpl: str, u: dict | None, **extra) -> str:
    """Fill {name} / {id} (and any extra) placeholders in admin-editable texts."""
    vals = {
        "name": html.escape((u or {}).get("first_name") or "friend"),
        "id": str(u["id"]) if u else "",
    }
    vals.update(extra)
    for k, v in vals.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


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
    return ch["chat_id"] in pending  # a pending join request counts as joined


async def missing_channels(bot: Bot, user_id: int, use_cache: bool = True) -> list[dict]:
    if use_cache and _ok_until.get(user_id, 0) > time.monotonic():
        return []
    channels = await db.fetch("SELECT * FROM channels WHERE unchecked=0 ORDER BY pos, added, chat_id")
    if not channels:
        return []
    rows = await db.fetch("SELECT chat_id FROM join_requests WHERE user_id=:u", u=user_id)
    pending = {r["chat_id"] for r in rows}
    results = await asyncio.gather(*(_satisfied(bot, ch, user_id, pending) for ch in channels))
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


async def prompt_channels(missing: list[dict]) -> list[dict]:
    """Which channels the force-join message shows.
    Join Mode 'all' = every channel, 'not_joined' = only the ones the user still has to join (+ display-only ones)."""
    if setting("join_mode") == "not_joined":
        return merge_channels(missing, await unchecked_channels())
    return merge_channels(await db.fetch("SELECT * FROM channels ORDER BY pos, added, chat_id"))


def join_url(ch: dict) -> str:
    """The link a user gets: request link when the channel is in request mode, else the normal link."""
    if ch.get("request_mode") and ch.get("req_link"):
        return ch["req_link"]
    return ch["link"]
