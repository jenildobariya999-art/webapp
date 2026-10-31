"""core.py – config, database, per-bot settings/menu and helpers shared by every bot."""
import asyncio
import hashlib
import hmac
import html
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (InlineKeyboardButton, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)
from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

log = logging.getLogger("core")
load_dotenv()

# ═══════════════════════════ config ═══════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()                  # optional: your first bot
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()  # web dashboard login
if not BOT_TOKEN and not DASHBOARD_PASSWORD:
    raise SystemExit("Set DASHBOARD_PASSWORD (to manage bots from the web dashboard) and/or BOT_TOKEN.")

OWNERS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.lstrip("-").isdigit()}
ADMINS = set(OWNERS)  # owners + admins stored in the DB (same admins for every bot)

SECRET_KEY = os.getenv("SECRET_KEY") or hashlib.sha256(("sk:" + (DASHBOARD_PASSWORD or BOT_TOKEN)).encode()).hexdigest()
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
ALLOWED_UPDATES = ["message", "callback_query", "chat_join_request", "chat_member"]


def webhook_secret(bid: int) -> str:
    return hmac.new(SECRET_KEY.encode(), str(bid).encode(), hashlib.sha256).hexdigest()[:48]


def _normalize_db_url(url: str) -> str:
    if not url:
        return "sqlite+aiosqlite:///bot.db"
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+asyncpg://"):
        parts = urlsplit(url)  # asyncpg uses ssl=..., not sslmode=..., and doesn't know channel_binding
        qs = [("ssl" if k == "sslmode" else k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k != "channel_binding"]
        url = urlunsplit(parts._replace(query=urlencode(qs)))
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "").strip())

# ═══════════════════════════ database ═══════════════════════════
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)


def _schema() -> list[str]:
    pg = engine.dialect.name == "postgresql"
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        """CREATE TABLE IF NOT EXISTS bots(
            id BIGINT PRIMARY KEY, token TEXT NOT NULL, username TEXT, name TEXT,
            active INTEGER NOT NULL DEFAULT 1, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS bot_users(
            bot_id BIGINT NOT NULL, id BIGINT NOT NULL, username TEXT, first_name TEXT,
            blocked INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(bot_id, id))""",
        "CREATE INDEX IF NOT EXISTS ix_bot_users_created ON bot_users(bot_id, created)",
        """CREATE TABLE IF NOT EXISTS bot_settings(
            bot_id BIGINT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(bot_id, key))""",
        """CREATE TABLE IF NOT EXISTS bot_channels(
            bot_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, title TEXT, link TEXT NOT NULL, req_link TEXT,
            request_mode INTEGER NOT NULL DEFAULT 0, unchecked INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL DEFAULT 'channel', pos INTEGER NOT NULL DEFAULT 0, icon TEXT,
            added TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(bot_id, chat_id))""",
        """CREATE TABLE IF NOT EXISTS bot_requests(
            bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(bot_id, user_id, chat_id))""",
        f"""CREATE TABLE IF NOT EXISTS bot_menu(
            id {pk}, bot_id BIGINT NOT NULL, label TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
            photo TEXT, style TEXT NOT NULL DEFAULT '', pos INTEGER NOT NULL DEFAULT 0,
            newline INTEGER NOT NULL DEFAULT 1)""",
        """CREATE TABLE IF NOT EXISTS bot_prompts(
            bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL, prompt_id BIGINT NOT NULL, extra_id BIGINT NOT NULL,
            PRIMARY KEY(bot_id, user_id, prompt_id))""",
        "CREATE TABLE IF NOT EXISTS admins(user_id BIGINT PRIMARY KEY)",
    ]


async def init():
    async with engine.begin() as conn:
        for stmt in _schema():
            await conn.execute(text(stmt))


async def fetch(sql: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        res = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in res.fetchall()]


async def fetchone(sql: str, **params):
    rows = await fetch(sql, **params)
    return rows[0] if rows else None


async def scalar(sql: str, **params):
    async with engine.connect() as conn:
        res = await conn.execute(text(sql), params)
        return res.scalar()


async def execute(sql: str, **params) -> int:
    """Run a write statement, returns number of affected rows."""
    async with engine.begin() as conn:
        res = await conn.execute(text(sql), params)
        return res.rowcount or 0


async def insert_returning(sql: str, **params):
    async with engine.begin() as conn:
        res = await conn.execute(text(sql + " RETURNING id"), params)
        return res.scalar()


async def close():
    await engine.dispose()


def ts(dt: datetime):
    """A UTC datetime in the form the current database wants as a query parameter."""
    return dt if engine.dialect.name == "postgresql" else dt.strftime("%Y-%m-%d %H:%M:%S")


async def migrate_legacy(bid: int):
    """Old single-bot installs: copy users/channels/settings/menu into the new per-bot tables (once)."""
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    if "users" not in names or setting(bid, "legacy_migrated") == "1":
        return
    b = int(bid)
    copies = [
        f"INSERT INTO bot_users(bot_id,id,username,first_name,blocked,banned,verified,created) "
        f"SELECT {b},id,username,first_name,blocked,banned,verified,created FROM users",
        f"INSERT INTO bot_channels(bot_id,chat_id,title,link,req_link,request_mode,unchecked,kind,pos,icon,added) "
        f"SELECT {b},chat_id,title,link,req_link,request_mode,unchecked,kind,pos,icon,added FROM channels",
        f"INSERT INTO bot_requests(bot_id,user_id,chat_id,created) SELECT {b},user_id,chat_id,created FROM join_requests",
        f"INSERT INTO bot_settings(bot_id,key,value) SELECT {b},key,value FROM settings",
        f"INSERT INTO bot_menu(bot_id,label,reply,photo,style,pos,newline) "
        f"SELECT {b},label,reply,photo,style,pos,newline FROM menu_buttons",
    ]
    for sql in copies:
        try:
            await execute(sql)
        except Exception as e:  # missing table/column in a very old schema – skip that part
            log.warning("legacy copy skipped: %s", str(e)[:120])
    await set_setting(bid, "legacy_migrated", "1")
    await load_bot_cache(bid)
    log.info("Legacy data copied to bot %s", bid)


# ═══════════════════════════ per-bot settings ═══════════════════════════
DEFAULTS = {
    "bot_enabled": "1",
    "force_join": "1",
    "auto_approve": "0",
    "join_mode": "all",          # all = show every channel, not_joined = only channels the user hasn't joined
    "notify_new_user": "1",      # tell admins when a new user starts the bot
    "style:join_btn": "primary",
    "style:check_btn": "success",
    "join_btn": "➕ Join",
    "check_btn": "✅ Claim",
    "welcome": (
        "🎉 <b>Welcome, {name}!</b>\n\n"
        "You're verified and ready to go 🚀\n\n"
        "👇 <i>Pick an option from the menu below.</i>"
    ),
    "force_text": (
        "🔒 <b>Join required</b>\n\n"
        "To unlock the bot, please join all the channels below, then tap <b>✅ Claim</b>.\n\n"
        "📨 <i>For request channels, sending a join request is enough!</i>"
    ),
    "force_extra": "",           # optional 2nd message sent right after the force-join message
    "off_text": (
        "🛠 <b>We'll be right back!</b>\n\n"
        "The bot is under maintenance at the moment.\nPlease try again in a little while 🙏"
    ),
}

S: dict[int, dict[str, str]] = {}      # settings cache   {bot_id: {key: value}}
MENU: dict[int, list[dict]] = {}       # menu-button cache {bot_id: [button, …]}


async def load_bot_cache(bid: int):
    rows = await fetch("SELECT key, value FROM bot_settings WHERE bot_id=:b", b=bid)
    S[bid] = {r["key"]: r["value"] for r in rows}
    MENU[bid] = await fetch("SELECT * FROM bot_menu WHERE bot_id=:b ORDER BY pos, id", b=bid)


def setting(bid: int, key: str) -> str:
    return S.get(bid, {}).get(key, DEFAULTS.get(key, ""))


async def get_setting(bid: int, key: str) -> str:
    return setting(bid, key)


async def set_setting(bid: int, key: str, value: str):
    await execute(
        "INSERT INTO bot_settings(bot_id, key, value) VALUES(:b, :k, :v) "
        "ON CONFLICT(bot_id, key) DO UPDATE SET value=excluded.value", b=bid, k=key, v=value)
    S.setdefault(bid, {})[key] = value


async def reset_setting(bid: int, key: str):
    await execute("DELETE FROM bot_settings WHERE bot_id=:b AND key=:k", b=bid, k=key)
    S.setdefault(bid, {}).pop(key, None)


async def load_menu(bid: int):
    MENU[bid] = await fetch("SELECT * FROM bot_menu WHERE bot_id=:b ORDER BY pos, id", b=bid)


# ── default menu ──
DEFAULT_MENU = [
    ("New Yono App", "🎮 <b>New Yono App</b>\n\nThe download link will be shared here very soon. Stay tuned! 🔔", 1),
    ("Lucky Gullak", "🍀 <b>Lucky Gullak</b>\n\nDetails will be shared here very soon. Stay tuned! 🔔", 1),
    ("Big Promo Code", "🎁 <b>Big Promo Code</b>\n\nYour promo code will be shared here very soon. Stay tuned! 🔔", 0),
]
OLD_DEFAULT_LABELS = ["New yono app", "Lucky Gullak | Big Promo code"]
MENU_MAX_PER_LINE = 3


def layout_rows(buttons: list[dict], cap: int = MENU_MAX_PER_LINE) -> list[list[dict]]:
    """newline=1 starts a new line, newline=0 joins the previous button's line (max `cap` per line)."""
    rows: list[list[dict]] = []
    for b in buttons:
        if not rows or b["newline"] or len(rows[-1]) >= cap:
            rows.append([b])
        else:
            rows[-1].append(b)
    return rows


async def seed_menu(bid: int):
    """New bot: create the 3 default buttons. Legacy 2-button default menu: upgrade it to 3 buttons."""
    ver = setting(bid, "menu_seeded")
    if ver != "2":
        rows = await fetch("SELECT * FROM bot_menu WHERE bot_id=:b ORDER BY pos, id", b=bid)
        if not rows and ver != "1":
            for i, (label, reply, nl) in enumerate(DEFAULT_MENU, 1):
                await execute("INSERT INTO bot_menu(bot_id, label, reply, pos, newline) VALUES(:b, :l, :r, :p, :n)",
                              b=bid, l=label, r=reply, p=i, n=nl)
        elif ver == "1" and [r["label"] for r in rows] == OLD_DEFAULT_LABELS:
            await execute("UPDATE bot_menu SET label=:l, newline=1 WHERE id=:i", l="New Yono App", i=rows[0]["id"])
            await execute("UPDATE bot_menu SET label=:l, newline=1 WHERE id=:i", l="Lucky Gullak", i=rows[1]["id"])
            label, reply, nl = DEFAULT_MENU[2]
            await execute("INSERT INTO bot_menu(bot_id, label, reply, pos, newline) VALUES(:b, :l, :r, 3, :n)",
                          b=bid, l=label, r=reply, n=nl)
        await set_setting(bid, "menu_seeded", "2")
    await load_menu(bid)


# ═══════════════════════════ sending helpers ═══════════════════════════
async def send_photo_text(bot: Bot, chat_id: int, photo: str | None, text: str, reply_markup=None):
    """Text, or a photo with caption (falls back to two messages if the caption is too long)."""
    if photo:
        try:
            return await bot.send_photo(chat_id, photo, caption=text or None, reply_markup=reply_markup)
        except TelegramBadRequest as e:
            log.info("photo+caption failed (%s) – sending separately", e)
            try:
                await bot.send_photo(chat_id, photo)
            except TelegramBadRequest:
                pass
    return await bot.send_message(chat_id, text or "👍", reply_markup=reply_markup)


async def send_rich(bot: Bot, chat_id: int, key: str, text: str, reply_markup=None):
    """Send the text of setting `key` (as a photo caption if the admin attached a photo to it)."""
    return await send_photo_text(bot, chat_id, setting(bot.id, f"media:{key}"), text, reply_markup)


async def delete_prompt(bot: Bot, uid: int, prompt_id: int):
    """Delete ONE force-join message and the extra message that was sent with it (nothing else)."""
    row = await fetchone("SELECT extra_id FROM bot_prompts WHERE bot_id=:b AND user_id=:u AND prompt_id=:p",
                         b=bot.id, u=uid, p=prompt_id)
    for mid in ([row["extra_id"]] if row else []) + [prompt_id]:
        try:
            await bot.delete_message(uid, int(mid))
        except Exception:
            pass
    await execute("DELETE FROM bot_prompts WHERE bot_id=:b AND user_id=:u AND prompt_id=:p", b=bot.id, u=uid, p=prompt_id)


# ═══════════════════════════ users ═══════════════════════════
async def upsert_user(bid: int, user) -> bool:
    """Create/update a user of bot `bid`. True the first time we see this user."""
    existed = await fetchone("SELECT 1 AS x FROM bot_users WHERE bot_id=:b AND id=:i", b=bid, i=user.id)
    await execute(
        "INSERT INTO bot_users(bot_id, id, username, first_name) VALUES(:b, :i, :u, :f) "
        "ON CONFLICT(bot_id, id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, blocked=0",
        b=bid, i=user.id, u=user.username, f=user.first_name)
    return existed is None


async def get_user(bid: int, uid: int):
    return await fetchone("SELECT * FROM bot_users WHERE bot_id=:b AND id=:i", b=bid, i=uid)


def render(tpl: str, u: dict | None, **extra) -> str:
    """Fill {name} / {id} (and any extra) placeholders in admin-editable texts."""
    vals = {"name": html.escape((u or {}).get("first_name") or "friend"), "id": str(u["id"]) if u else ""}
    vals.update(extra)
    for k, v in vals.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


async def notify_new_user(bot: Bot, user):
    """Tell every admin that someone new started this bot (switchable per bot in Settings)."""
    if setting(bot.id, "notify_new_user") != "1":
        return
    total = await scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:b", b=bot.id)
    try:
        me = (await bot.me()).username
    except Exception:
        me = str(bot.id)
    uname = f" (@{user.username})" if user.username else ""
    text = (f"🆕 <b>New user</b> on @{me}\n\n👤 {html.escape(user.full_name)}{uname}\n"
            f"🆔 <code>{user.id}</code>\n\n👥 Total users: <b>{total}</b>")
    for aid in list(ADMINS):
        if aid != user.id:
            try:
                await bot.send_message(aid, text)
            except Exception:
                pass


async def load_admins():
    rows = await fetch("SELECT user_id FROM admins")
    ADMINS.clear()
    ADMINS.update(OWNERS)
    ADMINS.update(r["user_id"] for r in rows)


# ═══════════════════════════ force-join ═══════════════════════════
_OK = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
_ok_until: dict[tuple[int, int], float] = {}
CACHE_TTL = 15  # seconds; only "all joined" results are cached


def invalidate(bid: int, user_id: int | None = None):
    if user_id is None:
        for k in [k for k in _ok_until if k[0] == bid]:
            _ok_until.pop(k, None)
    else:
        _ok_until.pop((bid, user_id), None)


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
    bid = bot.id
    if use_cache and _ok_until.get((bid, user_id), 0) > time.monotonic():
        return []
    channels = await fetch("SELECT * FROM bot_channels WHERE bot_id=:b AND unchecked=0 ORDER BY pos, added, chat_id", b=bid)
    if not channels:
        return []
    rows = await fetch("SELECT chat_id FROM bot_requests WHERE bot_id=:b AND user_id=:u", b=bid, u=user_id)
    pending = {r["chat_id"] for r in rows}
    results = await asyncio.gather(*(_satisfied(bot, ch, user_id, pending) for ch in channels))
    missing = [ch for ch, ok in zip(channels, results) if not ok]
    if not missing:
        _ok_until[(bid, user_id)] = time.monotonic() + CACHE_TTL
    return missing


async def unchecked_channels(bid: int) -> list[dict]:
    return await fetch("SELECT * FROM bot_channels WHERE bot_id=:b AND unchecked=1 ORDER BY pos, added, chat_id", b=bid)


def merge_channels(*groups) -> list[dict]:
    allc = [c for g in groups for c in g]
    return sorted(allc, key=lambda c: (c["pos"], str(c["added"]), c["chat_id"]))


async def prompt_channels(bid: int, missing: list[dict]) -> list[dict]:
    """Join Mode 'all' = show every channel, 'not_joined' = only channels still to join (+ display-only ones)."""
    if setting(bid, "join_mode") == "not_joined":
        return merge_channels(missing, await unchecked_channels(bid))
    return merge_channels(await fetch("SELECT * FROM bot_channels WHERE bot_id=:b ORDER BY pos, added, chat_id", b=bid))


def join_url(ch: dict) -> str:
    """Request link when the channel is in request mode, else the normal link."""
    if ch.get("request_mode") and ch.get("req_link"):
        return ch["req_link"]
    return ch["link"]


# ═══════════════════════════ button icons / colours ═══════════════════════════
STYLES = ("primary", "success", "danger")  # blue / green / red buttons


def icon(bid: int, key: str) -> str | None:
    return setting(bid, f"icon:{key}") or None


async def set_icon(bid: int, key: str, emoji_id: str):
    await set_setting(bid, f"icon:{key}", emoji_id)


async def clear_icon(bid: int, key: str):
    await reset_setting(bid, f"icon:{key}")


def style_of(bid: int, key: str) -> str | None:
    v = setting(bid, f"style:{key}")
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


def custom_emoji_ids(m: Message) -> list[str]:
    ents = m.entities or m.caption_entities or []
    return [e.custom_emoji_id for e in ents if e.type == "custom_emoji"]


def split_label(m: Message) -> tuple[str, str | None]:
    """(text without premium emoji, id of the first premium emoji)."""
    text = m.text or m.caption or ""
    ents = sorted((e for e in (m.entities or m.caption_entities or []) if e.type == "custom_emoji"),
                  key=lambda e: e.offset)
    if not ents:
        return " ".join(text.split()), None
    raw = text.encode("utf-16-le")  # entity offsets are UTF-16 code units
    out, last = bytearray(), 0
    for e in ents:
        out += raw[last * 2:e.offset * 2]
        last = e.offset + e.length
    out += raw[last * 2:]
    return " ".join(out.decode("utf-16-le").split()), ents[0].custom_emoji_id


def _has_extras(markup) -> bool:
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
    """If Telegram refuses button icons/colours, retry the request without them."""

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            markup = getattr(method, "reply_markup", None)
            if markup is not None and _has_extras(markup):
                log.warning("Button icon/style rejected (%s) – retrying without them", e)
                clean = type(markup).model_validate(_strip(markup.model_dump(exclude_none=True)))
                return await make_request(bot, method.model_copy(update={"reply_markup": clean}))
            raise


def main_kb(bid: int):
    """User menu built from the buttons + layout set in the admin panel."""
    rows = []
    for line in layout_rows(MENU.get(bid, [])):
        btns = []
        for b in line:
            extra = {"style": b["style"]} if b["style"] in STYLES else {}
            btns.append(KeyboardButton(text=b["label"], **extra))
        rows.append(btns)
    if not rows:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Choose an option 👇")
