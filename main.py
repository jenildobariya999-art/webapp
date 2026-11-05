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


# ------------------------------------------------------------------ premium emoji pack
# key -> normal (fallback) emoji. Each key can be mapped to a premium emoji in Admin Panel -> Premium Emoji.
EMO = {
    "bot": "🤖", "gem": "💎", "bolt": "⚡", "ok": "✅", "no": "❌", "lock": "🔐", "unlock": "🔓",
    "gear": "⚙️", "mega": "📢", "group": "➕", "back": "🔙", "star": "⭐", "fire": "🔥",
    "rocket": "🚀", "info": "ℹ️", "chart": "📊", "pen": "✏️", "mail": "📨", "crown": "👑",
    "check": "☑️", "phone": "📱", "key": "🔑", "book": "📖", "list": "📋", "eye": "👁",
    "shield": "🛡", "party": "🎉", "swap": "🔁", "bell": "🔔", "image": "🖼", "link": "🔗",
}

# Default pack: paste premium emoji IDs here, in EMO key order, to pre-fill them at startup.
# Example: "5372981976804366741", "6158677579965407023", ...
# Leave empty to manage everything from the Admin Panel -> Premium Emoji menu instead.
_DEFAULT_IDS = [
]
DEFAULT_EMOJI = dict(zip(list(EMO), _DEFAULT_IDS))

# ------------------------------------------------------------------ storage (MongoDB)
GLOBAL_KEYS = ["g", "welcome_msg", "banner", "emoji", "premium", "btn_fx", "channel_url", "tut_url", "stats"]
DB = {
    "users": set(),
    "sessions": {},  # uid -> {"s": telethon string session (login state for that user's own account)}
    "chats": {},  # chat_id -> {owner, title, auto}
    "g": {"dm": True, "approve_msg": None},
    "welcome_msg": None,
    "banner": None,
    "emoji": {},
    "premium": True,
    "btn_fx": True,
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
    for k, v in DEFAULT_EMOJI.items():
        DB["emoji"].setdefault(k, v)
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


def e(key):
    """Premium emoji (if set and enabled), otherwise the normal emoji."""
    fb = EMO.get(key, "•")
    cid = DB["emoji"].get(key)
    if DB["premium"] and cid:
        return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'
    return fb


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
        cid = DB["emoji"].get(ek)
        if DB["btn_fx"] and cid:
            kw["icon_custom_emoji_id"] = cid
        else:
            label = f"{EMO.get(ek, '')} {text}".strip()
    if DB["btn_fx"] and style:
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
        f"{e('bot')} <b>Hey {{name}}!</b> {e('check')}\n\n"
        f"{e('gem')} <b>#1 Free Auto Approve Bot on Telegram</b>\n"
        f"{e('star')} Accept New Join Requests\n"
        f"{e('star')} Accept Pending Join Requests\n\n"
        f"{e('rocket')} Approves every new join request automatically, 24/7\n"
        f"{e('bolt')} Clears old pending requests in bulk\n\n"
        f"{e('ok')} Powered By <b>Piro</b>"
    )


def default_approve():
    return (
        f"{e('party')} <b>Congratulations {{name}}!</b>\n\n"
        f"{e('ok')} Your join request for <b>{{chat}}</b> has been <b>approved</b>.\n"
        f"{e('fire')} Welcome aboard!"
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
            if DB["btn_fx"] and any(x in s for x in ("button", "icon", "style")):
                DB["btn_fx"] = False  # button icons not available for this bot -> switch off automatically
                save()
            elif not stripped:
                stripped = True  # premium emoji in text failed -> fall back to normal emoji
            continue
    log.error("render failed")


# ------------------------------------------------------------------ screens
def add_links():
    me = BOT_USER[0]
    return [
        btn("Add to Group", "group", url=f"https://t.me/{me}?startgroup=true&admin=invite_users+restrict_members"),
        btn("Add to Channel", "mega", url=f"https://t.me/{me}?startchannel=true&admin=invite_users+post_messages"),
    ]


def s_home(uid, fname):
    text = (DB["welcome_msg"] or default_welcome()).replace("{name}", esc(fname))

    def kb():
        rows = [
            add_links(),
            [btn("Bulk Approve", "bolt", "go:bulk", style="primary"), btn("Help Menu", "info", "go:help")],
            [btn("My Channels", "list", "go:channels")],
        ]
        if DB["channel_url"]:
            rows.append([btn("Private Channel", "fire", url=DB["channel_url"])])
        if is_admin(uid):
            rows.append([btn("Admin Panel", "crown", "go:admin", style="danger")])
        return Markup(rows)

    return text, kb


def s_bulk(uid, fname):
    logged = str(uid) in DB["sessions"]
    status = f"{e('ok')} Logged in" if logged else f"{e('no')} Not logged in"
    text = (
        f"{e('bolt')} <b>Bulk Approval Feature</b>\n\n"
        f"<blockquote>"
        f"{e('lock')} <b>Login</b> - Connect your account.\n"
        f"{e('unlock')} <b>Logout</b> - Disconnect your account.\n"
        f"{e('star')} <b>Approve All</b> - Approve every pending request.\n"
        f"{e('book')} <b>Tutorial</b> - Learn how this works."
        f"</blockquote>\n\n"
        f"<i>Log in once, then forward any message from your channel or group to start the bulk approval instantly.</i>\n\n"
        f"{e('shield')} Status: <b>{status}</b>"
    )

    def kb():
        tut = btn("Tutorial", "book", url=DB["tut_url"]) if DB["tut_url"] else btn("Tutorial", "book", "go:help")
        return Markup(
            [
                [btn("Login", "lock", "act:login", style="primary"), btn("Logout", "unlock", "act:logout", style="danger")],
                [btn("Approve All", "star", "act:approve", style="success")],
                add_links(),
                [tut, btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_help(uid, fname):
    text = (
        f"{e('info')} <b>Here Is My Help Menu</b>\n\n"
        f"{e('book')} <u>Getting Started</u>\n"
        f"<blockquote>"
        f"{e('group')} Add me to your channel or group as an <b>admin</b> with the <b>Invite Users</b> permission.\n"
        f"{e('ok')} That's all - every new join request is approved automatically."
        f"</blockquote>\n\n"
        f"{e('gear')} <u>Features</u>\n"
        f"<blockquote>"
        f"{e('list')} <b>My Channels</b> - switch auto-approve ON or OFF for each channel.\n"
        f"{e('bolt')} <b>Bulk Approve</b> - log in once and approve all old pending requests.\n"
        f"{e('mail')} Approved members receive a welcome message in their DM."
        f"</blockquote>"
    )
    return text, lambda: Markup([[btn("Back", "back", "go:home")]])


def s_channels(uid, fname):
    mine = [(cid, c) for cid, c in DB["chats"].items() if c.get("owner") == uid][:15]
    if mine:
        body = f"<i>Tap a channel below to manage it ({len(mine)}).</i>"
    else:
        body = "<i>No channels connected yet.\nAdd me to your channel or group as an admin and it will appear here.</i>"
    text = f"{e('list')} <b>My Channels</b>\n\n{body}"

    def kb():
        rows = [[btn((c.get("title") or "Channel")[:28], "mega", f"act:ch_open:{cid}")] for cid, c in mine]
        rows.append(add_links())
        rows.append([btn("Back", "back", "go:home")])
        return Markup(rows)

    return text, kb


def s_channel_menu(uid, cid):
    info = DB["chats"].get(cid)
    if not info or not (info.get("owner") == uid or is_admin(uid)):
        return None
    on = info.get("auto", True)
    text = (
        f"{e('mega')} <b>{esc(info.get('title'))}</b>\n\n"
        f"<blockquote>{e('swap')} Auto Approve: <b>{'ON' if on else 'OFF'}</b>\n"
        + (
            f"{e('ok')} New join requests are approved automatically.\n"
            if on
            else f"{e('no')} New join requests stay pending until you turn this ON.\n"
        )
        + f"{e('chart')} Approved via this bot (all time): <b>{info.get('approved', 0)}</b></blockquote>"
    )

    def kb():
        return Markup(
            [
                [btn(f"Auto Approve: {'ON' if on else 'OFF'}", "ok" if on else "no", f"act:ch_t:{cid}", style="success" if on else "danger")],
                [btn("Approve Pending Requests", "bolt", f"act:ch_p:{cid}", style="primary")],
                [btn("Back", "back", "go:channels")],
            ]
        )

    return text, kb


async def channel_menu(ctx, user, chat_id, cid, msg=None):
    r = s_channel_menu(user.id, cid)
    if not r:
        return await goto(ctx, user, chat_id, "channels", msg=msg)
    text, kb = r
    await render(ctx, chat_id, text, kb, msg=msg)


def s_admin(uid, fname):
    s = DB["stats"]
    dm_on = DB["g"]["dm"]
    text = (
        f"{e('crown')} <b>Admin Panel</b>\n\n"
        f"<blockquote>{e('chart')} Users: <b>{len(DB['users'])}</b>\n"
        f"{e('mega')} Channels: <b>{len(DB['chats'])}</b>\n"
        f"{e('lock')} Logged-in accounts: <b>{len(DB['sessions'])}</b>\n"
        f"{e('ok')} Total approved: <b>{s['approved']}</b></blockquote>\n\n"
        f"{e('mail')} Welcome DM after approval: <b>{'ON' if dm_on else 'OFF'}</b>\n\n"
        f"{e('ok')} <b>Approval message</b>\n<blockquote>{pv(DB['g'].get('approve_msg') or default_approve())}</blockquote>"
    )

    def kb():
        return Markup(
            [
                [btn("Approval Message", "pen", "act:g_ap", style="success"), btn("Preview", "eye", "act:pv_ap")],
                [btn("Welcome Message", "pen", "act:g_wl"), btn("Banner Image", "image", "act:banner")],
                [btn("Links", "link", "go:a_links")],
                [
                    btn(f"DM: {'ON' if dm_on else 'OFF'}", "bell", "act:t_dm"),
                    btn(f"Premium Text: {'ON' if DB['premium'] else 'OFF'}", "swap", "act:t_prem"),
                ],
                [btn(f"Button Icons: {'ON' if DB['btn_fx'] else 'OFF'}", "swap", "act:t_fx"), btn("Remove Banner", "no", "act:banner_rm")],
                [btn("Broadcast", "mega", "act:bc", style="success")],
                [btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_a_links(uid, fname):
    text = (
        f"{e('link')} <b>Links</b>\n\n"
        f"<blockquote>{e('fire')} Private Channel: {esc(DB['channel_url']) or '<i>not set</i>'}\n"
        f"{e('book')} Tutorial: {esc(DB['tut_url']) or '<i>not set</i>'}</blockquote>"
    )
    return text, lambda: Markup(
        [
            [btn("Set Channel URL", "fire", "act:url_ch"), btn("Set Tutorial URL", "book", "act:url_tut")],
            [btn("Clear Both", "no", "act:url_clear"), btn("Back", "back", "go:admin")],
        ]
    )


SCREENS = {
    "home": s_home, "bulk": s_bulk, "help": s_help, "channels": s_channels,
    "admin": s_admin, "a_links": s_a_links,
}
ADMIN_SCREENS = {"admin", "a_links"}


async def goto(ctx, user, chat_id, name, msg=None):
    if name in ADMIN_SCREENS and not is_admin(user.id):
        name = "home"
    text, kb = SCREENS[name](user.id, user.first_name or "User")
    await render(ctx, chat_id, text, kb, msg=msg)


async def prompt(ctx, chat_id, msg, state, text, ret, **extra):
    ud = ctx.user_data
    ud["state"] = state
    ud["ret"] = ret
    ud.update(extra)
    await render(ctx, chat_id, text, lambda: Markup([[btn("Cancel", "no", f"go:{ret}", style="danger")]]), msg=msg)


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
            kb = Markup([[btn("Open " + (chat.title or "Channel")[:20], "mega", url=f"https://t.me/{chat.username}")]])
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
            warn = f"\n\n{e('bell')} <b>Warning:</b> please give me the <b>Invite Users</b> permission, otherwise I cannot approve requests."
        try:
            await context.bot.send_message(
                m.from_user.id,
                f"{e('ok')} <b>{esc(chat.title)}</b> is connected!\n\n"
                f"{e('bolt')} New join requests will now be <b>approved automatically</b>.\n"
                f"{e('list')} Switch it ON/OFF or approve pending requests from <b>My Channels</b> in the main menu.{warn}",
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
            f"{e('bell')} <b>New user started the bot</b>\n\n"
            f"<blockquote>{esc(u.first_name or 'User')} ({esc(uname)})\nID: <code>{u.id}</code>\n"
            f"{e('chart')} Total users: <b>{len(DB['users'])}</b></blockquote>"
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
            f"{e('lock')} <b>Secure Login</b>\n\n"
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
            f"{e('mail')} <b>Approve All</b>\n\nForward <b>any message</b> from your channel or group to start the bulk approval.\n"
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
    if act in ("g_ap", "g_wl"):
        names = {"g_ap": "APPROVAL", "g_wl": "WELCOME (main menu)"}
        return await prompt(
            context, chat_id, msg, act,
            f"{e('pen')} <b>Send the new {names[act]} message</b>\n\n"
            f"Use <code>{{name}}</code> for the user's name"
            + ("" if act == "g_wl" else " and <code>{chat}</code> for the channel name")
            + f".\nPremium emoji and formatting are supported."
            + ("" if act == "g_wl" else "\nYou can also send a photo / video / GIF with a caption."), "admin",
        )
    if act == "banner":
        return await prompt(context, chat_id, msg, "banner", f"{e('image')} <b>Send the banner photo</b> (shown on top of every screen).", "admin")
    if act == "banner_rm":
        DB["banner"] = None
        save()
        ud["panel"] = None
        return await goto(context, user, chat_id, "admin")
    if act == "t_prem":
        DB["premium"] = not DB["premium"]
        save()
        return await goto(context, user, chat_id, "admin", msg=msg)
    if act == "t_fx":
        DB["btn_fx"] = not DB["btn_fx"]
        save()
        return await goto(context, user, chat_id, "admin", msg=msg)
    if act == "bc":
        return await prompt(
            context, chat_id, msg, "broadcast",
            f"{e('mega')} <b>Send the broadcast message</b> (goes to all users).\nPremium emoji, photo and video are supported.", "admin",
        )
    if act in ("url_ch", "url_tut"):
        return await prompt(context, chat_id, msg, act, f"{e('link')} <b>Send the URL</b> (https://t.me/...)", "a_links")
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
    back = lambda: Markup([[btn("Back", "back", "go:bulk")]])
    if not cl:
        return await render(ctx, chat_id, f"{e('no')} Your session has expired. Please log in again.", back)
    await render(ctx, chat_id, f"{e('rocket')} <b>{esc(title)}</b>\n\n⏳ Processing pending requests...")
    try:
        await cl.get_dialogs(limit=300)
        peer = await cl.get_input_entity(tg_chat_id)
    except Exception as ex:
        return await render(
            ctx, chat_id,
            f"{e('no')} Chat not found.\nThe account you logged in with must be an <b>admin</b> of this chat.\n<code>{esc(ex)}</code>", back,
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
            return await render(ctx, chat_id, f"{e('no')} Error: <code>{esc(ex)}</code>\nApproved so far: <b>{done}</b>", back)
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
                await render(ctx, chat_id, f"{e('bolt')} <b>{esc(title)}</b>\n\n⏳ Approved so far: <b>{done}</b>")
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
    dm_line = f"\n{e('mail')} DM delivered: <b>{dm_ok}</b>   {e('no')} Failed: <b>{dm_fail}</b>" if send_dm and dm_tasks else ""

    if not found_any:
        text = (
            f"{e('info')} <b>No Pending Requests</b>\n\n"
            f"<blockquote>{e('mega')} {esc(title)}\n"
            f"{e('swap')} Auto Approve: <b>{'ON' if on else 'OFF'}</b>\n"
            f"{e('chart')} Approved via this bot (all time): <b>{ch.get('approved', 0)}</b></blockquote>\n\n"
            f"<i>There are no old join requests waiting right now - new requests are handled automatically as they arrive.</i>"
        )
    else:
        text = (
            f"{e('party')} <b>Done!</b>\n\n"
            f"<blockquote>{e('mega')} {esc(title)}\n"
            f"{e('ok')} Approved just now: <b>{done}</b>\n"
            f"{e('chart')} Approved via this bot (all time): <b>{ch.get('approved', 0)}</b>{dm_line}</blockquote>"
        )
    await render(ctx, chat_id, text, back)

    uname = f"@{user.username}" if user.username else "no username"
    status_line = f"{e('ok')} Approved: <b>{done}</b>" if found_any else f"{e('info')} No pending requests found"
    admin_text = (
        f"{e('bolt')} <b>Approve All used</b>\n\n"
        f"<blockquote>{esc(user.first_name or 'User')} ({esc(uname)}, <code>{uid}</code>)\n"
        f"{e('mega')} {esc(title)}\n"
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
        f"{e('ok')} <b>Login successful!</b>\n\nNow press <b>Approve All</b>, or simply forward any message from your channel or group.",
        lambda: Markup([[btn("Approve All", "star", "act:approve", style="success"), btn("Back", "back", "go:bulk")]]),
    )


# ------------------------------------------------------------------ message router
async def broadcast(ctx, chat_id, t):
    ok = 0
    users = list(DB["users"])
    for u in users:
        try:
            await send_tpl(ctx.bot, u, t)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await render(
        ctx, chat_id, f"{e('ok')} Broadcast finished: <b>{ok}/{len(users)}</b> delivered.",
        lambda: Markup([[btn("Back", "back", "go:admin")]]),
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
                    reply_markup=Markup([[btn("Bulk Approve", "bolt", "go:bulk", style="primary")]]),
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
                f"{e('no')} That doesn't look like a valid number. Please enter it without + and without spaces, like <code>91xxxxxxxxxx</code>.", "bulk",
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
                f"{e('no')} <code>{esc(ex)}</code>\n\nPlease enter your number again, without + and without spaces.", "bulk",
            )
        _login_tmp[uid] = {"cl": cl, "phone": phone, "hash": sent.phone_code_hash}
        return await prompt(
            context, chat_id, None, "code",
            f"{e('key')} Telegram just sent you a login code. Send it here with <b>spaces between the digits</b>, "
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
            return await prompt(context, chat_id, None, "2fa", f"{e('lock')} Send your <b>two-step verification password</b>.", "bulk")
        except PhoneCodeInvalidError:
            return await prompt(context, chat_id, None, "code", f"{e('no')} Wrong code. Please send it again (with spaces).", "bulk")
        except Exception as ex:
            await _drop_login(uid)
            return await prompt(context, chat_id, None, "phone", f"{e('no')} <code>{esc(ex)}</code>\n\nLet's try again - enter your number.", "bulk")
        return await _login_done(context, user, chat_id)

    if state == "2fa":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "cl" not in t:
            return await finish("bulk")
        try:
            await t["cl"].sign_in(password=text)
        except Exception as ex:
            return await prompt(context, chat_id, None, "2fa", f"{e('no')} <code>{esc(ex)}</code>\n\nPlease send the password again.", "bulk")
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
        await render(context, chat_id, f"{e('mega')} Broadcasting...")
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
