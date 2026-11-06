"""
Auto Approve Join Request Bot  (Render + MongoDB)

NEW requests : approved automatically (24/7) + welcome DM to the user
OLD pending  : the channel owner logs in with their own phone number and approves all pending requests in bulk
USERS        : add the bot, switch auto-approve ON/OFF per channel, approve pending requests
ADMIN        : Admin Panel (messages, banner, premium emoji, links, broadcast, stats)

ENV (Render -> Environment):
  BOT_TOKEN    token from @BotFather
  API_ID       your my.telegram.org API ID (used to talk to Telegram's login API only)
  API_HASH     your my.telegram.org API HASH
  ADMIN_IDS    123456789,987654321
  MONGO_URI    mongodb+srv://user:pass@cluster.mongodb.net/
  MONGO_DB     optional (default approve_bot)
  SESSION_KEY  optional - if empty, the encryption key is derived from BOT_TOKEN automatically
  PORT         provided by Render

API_ID / API_HASH just identify this one app to Telegram (like a website's domain) - they are not a
personal login. Every user still logs in with their OWN phone number, OTP and password, and ends up
with their own separate session, so one user's bulk-approve never affects another user's account.
"""
import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import re
import time

from aiohttp import web
from cryptography.fernet import Fernet
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetChatInviteImportersRequest, HideChatJoinRequestRequest
from telethon.tl.types import InputUserEmpty

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("approve-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
PORT = int(os.environ.get("PORT", "8080"))
MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB", "approve_bot")
BOT_USER = [""]
HTML = ParseMode.HTML
NOPREVIEW = LinkPreviewOptions(is_disabled=True)


def _make_fernet():
    raw = (os.environ.get("SESSION_KEY") or "").strip()
    if raw:
        try:
            return Fernet(raw.encode())
        except Exception:
            log.warning("SESSION_KEY is invalid - deriving the key from BOT_TOKEN instead")
    key = base64.urlsafe_b64encode(hashlib.sha256(("tg-session-key:" + BOT_TOKEN).encode()).digest())
    return Fernet(key)


FERNET = _make_fernet()


def enc(s):
    return FERNET.encrypt(s.encode()).decode()


def dec(s):
    try:
        return FERNET.decrypt(s.encode()).decode()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PREMIUM EMOJI - fully managed here in code, in ONE place. No bot menu, no database.
#
# TO CHANGE AN EXISTING EMOJI:
#   1. Send that premium emoji to this bot in a private chat (no setup needed)
#      - it replies with the emoji's numeric ID.
#   2. Paste the ID as the first value on that emoji's line below.
#   3. Redeploy.
#
# TO ADD A BRAND NEW EMOJI (only ONE line to add, nothing else to touch):
#   "EMOJI_NAME": ("premium id or empty string", "fallback emoji"),
#   Then use it as {EMOJI_NAME} in any message text, or "EMOJI_NAME" as a
#   button's icon key in btn(...).
#
# Leave the id as "" to keep using the fallback emoji for that slot - nothing
# breaks, the bot falls back automatically either way.
#
# PREMIUM_ENABLED / BUTTON_ICONS_ENABLED below are master switches: set to
# False to turn all premium emoji (in text / in buttons) off at once.
# ---------------------------------------------------------------------------
PREMIUM_ENABLED = True
BUTTON_ICONS_ENABLED = True

_EMOJI_DEFS = {
    "EMOJI_BOT":      ("5372981976804366741", "🤖"),
    "EMOJI_GEM":       ("5965324783728989746", "💎"),
    "EMOJI_BOLT":      ("6291627373490083552", "⚡"),
    "EMOJI_SUCCESS":    ("5936081824584306964", "☑️"),
    "EMOJI_FAILED":     ("5936025362944233351", "❌"),
    "EMOJI_LOCK":       ("6158928436120263997", "🛡"),
    "EMOJI_UNLOCK":     ("6158836781518169411", "🔒"),
    "EMOJI_SETTINGS":    ("6337048821603763745", "⚙️"),
    "EMOJI_BACK":       ("5352759161945867747", "🔙"),
    "EMOJI_STAR":       ("5954135079662916434", "⭐️"),
    "EMOJI_FIRE":       ("6278070133126598544", "🔥"),
    "EMOJI_ROCKET":     ("6291981609507755102", "🚀"),
    "EMOJI_INFO":       ("6159032232594907278", "ℹ️"),
    "EMOJI_CHART":      ("6158910878293957936", "📊"),
    "EMOJI_EDIT":       ("6161349320206457768", "✏️"),
    "EMOJI_MAIL":       ("6158813567219932663", "✉️"),
    "EMOJI_CROWN":      ("6278244212446073097", "👑"),
    "EMOJI_CHECK":      ("5249053508681883137", "👤"),
    "EMOJI_PHONE":      ("5195033767969839232", "📱"),
    "EMOJI_KEY":        ("5855092747270098407", "🔑"),
    "EMOJI_BOOK":       ("5330115548900501467", "🔖"),
    "EMOJI_LIST":       ("6158877450563494862", "✍️"),
    "EMOJI_EYE":        ("6161336345110257391", "👀"),
    "EMOJI_SHIELD":     ("4958617898751886363", "🛡"),
    "EMOJI_PARTY":      ("5942583262609150318", "🎉"),
    "EMOJI_SWAP":       ("6242319667168287353", "🔁"),
    "EMOJI_BELL":       ("5015299982316536836", "🔔"),
    "EMOJI_GROUP":      ("5841494459904168607", "➕"),
    "EMOJI_CHANNEL":    ("6269303009658802514", "➕"),
    "EMOJI_IMAGE":      ("4956290155326473271", "🖼"),
    "EMOJI_LINK":        ("5375074927252621134", "🔗"),
    "EMOJI_NEW":        ("5895669571058142797", "🆕"),
    "EMOJI_PENDING":    ("5262838597060422237", "🔔"),
    "EMOJI_BY":         ("5190498849440931467", "🧑🏻‍💻"),
}

_IDS = {k: cid for k, (cid, fb) in _EMOJI_DEFS.items()}
_FALLBACK = {k: fb for k, (cid, fb) in _EMOJI_DEFS.items()}


def _tg(key):
    fb = _FALLBACK[key]
    cid = _IDS.get(key)
    if PREMIUM_ENABLED and cid:
        return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'
    return fb


# Builds EMOJI_BOT, EMOJI_GEM, ... as plain module-level constants, one per
# _EMOJI_DEFS entry above - these are what every message's text uses directly,
# e.g. {EMOJI_FAILED}. Adding a key above automatically creates its constant.
for _k in _EMOJI_DEFS:
    globals()[_k] = _tg(_k)
del _k


# ------------------------------------------------------------------ storage (MongoDB)
GLOBAL_KEYS = ["g", "welcome_msg", "banner", "channel_url", "tut_url", "stats"]
DB = {
    "users": set(),
    "sessions": {},  # uid -> {"s": telethon string session (login state for that user's own account)}
    "chats": {},  # chat_id -> {owner, title, auto}
    "g": {"dm": True, "notify": True, "approve_msg": None},
    "welcome_msg": None,
    "banner": None,
    "channel_url": "",
    "tut_url": "",
    "stats": {"approved": 0},
}
mdb = None
_dirty = False
_tasks = set()


async def load_db():
    global mdb
    mdb = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=15000)[MONGO_DB]
    await mdb.command("ping")
    kv = await mdb.kv.find_one({"_id": "main"}) or {}
    for k in GLOBAL_KEYS:
        if k in kv:
            if isinstance(DB[k], dict):
                DB[k].update(kv[k])
            else:
                DB[k] = kv[k]
    DB["users"] = {d["_id"] async for d in mdb.users.find({}, {"_id": 1})}
    async for d in mdb.sessions.find():
        raw = dec(d["d"]) if d.get("d") else None
        if raw:
            try:
                DB["sessions"][d["_id"]] = json.loads(raw)
            except ValueError:
                pass
    async for d in mdb.chats.find():
        DB["chats"][d.pop("_id")] = d
    log.info("Mongo loaded: %d users, %d chats, %d sessions", len(DB["users"]), len(DB["chats"]), len(DB["sessions"]))


async def _safe(coro):
    try:
        await coro
    except Exception:
        log.exception("mongo write failed")


def bg(coro):
    t = asyncio.get_running_loop().create_task(_safe(coro))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def save():
    """Global settings/stats (debounced, 2 sec)."""
    global _dirty
    _dirty = True


async def flush():
    global _dirty
    if not _dirty:
        return
    _dirty = False
    try:
        await mdb.kv.replace_one({"_id": "main"}, {k: DB[k] for k in GLOBAL_KEYS}, upsert=True)
    except Exception:
        _dirty = True
        log.exception("flush failed")


async def flush_loop():
    while True:
        await asyncio.sleep(2)
        await flush()


def add_user(uid):
    if uid in DB["users"]:
        return False
    DB["users"].add(uid)
    bg(mdb.users.update_one({"_id": uid}, {"$set": {"t": time.time()}}, upsert=True))
    return True


def save_session(uid):
    bg(mdb.sessions.replace_one({"_id": str(uid)}, {"d": enc(json.dumps(DB["sessions"][str(uid)]))}, upsert=True))


def del_session(uid):
    DB["sessions"].pop(str(uid), None)
    bg(mdb.sessions.delete_one({"_id": str(uid)}))


def save_chat(cid):
    bg(mdb.chats.replace_one({"_id": str(cid)}, dict(DB["chats"][str(cid)]), upsert=True))


def del_chat(cid):
    DB["chats"].pop(str(cid), None)
    bg(mdb.chats.delete_one({"_id": str(cid)}))


# ------------------------------------------------------------------ helpers
def is_admin(uid):
    return uid in ADMIN_IDS


def esc(s):
    return html.escape(str(s or ""))


def strip_custom(s):
    return re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", s, flags=re.S)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s)


def vis_len(s):
    """Length of the text as the user sees it (HTML tags do not count)."""
    return len(html.unescape(strip_tags(s or "")))


def plain(s, n=450):
    t = html.unescape(strip_tags(s or "")).strip()
    return esc(t[:n] + ("…" if len(t) > n else ""))


def btn(text, ek=None, cb=None, url=None, style=None):
    kw = {}
    label = text
    if ek:
        cid = _IDS.get(ek)
        if BUTTON_ICONS_ENABLED and cid:
            kw["icon_custom_emoji_id"] = cid
        else:
            label = f"{_FALLBACK.get(ek, '')} {text}".strip()
    if BUTTON_ICONS_ENABLED and style:
        kw["style"] = style
    return Btn(label, callback_data=cb, url=url, api_kwargs=kw or None)


def fmt(tpl_html, name, chat):
    return tpl_html.replace("{name}", esc(name)).replace("{chat}", esc(chat))


# ---- message templates: {"html": "...", "media": {"t": "photo|video|animation|document", "id": file_id} | None}
def norm(v):
    if not v:
        return None
    return {"html": v, "media": None} if isinstance(v, str) else v


def capture(msg):
    """Telegram message -> template (premium emoji / formatting / media)."""
    h = msg.text_html or msg.caption_html or ""
    media = None
    if msg.photo:
        media = {"t": "photo", "id": msg.photo[-1].file_id}
    elif msg.video:
        media = {"t": "video", "id": msg.video.file_id}
    elif msg.animation:
        media = {"t": "animation", "id": msg.animation.file_id}
    elif msg.document:
        media = {"t": "document", "id": msg.document.file_id}
    return {"html": h, "media": media}


def custom_emoji_ids(msg):
    """IDs of all premium emoji in a message (text or caption), in order."""
    ents = list(msg.entities or []) + list(msg.caption_entities or [])
    ents = [en for en in ents if en.type == "custom_emoji"]
    ents.sort(key=lambda en: en.offset)
    return [en.custom_emoji_id for en in ents]


def fwd_chat(msg):
    """Channel/group a message was forwarded from (None for hidden or private forwards)."""
    fo = getattr(msg, "forward_origin", None)
    if fo is not None:
        c = getattr(fo, "chat", None) or getattr(fo, "sender_chat", None)
        if c:
            return c
    return getattr(msg, "forward_from_chat", None)


_dm_sem = asyncio.Semaphore(15)


async def send_tpl(bot, chat_id, t, name=None, chat=None, kb=None, prefix=""):
    """Send a template (media + premium emoji). name=None -> placeholders are left untouched."""
    t = norm(t)
    body = prefix + (fmt(t["html"], name, chat) if name is not None else t["html"])
    media = t.get("media")
    senders = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation, "document": bot.send_document}
    last = None
    for attempt in (0, 1):
        txt = body if attempt == 0 else strip_custom(body)  # premium emoji failed -> normal emoji
        try:
            if media and vis_len(txt) <= 1000:
                return await senders[media["t"]](chat_id, media["id"], caption=txt or None, parse_mode=HTML, reply_markup=kb)
            if media:
                await senders[media["t"]](chat_id, media["id"])
            return await bot.send_message(chat_id, txt or "…", parse_mode=HTML, reply_markup=kb, link_preview_options=NOPREVIEW)
        except RetryAfter as ra:
            await asyncio.sleep(ra.retry_after + 1)
            last = ra
        except BadRequest as ex:
            last = ex
    raise last


async def dm(bot, uid, t, name, chat, kb=None):
    async with _dm_sem:
        ok = True
        try:
            await send_tpl(bot, uid, t, name, chat, kb)
        except Exception as ex:
            ok = False
            log.info("DM failed %s: %s", uid, ex)
        await asyncio.sleep(0.04)
        return ok


async def notify_admins(ctx, text, exclude=None):
    if not DB["g"].get("notify", True):
        return
    for admin_id in ADMIN_IDS:
        if admin_id == exclude:
            continue
        try:
            await ctx.bot.send_message(admin_id, text, parse_mode=HTML)
        except Exception:
            pass


# ------------------------------------------------------------------ default texts
def default_welcome():
    return (
        f"{EMOJI_BOT} <b>Hey {{name}}!</b> {EMOJI_CHECK}\n\n"
        f"{EMOJI_GEM} <b>#1 Free Auto Approve Bot on Telegram</b>\n"
        f"{EMOJI_NEW} Accept New Join Requests\n"
        f"{EMOJI_PENDING} Accept Pending Join Requests\n\n"
        f"{EMOJI_ROCKET} Approves every new join request automatically, 24/7\n"
        f"{EMOJI_BOLT} Clears old pending requests in bulk\n\n"
        f"{EMOJI_BY} Powered By <b>Piro</b>"
    )


def default_approve():
    return (
        f"{EMOJI_PARTY} <b>Congratulations {{name}}!</b>\n\n"
        f"{EMOJI_SUCCESS} Your join request for <b>{{chat}}</b> has been <b>approved</b>.\n"
        f"{EMOJI_FIRE} Welcome aboard!"
    )


def tpl():
    """Approval DM template (set by the admin). The bot only approves - there is no reject message."""
    v = norm(DB["g"].get("approve_msg"))
    if v and (v["html"] or v.get("media")):
        return v
    return {"html": default_approve(), "media": None}


def pv(t):
    t = norm(t)
    return plain(t["html"]) + (" 📎" if t.get("media") else "")


# ------------------------------------------------------------------ rendering (single editable panel)
async def _put(ctx, chat_id, panel, text, kb, banner):
    bot = ctx.bot
    if panel:
        if banner and panel["photo"]:
            await bot.edit_message_caption(panel["chat"], panel["id"], caption=text, parse_mode=HTML, reply_markup=kb)
            return panel
        if not banner and not panel["photo"]:
            await bot.edit_message_text(
                text, chat_id=panel["chat"], message_id=panel["id"], parse_mode=HTML,
                reply_markup=kb, link_preview_options=NOPREVIEW,
            )
            return panel
        try:
            await bot.delete_message(panel["chat"], panel["id"])
        except Exception:
            pass
    if banner:
        m = await bot.send_photo(chat_id, banner, caption=text, parse_mode=HTML, reply_markup=kb)
    else:
        m = await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=kb, link_preview_options=NOPREVIEW)
    return {"chat": m.chat_id, "id": m.message_id, "photo": bool(banner)}


async def render(ctx, chat_id, text, kb_fn=None, msg=None):
    ud = ctx.user_data
    if msg is not None:
        ud["panel"] = {"chat": msg.chat_id, "id": msg.message_id, "photo": bool(msg.photo)}
    stripped = False
    for _ in range(4):
        panel = ud.get("panel")
        t = strip_custom(text) if stripped else text
        kb = kb_fn() if kb_fn else None
        banner = DB["banner"] if vis_len(t) <= 1000 else None
        try:
            ud["panel"] = await _put(ctx, chat_id, panel, t, kb, banner)
            return
        except RetryAfter as ra:
            await asyncio.sleep(ra.retry_after + 1)
        except BadRequest as ex:
            s = str(ex).lower()
            if "not modified" in s:
                return
            if any(x in s for x in ("not found", "can't be edited", "message_id_invalid", "to edit")):
                ud["panel"] = None
                continue
            log.warning("render BadRequest: %s", ex)
            global BUTTON_ICONS_ENABLED
            if BUTTON_ICONS_ENABLED and any(x in s for x in ("button", "icon", "style")):
                BUTTON_ICONS_ENABLED = False  # button icons not available for this bot -> switch off automatically (this run)
            elif not stripped:
                stripped = True  # premium emoji in text failed -> fall back to normal emoji
            continue
    log.error("render failed")


# ------------------------------------------------------------------ screens
def add_links():
    me = BOT_USER[0]
    return [
        btn("Add to Group", "EMOJI_GROUP", url=f"https://t.me/{me}?startgroup=true&admin=invite_users+restrict_members"),
        btn("Add to Channel", "EMOJI_CHANNEL", url=f"https://t.me/{me}?startchannel=true&admin=invite_users+post_messages"),
    ]


def s_home(uid, fname):
    text = (DB["welcome_msg"] or default_welcome()).replace("{name}", esc(fname))

    def kb():
        rows = [
            add_links(),
            [btn("Bulk Approve", "EMOJI_BOLT", "go:bulk", style="primary"), btn("Help Menu", "EMOJI_INFO", "go:help")],
            [btn("My Channels", "EMOJI_LIST", "go:channels")],
        ]
        if DB["channel_url"]:
            rows.append([btn("Private Channel", "EMOJI_FIRE", url=DB["channel_url"])])
        if is_admin(uid):
            rows.append([btn("Admin Panel", "EMOJI_CROWN", "go:admin", style="danger")])
        return Markup(rows)

    return text, kb


def s_bulk(uid, fname):
    logged = str(uid) in DB["sessions"]
    status = f"{EMOJI_SUCCESS} Logged in" if logged else f"{EMOJI_FAILED} Not logged in"
    text = (
        f"{EMOJI_BOLT} <b>Bulk Approval Feature</b>\n\n"
        f"<blockquote>"
        f"{EMOJI_LOCK} <b>Login</b> - Connect your account.\n"
        f"{EMOJI_UNLOCK} <b>Logout</b> - Disconnect your account.\n"
        f"{EMOJI_STAR} <b>Approve All</b> - Approve every pending request.\n"
        f"{EMOJI_BOOK} <b>Tutorial</b> - Learn how this works."
        f"</blockquote>\n\n"
        f"<i>Log in once, then forward any message from your channel or group to start the bulk approval instantly.</i>\n\n"
        f"{EMOJI_SHIELD} Status: <b>{status}</b>"
    )

    def kb():
        tut = btn("Tutorial", "EMOJI_BOOK", url=DB["tut_url"]) if DB["tut_url"] else btn("Tutorial", "EMOJI_BOOK", "go:help")
        return Markup(
            [
                [btn("Login", "EMOJI_LOCK", "act:login", style="primary"), btn("Logout", "EMOJI_UNLOCK", "act:logout", style="danger")],
                [btn("Approve All", "EMOJI_STAR", "act:approve", style="success")],
                add_links(),
                [tut, btn("Back", "EMOJI_BACK", "go:home")],
            ]
        )

    return text, kb


def s_help(uid, fname):
    text = (
        f"{EMOJI_INFO} <b>Here Is My Help Menu</b>\n\n"
        f"{EMOJI_BOOK} <u>Getting Started</u>\n"
        f"<blockquote>"
        f"{EMOJI_GROUP} Add me to your channel or group as an <b>admin</b> with the <b>Invite Users</b> permission.\n"
        f"{EMOJI_SUCCESS} That's all - every new join request is approved automatically."
        f"</blockquote>\n\n"
        f"{EMOJI_SETTINGS} <u>Features</u>\n"
        f"<blockquote>"
        f"{EMOJI_LIST} <b>My Channels</b> - switch auto-approve ON or OFF for each channel.\n"
        f"{EMOJI_BOLT} <b>Bulk Approve</b> - log in once and approve all old pending requests.\n"
        f"{EMOJI_MAIL} Approved members receive a welcome message in their DM."
        f"</blockquote>"
    )
    return text, lambda: Markup([[btn("Back", "EMOJI_BACK", "go:home")]])


async def get_pending_count(ctx, cid):
    """Live pending-join-request count for a chat, via Telegram's own Chat field.
    Returns None when it can't be checked (bot no longer admin, no permission, etc.)."""
    try:
        chat = await ctx.bot.get_chat(int(cid))
        n = getattr(chat, "pending_join_request_count", None)
        return int(n) if n is not None else 0
    except Exception:
        return None


async def render_channels(ctx, user, chat_id, msg=None):
    uid = user.id
    mine = [(cid, c) for cid, c in DB["chats"].items() if c.get("owner") == uid][:15]
    counts = {}
    if mine:
        results = await asyncio.gather(*[get_pending_count(ctx, cid) for cid, _ in mine])
        counts = dict(zip([cid for cid, _ in mine], results))

    if mine:
        total_pending = sum(c for c in counts.values() if c)
        body = f"<i>Tap a channel below to manage it.</i>\n{EMOJI_BOLT} Pending across all channels: <b>{total_pending}</b>"
    else:
        body = "<i>No channels connected yet.\nAdd me to your channel or group as an admin and it will appear here.</i>"
    text = f"{EMOJI_LIST} <b>My Channels</b>\n\n{body}"

    def kb():
        rows = []
        for cid, c in mine:
            pc = counts.get(cid)
            label = (c.get("title") or "Channel")[:24]
            suffix = f" ({pc} pending)" if pc else (" (pending: ?)" if pc is None else "")
            rows.append([btn(f"{label}{suffix}", "EMOJI_CHANNEL", f"act:ch_open:{cid}")])
        rows.append(add_links())
        rows.append([btn("Back", "EMOJI_BACK", "go:home")])
        return Markup(rows)

    await render(ctx, chat_id, text, kb, msg=msg)


async def channel_menu(ctx, user, chat_id, cid, msg=None):
    uid = user.id
    info = DB["chats"].get(cid)
    if not info or not (info.get("owner") == uid or is_admin(uid)):
        return await render_channels(ctx, user, chat_id, msg=msg)

    on = info.get("auto", True)
    pending = await get_pending_count(ctx, cid)
    if pending is None:
        pend_line = f"{EMOJI_INFO} Pending requests: <b>unknown</b> (check my admin rights)\n"
    else:
        pend_line = f"{EMOJI_BOLT} Pending requests: <b>{pending}</b>\n"

    text = (
        f"{EMOJI_CHANNEL} <b>{esc(info.get('title'))}</b>\n\n"
        f"<blockquote>{EMOJI_SWAP} Auto Approve: <b>{'ON' if on else 'OFF'}</b>\n"
        + (
            f"{EMOJI_SUCCESS} New join requests are approved automatically.\n"
            if on
            else f"{EMOJI_FAILED} New join requests stay pending until you turn this ON.\n"
        )
        + pend_line
        + f"{EMOJI_CHART} Approved via this bot (all time): <b>{info.get('approved', 0)}</b></blockquote>"
    )
    pending_label = "Approve Pending Requests" + (f" ({pending})" if pending else "")

    def kb():
        return Markup(
            [
                [btn(f"Auto Approve: {'ON' if on else 'OFF'}", "EMOJI_SUCCESS" if on else "EMOJI_FAILED", f"act:ch_t:{cid}", style="success" if on else "danger")],
                [btn(pending_label, "EMOJI_BOLT", f"act:ch_p:{cid}", style="primary")],
                [btn("Back", "EMOJI_BACK", "go:channels")],
            ]
        )

    await render(ctx, chat_id, text, kb, msg=msg)


def s_admin(uid, fname):
    s = DB["stats"]
    dm_on = DB["g"]["dm"]
    notify_on = DB["g"].get("notify", True)
    text = (
        f"{EMOJI_CROWN} <b>Admin Panel</b>\n\n"
        f"<blockquote>{EMOJI_CHART} Users: <b>{len(DB['users'])}</b>\n"
        f"{EMOJI_CHANNEL} Channels: <b>{len(DB['chats'])}</b>\n"
        f"{EMOJI_LOCK} Logged-in accounts: <b>{len(DB['sessions'])}</b>\n"
        f"{EMOJI_SUCCESS} Total approved: <b>{s['approved']}</b></blockquote>\n\n"
        f"{EMOJI_MAIL} Welcome DM after approval: <b>{'ON' if dm_on else 'OFF'}</b>\n"
        f"{EMOJI_BELL} New-user / Approve-All alerts to admins: <b>{'ON' if notify_on else 'OFF'}</b>\n\n"
        f"{EMOJI_SUCCESS} <b>Approval message</b>\n<blockquote>{pv(DB['g'].get('approve_msg') or default_approve())}</blockquote>"
    )

    def kb():
        return Markup(
            [
                [btn("Approval Message", "EMOJI_EDIT", "act:g_ap", style="success"), btn("Preview", "EMOJI_EYE", "act:pv_ap")],
                [btn("Welcome Message", "EMOJI_EDIT", "act:g_wl"), btn("Banner Image", "EMOJI_IMAGE", "act:banner")],
                [btn("Links", "EMOJI_LINK", "go:a_links")],
                [
                    btn(f"DM: {'ON' if dm_on else 'OFF'}", "EMOJI_BELL", "act:t_dm"),
                    btn(f"Admin Alerts: {'ON' if notify_on else 'OFF'}", "EMOJI_BELL", "act:t_notify"),
                ],
                [btn("Remove Banner", "EMOJI_FAILED", "act:banner_rm")],
                [btn("Broadcast", "EMOJI_CHANNEL", "act:bc", style="success")],
                [btn("Back", "EMOJI_BACK", "go:home")],
            ]
        )

    return text, kb


def s_a_links(uid, fname):
    text = (
        f"{EMOJI_LINK} <b>Links</b>\n\n"
        f"<blockquote>{EMOJI_FIRE} Private Channel: {esc(DB['channel_url']) or '<i>not set</i>'}\n"
        f"{EMOJI_BOOK} Tutorial: {esc(DB['tut_url']) or '<i>not set</i>'}</blockquote>"
    )
    return text, lambda: Markup(
        [
            [btn("Set Channel URL", "EMOJI_FIRE", "act:url_ch"), btn("Set Tutorial URL", "EMOJI_BOOK", "act:url_tut")],
            [btn("Clear Both", "EMOJI_FAILED", "act:url_clear"), btn("Back", "EMOJI_BACK", "go:admin")],
        ]
    )


SCREENS = {
    "home": s_home, "bulk": s_bulk, "help": s_help,
    "admin": s_admin, "a_links": s_a_links,
}
ADMIN_SCREENS = {"admin", "a_links"}


async def goto(ctx, user, chat_id, name, msg=None):
    if name in ADMIN_SCREENS and not is_admin(user.id):
        name = "home"
    if name == "channels":
        return await render_channels(ctx, user, chat_id, msg=msg)
    text, kb = SCREENS[name](user.id, user.first_name or "User")
    await render(ctx, chat_id, text, kb, msg=msg)


async def prompt(ctx, chat_id, msg, state, text, ret, **extra):
    ud = ctx.user_data
    ud["state"] = state
    ud["ret"] = ret
    ud.update(extra)
    await render(ctx, chat_id, text, lambda: Markup([[btn("Cancel", "EMOJI_FAILED", f"go:{ret}", style="danger")]]), msg=msg)


# ------------------------------------------------------------------ join requests / membership
async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user, chat = req.from_user, req.chat
    info = DB["chats"].get(str(chat.id), {})
    if info.get("auto", True) is False:
        return  # the channel owner switched auto-approve OFF - the request stays pending
    try:
        await req.approve()
        DB["stats"]["approved"] += 1
        cid = str(chat.id)
        if cid in DB["chats"]:
            DB["chats"][cid]["approved"] = DB["chats"][cid].get("approved", 0) + 1
            save_chat(cid)
        save()
    except Exception as ex:
        log.warning("join request error: %s", ex)
        return
    if DB["g"]["dm"]:
        kb = None
        if chat.username:
            kb = Markup([[btn("Open " + (chat.title or "Channel")[:20], "EMOJI_CHANNEL", url=f"https://t.me/{chat.username}")]])
        context.application.create_task(
            dm(context.bot, req.user_chat_id, tpl(), user.first_name or "User", chat.title or "channel", kb)
        )


async def on_my_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.my_chat_member
    chat = m.chat
    if chat.type == "private":
        return
    new = m.new_chat_member
    if new.status == "administrator":
        old = DB["chats"].get(str(chat.id), {})
        DB["chats"][str(chat.id)] = {
            "owner": m.from_user.id, "title": chat.title,
            "auto": old.get("auto", True), "approved": old.get("approved", 0),
        }
        save_chat(chat.id)
        warn = ""
        if getattr(new, "can_invite_users", True) is False:
            warn = f"\n\n{EMOJI_BELL} <b>Warning:</b> please give me the <b>Invite Users</b> permission, otherwise I cannot approve requests."
        try:
            await context.bot.send_message(
                m.from_user.id,
                f"{EMOJI_SUCCESS} <b>{esc(chat.title)}</b> is connected!\n\n"
                f"{EMOJI_BOLT} New join requests will now be <b>approved automatically</b>.\n"
                f"{EMOJI_LIST} Switch it ON/OFF or approve pending requests from <b>My Channels</b> in the main menu.{warn}",
                parse_mode=HTML,
            )
        except Exception:
            pass
    elif new.status in ("left", "kicked"):
        del_chat(chat.id)


# ------------------------------------------------------------------ commands (only /start and the hidden /admin)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    is_new = add_user(u.id)
    context.user_data.clear()
    await goto(context, u, update.effective_chat.id, "home")
    if is_new:
        uname = f"@{u.username}" if u.username else "no username"
        text = (
            f"{EMOJI_BELL} <b>New user started the bot</b>\n\n"
            f"<blockquote>{esc(u.first_name or 'User')} ({esc(uname)})\nID: <code>{u.id}</code>\n"
            f"{EMOJI_CHART} Total users: <b>{len(DB['users'])}</b></blockquote>"
        )
        await notify_admins(context, text, exclude=u.id)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data.clear()
    await goto(context, update.effective_user, update.effective_chat.id, "admin")


# ------------------------------------------------------------------ callbacks
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    uid = user.id
    data = q.data
    chat_id = q.message.chat_id
    ud = context.user_data
    msg = q.message

    if data.startswith("go:"):
        await q.answer()
        ud.pop("state", None)
        await _drop_login(uid)
        return await goto(context, user, chat_id, data[3:], msg=msg)

    if not data.startswith("act:"):
        return await q.answer()
    act = data[4:]

    # ---------------- user: login / logout / approve
    if act == "login":
        await q.answer()
        await _drop_login(uid)
        return await prompt(
            context, chat_id, msg, "phone",
            f"{EMOJI_LOCK} <b>Secure Login</b>\n\n"
            f"<blockquote>Your session is stored <b>encrypted</b> and used only to approve pending join requests for your own "
            f"channels. Press <b>Logout</b> at any time to revoke and delete it.</blockquote>\n\n"
            f"Please enter your number (<b>without +</b> and <b>without spaces</b>, like <code>91xxxxxxxxxx</code>) "
            f"where <b>91</b> is the country code and <b>xxxxxxxxxx</b> is your number.", "bulk",
        )

    if act == "logout":
        await q.answer()
        cl = None
        try:
            cl = await get_client(uid)
        except Exception:
            pass
        _clients.pop(uid, None)
        del_session(uid)
        if cl:
            try:
                await cl.log_out()
            except Exception:
                pass
        return await goto(context, user, chat_id, "bulk", msg=msg)

    if act == "approve":
        if str(uid) not in DB["sessions"]:
            return await q.answer("Please log in first.", show_alert=True)
        await q.answer()
        return await prompt(
            context, chat_id, msg, "await_forward",
            f"{EMOJI_MAIL} <b>Approve All</b>\n\nForward <b>any message</b> from your channel or group to start the bulk approval.\n"
            f"<i>Hidden forwards do not work.</i>", "bulk",
        )

    # ---------------- user: manage own channels
    if act.startswith("ch_open:") or act.startswith("ch_t:") or act.startswith("ch_p:"):
        cid = act.split(":", 1)[1]
        info = DB["chats"].get(cid)
        if not info or not (info.get("owner") == uid or is_admin(uid)):
            return await q.answer("This channel does not belong to you.", show_alert=True)
        if act.startswith("ch_open:"):
            await q.answer()
            return await channel_menu(context, user, chat_id, cid, msg=msg)
        if act.startswith("ch_t:"):
            info["auto"] = not info.get("auto", True)
            save_chat(cid)
            await q.answer("Auto-approve " + ("ON ✅" if info["auto"] else "OFF ⛔"))
            return await channel_menu(context, user, chat_id, cid, msg=msg)
        if str(uid) not in DB["sessions"]:
            return await q.answer("Please log in first (Bulk Approve → Login).", show_alert=True)
        await q.answer("Approving pending requests...")
        context.application.create_task(bulk_guard(context, user, chat_id, int(cid), info.get("title") or "channel"))
        return

    # ---------------- admin actions
    if not is_admin(uid):
        return await q.answer("Admin only.", show_alert=True)
    await q.answer()

    if act == "pv_ap":
        try:
            await send_tpl(context.bot, uid, tpl(), user.first_name or "User", "Demo Channel", prefix="👁 <b>Preview:</b>\n\n")
        except Exception as ex:
            await context.bot.send_message(uid, f"❌ Preview error: {esc(str(ex)[:200])}", parse_mode=HTML)
        return
    if act == "t_dm":
        DB["g"]["dm"] = not DB["g"]["dm"]
        save()
        return await goto(context, user, chat_id, "admin", msg=msg)
    if act == "t_notify":
        DB["g"]["notify"] = not DB["g"].get("notify", True)
        save()
        return await goto(context, user, chat_id, "admin", msg=msg)
    if act in ("g_ap", "g_wl"):
        names = {"g_ap": "APPROVAL", "g_wl": "WELCOME (main menu)"}
        return await prompt(
            context, chat_id, msg, act,
            f"{EMOJI_EDIT} <b>Send the new {names[act]} message</b>\n\n"
            f"Use <code>{{name}}</code> for the user's name"
            + ("" if act == "g_wl" else " and <code>{chat}</code> for the channel name")
            + f".\nPremium emoji and formatting are supported."
            + ("" if act == "g_wl" else "\nYou can also send a photo / video / GIF with a caption."), "admin",
        )
    if act == "banner":
        return await prompt(context, chat_id, msg, "banner", f"{EMOJI_IMAGE} <b>Send the banner photo</b> (shown on top of every screen).", "admin")
    if act == "banner_rm":
        DB["banner"] = None
        save()
        ud["panel"] = None
        return await goto(context, user, chat_id, "admin")
    if act == "bc":
        return await prompt(
            context, chat_id, msg, "broadcast",
            f"{EMOJI_CHANNEL} <b>Send the broadcast message</b> (goes to all users).\nPremium emoji, photo and video are supported.", "admin",
        )
    if act in ("url_ch", "url_tut"):
        return await prompt(context, chat_id, msg, act, f"{EMOJI_LINK} <b>Send the URL</b> (https://t.me/...)", "a_links")
    if act == "url_clear":
        DB["channel_url"] = DB["tut_url"] = ""
        save()
        return await goto(context, user, chat_id, "a_links", msg=msg)


# ------------------------------------------------------------------ telethon (user login + bulk approve)
_clients: dict = {}
_login_tmp: dict = {}
_bulk_busy: set = set()


async def get_client(uid):
    cl = _clients.get(uid)
    if cl and cl.is_connected():
        return cl
    d = DB["sessions"].get(str(uid))
    if not d:
        return None
    cl = TelegramClient(StringSession(d["s"]), API_ID, API_HASH)
    await cl.connect()
    if not await cl.is_user_authorized():
        await cl.disconnect()
        return None
    _clients[uid] = cl
    return cl


async def _drop_login(uid):
    t = _login_tmp.pop(uid, None)
    if t and t.get("cl"):
        try:
            await t["cl"].disconnect()
        except Exception:
            pass


async def bulk_guard(ctx, user, chat_id, tg_chat_id, title):
    # runs in the background so other users are never blocked; one job per user at a time
    if user.id in _bulk_busy:
        return
    _bulk_busy.add(user.id)
    try:
        await bulk_approve(ctx, user, chat_id, tg_chat_id, title)
    except Exception:
        log.exception("bulk crashed")
    finally:
        _bulk_busy.discard(user.id)


async def bulk_approve(ctx, user, chat_id, tg_chat_id, title):
    uid = user.id
    cl = await get_client(uid)
    back = lambda: Markup([[btn("Back", "EMOJI_BACK", "go:bulk")]])
    if not cl:
        return await render(ctx, chat_id, f"{EMOJI_FAILED} Your session has expired. Please log in again.", back)
    await render(ctx, chat_id, f"{EMOJI_ROCKET} <b>{esc(title)}</b>\n\n⏳ Processing pending requests...")
    try:
        await cl.get_dialogs(limit=300)
        peer = await cl.get_input_entity(tg_chat_id)
    except Exception as ex:
        return await render(
            ctx, chat_id,
            f"{EMOJI_FAILED} Chat not found.\nThe account you logged in with must be an <b>admin</b> of this chat.\n<code>{esc(ex)}</code>", back,
        )
    cid = str(tg_chat_id)
    if cid not in DB["chats"]:
        DB["chats"][cid] = {"owner": uid, "title": title, "auto": True, "approved": 0}
        save_chat(cid)
    t_tpl = tpl()
    send_dm = DB["g"]["dm"]
    done, stuck, last, found_any = 0, 0, 0.0, False
    dm_tasks = []

    async def one(u):
        try:
            await cl(HideChatJoinRequestRequest(peer=peer, user_id=u, approved=True))
            return u
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception as ex:
            log.info("approve failed: %s", ex)
        return None

    while True:
        try:
            res = await cl(
                GetChatInviteImportersRequest(
                    peer=peer, requested=True, offset_date=0, offset_user=InputUserEmpty(), limit=100, link=None, q=""
                )
            )
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
            continue
        except Exception as ex:
            return await render(ctx, chat_id, f"{EMOJI_FAILED} Error: <code>{esc(ex)}</code>\nApproved so far: <b>{done}</b>", back)
        if not res.users:
            break
        found_any = True
        before = done
        for i in range(0, len(res.users), 20):
            results = await asyncio.gather(*[one(u) for u in res.users[i : i + 20]])
            for u in results:
                if u:
                    done += 1
                    DB["stats"]["approved"] += 1
                    if send_dm:
                        dm_tasks.append(asyncio.ensure_future(dm(ctx.bot, u.id, t_tpl, u.first_name or "User", title)))
            if time.time() - last > 2.5:
                last = time.time()
                await render(ctx, chat_id, f"{EMOJI_BOLT} <b>{esc(title)}</b>\n\n⏳ Approved so far: <b>{done}</b>")
        save()
        if done == before:
            stuck += 1
            if stuck >= 2:
                break
    dm_ok = dm_fail = 0
    if dm_tasks:
        results = await asyncio.gather(*dm_tasks, return_exceptions=True)
        dm_ok = sum(1 for r in results if r is True)
        dm_fail = len(results) - dm_ok

    ch = DB["chats"].setdefault(cid, {"owner": uid, "title": title, "auto": True, "approved": 0})
    ch["approved"] = ch.get("approved", 0) + done
    save_chat(cid)
    on = ch.get("auto", True)
    dm_line = f"\n{EMOJI_MAIL} DM delivered: <b>{dm_ok}</b>   {EMOJI_FAILED} Failed: <b>{dm_fail}</b>" if send_dm and dm_tasks else ""

    if not found_any:
        text = (
            f"{EMOJI_INFO} <b>No Pending Requests</b>\n\n"
            f"<blockquote>{EMOJI_CHANNEL} {esc(title)}\n"
            f"{EMOJI_SWAP} Auto Approve: <b>{'ON' if on else 'OFF'}</b>\n"
            f"{EMOJI_CHART} Approved via this bot (all time): <b>{ch.get('approved', 0)}</b></blockquote>\n\n"
            f"<i>There are no old join requests waiting right now - new requests are handled automatically as they arrive.</i>"
        )
    else:
        text = (
            f"{EMOJI_PARTY} <b>Done!</b>\n\n"
            f"<blockquote>{EMOJI_CHANNEL} {esc(title)}\n"
            f"{EMOJI_SUCCESS} Approved just now: <b>{done}</b>\n"
            f"{EMOJI_CHART} Approved via this bot (all time): <b>{ch.get('approved', 0)}</b>{dm_line}</blockquote>"
        )
    await render(ctx, chat_id, text, back)

    uname = f"@{user.username}" if user.username else "no username"
    status_line = f"{EMOJI_SUCCESS} Approved: <b>{done}</b>" if found_any else f"{EMOJI_INFO} No pending requests found"
    admin_text = (
        f"{EMOJI_BOLT} <b>Approve All used</b>\n\n"
        f"<blockquote>{esc(user.first_name or 'User')} ({esc(uname)}, <code>{uid}</code>)\n"
        f"{EMOJI_CHANNEL} {esc(title)}\n"
        f"{status_line}{dm_line}</blockquote>"
    )
    await notify_admins(ctx, admin_text, exclude=uid)


async def _login_done(ctx, user, chat_id):
    t = _login_tmp.pop(user.id)
    DB["sessions"][str(user.id)] = {"s": t["cl"].session.save()}
    save_session(user.id)
    _clients[user.id] = t["cl"]
    ctx.user_data.pop("state", None)
    await render(
        ctx, chat_id,
        f"{EMOJI_SUCCESS} <b>Login successful!</b>\n\nNow press <b>Approve All</b>, or simply forward any message from your channel or group.",
        lambda: Markup([[btn("Approve All", "EMOJI_STAR", "act:approve", style="success"), btn("Back", "EMOJI_BACK", "go:bulk")]]),
    )


def fmt_duration(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


_SPIN = ["⏳", "🔄", "⌛", "🔄"]


# ------------------------------------------------------------------ message router
async def broadcast(ctx, chat_id, t):
    users = list(DB["users"])
    total = len(users)
    start = time.time()
    ok = fail = 0
    last = 0.0
    back = lambda: Markup([[btn("Back", "EMOJI_BACK", "go:admin")]])
    for i, u in enumerate(users):
        try:
            await send_tpl(ctx.bot, u, t)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
        if time.time() - last > 2.0:
            last = time.time()
            done = ok + fail
            spin = _SPIN[i % len(_SPIN)]
            pct = int(done * 100 / total) if total else 100
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await render(
                ctx, chat_id,
                f"{spin} <b>Broadcasting...</b>  {pct}%\n<code>{bar}</code>\n\n"
                f"<blockquote>{EMOJI_CHART} Sent: <b>{done}/{total}</b>\n"
                f"{EMOJI_SUCCESS} Success: <b>{ok}</b>   {EMOJI_FAILED} Failed: <b>{fail}</b>\n"
                f"{EMOJI_BOLT} Time elapsed: <b>{fmt_duration(time.time() - start)}</b></blockquote>",
            )
    await render(
        ctx, chat_id,
        f"{EMOJI_PARTY} <b>Broadcast Complete!</b>\n\n"
        f"<blockquote>{EMOJI_CHART} Total users: <b>{total}</b>\n"
        f"{EMOJI_SUCCESS} Success: <b>{ok}</b>\n"
        f"{EMOJI_FAILED} Failed: <b>{fail}</b>\n"
        f"{EMOJI_BOLT} Time taken: <b>{fmt_duration(time.time() - start)}</b></blockquote>",
        back,
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user
    uid = user.id
    chat_id = update.effective_chat.id
    ud = context.user_data
    state = ud.get("state")

    # ---- no active step
    if not state:
        src = fwd_chat(msg)
        if src:  # a forwarded channel/group message = start bulk approve
            if str(uid) in DB["sessions"]:
                context.application.create_task(bulk_guard(context, user, chat_id, src.id, src.title or "channel"))
            else:
                await msg.reply_text(
                    "🔐 Please log in first to approve pending requests.",
                    reply_markup=Markup([[btn("Bulk Approve", "EMOJI_BOLT", "go:bulk", style="primary")]]),
                )
            return
        if is_admin(uid):  # admin sent premium emoji -> show their IDs
            ids = custom_emoji_ids(msg)
            if ids:
                lines = "\n".join(f"{i + 1}. <code>{cid}</code>" for i, cid in enumerate(ids[:50]))
                await msg.reply_text(f"💎 <b>Premium emoji IDs:</b>\n{lines}", parse_mode=HTML)
        return

    ret = ud.get("ret", "home")
    text = (msg.text or msg.caption or "").strip()

    async def cleanup():
        try:
            await msg.delete()
        except Exception:
            pass

    async def finish(screen=None):
        ud.pop("state", None)
        await goto(context, user, chat_id, screen or ret)

    # ---- bulk: forwarded message after pressing Approve All
    if state == "await_forward":
        chat = fwd_chat(msg)
        if not chat:
            return await msg.reply_text("❌ Please forward a message from your channel or group (hidden forwards do not work).")
        ud.pop("state", None)
        await cleanup()
        context.application.create_task(bulk_guard(context, user, chat_id, chat.id, chat.title or "channel"))
        return

    # ---- login flow: phone -> code -> (2FA)
    if state == "phone":
        await cleanup()
        digits = re.sub(r"\D", "", text)
        if len(digits) < 8:
            return await prompt(
                context, chat_id, None, "phone",
                f"{EMOJI_FAILED} That doesn't look like a valid number. Please enter it without + and without spaces, like <code>91xxxxxxxxxx</code>.", "bulk",
            )
        phone = "+" + digits
        cl = TelegramClient(StringSession(), API_ID, API_HASH)
        try:
            await cl.connect()
            sent = await cl.send_code_request(phone)
        except Exception as ex:
            try:
                await cl.disconnect()
            except Exception:
                pass
            return await prompt(
                context, chat_id, None, "phone",
                f"{EMOJI_FAILED} <code>{esc(ex)}</code>\n\nPlease enter your number again, without + and without spaces.", "bulk",
            )
        _login_tmp[uid] = {"cl": cl, "phone": phone, "hash": sent.phone_code_hash}
        return await prompt(
            context, chat_id, None, "code",
            f"{EMOJI_KEY} Telegram just sent you a login code. Send it here with <b>spaces between the digits</b>, "
            f"like <code>1 2 3 4 5</code>.\n<i>Without spaces Telegram may expire the code.</i>", "bulk",
        )

    if state == "code":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "cl" not in t:
            return await finish("bulk")
        try:
            await t["cl"].sign_in(t["phone"], re.sub(r"\D", "", text), phone_code_hash=t["hash"])
        except SessionPasswordNeededError:
            return await prompt(context, chat_id, None, "2fa", f"{EMOJI_LOCK} Send your <b>two-step verification password</b>.", "bulk")
        except PhoneCodeInvalidError:
            return await prompt(context, chat_id, None, "code", f"{EMOJI_FAILED} Wrong code. Please send it again (with spaces).", "bulk")
        except Exception as ex:
            await _drop_login(uid)
            return await prompt(context, chat_id, None, "phone", f"{EMOJI_FAILED} <code>{esc(ex)}</code>\n\nLet's try again - enter your number.", "bulk")
        return await _login_done(context, user, chat_id)

    if state == "2fa":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "cl" not in t:
            return await finish("bulk")
        try:
            await t["cl"].sign_in(password=text)
        except Exception as ex:
            return await prompt(context, chat_id, None, "2fa", f"{EMOJI_FAILED} <code>{esc(ex)}</code>\n\nPlease send the password again.", "bulk")
        return await _login_done(context, user, chat_id)

    # ---- admin-only states
    if not is_admin(uid):
        return

    if state in ("g_ap", "g_wl"):
        t = capture(msg)
        if not (t["html"] or t["media"]):
            return
        await cleanup()
        if state == "g_ap":
            DB["g"]["approve_msg"] = t
        else:
            DB["welcome_msg"] = t["html"]
        save()
        return await finish()

    if state == "banner":
        if not msg.photo:
            return await msg.reply_text("❌ Please send a photo.")
        DB["banner"] = msg.photo[-1].file_id
        save()
        await cleanup()
        ud["panel"] = None
        return await finish()

    if state in ("url_ch", "url_tut"):
        await cleanup()
        if not text.startswith("http"):
            return await msg.reply_text("❌ Please send a valid URL (https://...).")
        DB["channel_url" if state == "url_ch" else "tut_url"] = text
        save()
        return await finish()

    if state == "broadcast":
        t = capture(msg)
        await cleanup()
        ud.pop("state", None)
        await render(context, chat_id, f"{EMOJI_CHANNEL} Broadcasting...")
        context.application.create_task(broadcast(context, chat_id, t))


# ------------------------------------------------------------------ web server + main
async def run_web():
    async def health(_):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


async def main():
    await run_web()  # open the port first so Render detects the service
    await load_db()
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

    await app.initialize()
    BOT_USER[0] = app.bot.username
    try:
        await app.bot.delete_my_commands()  # everything works through buttons - no command list shown
    except Exception:
        pass
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query", "chat_join_request", "my_chat_member"],
        drop_pending_updates=False,  # requests that arrived while Render was restarting are not lost
    )
    log.info("Bot started as @%s", BOT_USER[0])
    flusher = asyncio.create_task(flush_loop())
    try:
        await asyncio.Event().wait()
    finally:
        flusher.cancel()
        await flush()


if __name__ == "__main__":
    asyncio.run(main())
