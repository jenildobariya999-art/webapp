"""
💎 Auto Approve Join Request Bot  (Render + MongoDB, v5 - per-user login)

NEW requests : bot khud approve/reject karta hai + user ko DM (message admin/owner ka set kiya hua)
OLD pending  : user Login (apna API ID/HASH + phone) karke apne channel ki pending requests bulk approve kare
USER side    : My Settings se apna approve/reject DM message, mode, DM on/off
ADMIN side   : Admin Panel -> default messages, welcome, banner, premium emoji, links, broadcast, stats
Messages     : text + premium emoji + formatting + photo/video/gif/file (caption) sab support

ENV (Render -> Environment):
  BOT_TOKEN    BotFather token
  ADMIN_IDS    123456789,987654321
  MONGO_URI    mongodb+srv://user:pass@cluster.mongodb.net/
  MONGO_DB     optional (default approve_bot)
  SESSION_KEY  Fernet key - user sessions encrypted store hote hain (login ke liye zaroori)
               generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
  PORT         Render khud deta hai

NOTE: API_ID / API_HASH owner ke env me NAHI hain. Har user login ke time apna dalta hai.
"""
import asyncio
import html
import json
import logging
import os
import re
import time

from aiohttp import web
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
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
PORT = int(os.environ.get("PORT", "8080"))
MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB", "approve_bot")
BOT_USER = [""]
HTML = ParseMode.HTML
NOPREVIEW = LinkPreviewOptions(is_disabled=True)

FERNET = None
if os.environ.get("SESSION_KEY"):
    from cryptography.fernet import Fernet

    FERNET = Fernet(os.environ["SESSION_KEY"].encode())


def enc(s):
    return FERNET.encrypt(s.encode()).decode()


def dec(s):
    try:
        return FERNET.decrypt(s.encode()).decode()
    except Exception:
        return None


# ------------------------------------------------------------------ emoji pack
# key -> normal (fallback) emoji. Admin Panel -> Premium Emoji se har key ko premium emoji do.
EMO = {
    "bot": "🤖", "gem": "💎", "bolt": "⚡", "ok": "✅", "no": "❌", "lock": "🔐", "unlock": "🔓",
    "gear": "⚙️", "mega": "📢", "group": "➕", "back": "🔙", "star": "⭐", "fire": "🔥",
    "rocket": "🚀", "info": "ℹ️", "chart": "📊", "pen": "✏️", "mail": "📨", "crown": "👑",
    "check": "☑️", "phone": "📱", "key": "🔑", "book": "📖", "list": "📋", "eye": "👁",
    "shield": "🛡", "party": "🎉", "swap": "🔁", "bell": "🔔", "image": "🖼", "link": "🔗",
}

# ------------------------------------------------------------------ storage (MongoDB)
GLOBAL_KEYS = ["g", "welcome_msg", "banner", "emoji", "premium", "btn_fx", "channel_url", "tut_url", "stats"]
DB = {
    "users": set(),
    "sessions": {},  # uid -> {"s": telethon string session, "id": api_id, "hash": api_hash}
    "chats": {},  # chat_id -> {owner, title}
    "cfg": {},  # uid -> {approve_msg, reject_msg, mode, dm}
    "g": {"mode": "approve", "dm": True, "approve_msg": None, "reject_msg": None},
    "welcome_msg": None,
    "banner": None,
    "emoji": {},
    "premium": True,
    "btn_fx": True,
    "channel_url": "",
    "tut_url": "",
    "stats": {"approved": 0, "rejected": 0},
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
    if FERNET:
        async for d in mdb.sessions.find():
            raw = dec(d["d"]) if d.get("d") else None
            if raw:
                try:
                    DB["sessions"][d["_id"]] = json.loads(raw)
                except ValueError:
                    pass
    async for d in mdb.chats.find():
        DB["chats"][d.pop("_id")] = d
    async for d in mdb.cfg.find():
        DB["cfg"][d.pop("_id")] = d
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
    if uid not in DB["users"]:
        DB["users"].add(uid)
        bg(mdb.users.update_one({"_id": uid}, {"$set": {"t": time.time()}}, upsert=True))


def save_cfg(uid):
    bg(mdb.cfg.replace_one({"_id": str(uid)}, dict(DB["cfg"].get(str(uid), {})), upsert=True))


def del_cfg(uid):
    DB["cfg"].pop(str(uid), None)
    bg(mdb.cfg.delete_one({"_id": str(uid)}))


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
    """Premium emoji (agar set + ON) warna normal emoji."""
    fb = EMO.get(key, "•")
    cid = DB["emoji"].get(key)
    if DB["premium"] and cid:
        return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'
    return fb


def strip_custom(s):
    return re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", s, flags=re.S)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s)


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
    """Telegram message -> template (premium emoji/bold/quote/etc. + media)."""
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
    """Message (text ya caption) me jitne premium emoji hain unke IDs, order me."""
    ents = list(msg.entities or []) + list(msg.caption_entities or [])
    ents = [en for en in ents if en.type == "custom_emoji"]
    ents.sort(key=lambda en: en.offset)
    return [en.custom_emoji_id for en in ents]


_dm_sem = asyncio.Semaphore(15)


async def send_tpl(bot, chat_id, t, name=None, chat=None, kb=None, prefix=""):
    """Template bhejo (media/premium emoji ke saath). name=None => placeholders replace nahi hote."""
    t = norm(t)
    body = prefix + (fmt(t["html"], name, chat) if name is not None else t["html"])
    media = t.get("media")
    senders = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation, "document": bot.send_document}
    last = None
    for attempt in (0, 1):
        txt = body if attempt == 0 else strip_custom(body)  # premium emoji fail -> normal emoji
        try:
            if media and len(txt) <= 1000:
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
        try:
            await send_tpl(bot, uid, t, name, chat, kb)
        except Exception as ex:
            log.info("DM fail %s: %s", uid, ex)
        await asyncio.sleep(0.04)


# ------------------------------------------------------------------ default texts
def default_welcome():
    return (
        f"{e('bot')} <b>Hey {{name}}!</b> {e('check')}\n\n"
        f"{e('gem')} <b>Free Auto Approve Join Request Bot</b>\n"
        f"{e('star')} Nayi join requests turant approve\n"
        f"{e('star')} Approve hote hi user ko DM\n"
        f"{e('star')} Old pending requests: Login karke bulk approve\n\n"
        f"{e('shield')} Bot ko channel/group me <b>admin</b> banao (Invite Users permission)\n"
        f"{e('bolt')} Bas itna karo - baaki sab <b>auto</b> {e('check')}\n\n"
        f"{e('ok')} Powered By <b>Piro</b>"
    )


def default_approve():
    return (
        f"{e('party')} <b>Congratulations {{name}}!</b>\n\n"
        f"{e('ok')} Aapki join request <b>{{chat}}</b> me <b>APPROVE</b> ho gayi hai.\n"
        f"{e('fire')} Welcome aboard!"
    )


def default_reject():
    return f"{e('no')} <b>Sorry {{name}}</b>\n\nAapki join request <b>{{chat}}</b> me <b>REJECT</b> ho gayi hai."


def ucfg(uid):
    return DB["cfg"].setdefault(str(uid), {})


def eff(uid, key):
    v = DB["cfg"].get(str(uid), {}).get(key)
    return DB["g"].get(key) if v is None else v


def tpl(uid, kind):
    v = norm(eff(uid, kind + "_msg"))
    if v and (v["html"] or v.get("media")):
        return v
    return {"html": default_approve() if kind == "approve" else default_reject(), "media": None}


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
        banner = DB["banner"] if len(t) <= 950 else None
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
                DB["btn_fx"] = False  # button premium feature available nahi -> auto off
                save()
            elif not stripped:
                stripped = True  # premium emoji text fallback
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
            [btn("My Settings", "gear", "go:settings"), btn("My Channels", "list", "go:channels")],
        ]
        if DB["channel_url"]:
            rows.append([btn("Private Channel", "fire", url=DB["channel_url"])])
        if is_admin(uid):
            rows.append([btn("Admin Panel", "crown", "go:admin", style="danger")])
        return Markup(rows)

    return text, kb


def s_bulk(uid, fname):
    logged = str(uid) in DB["sessions"]
    status = f"{e('ok')} <b>Logged in</b>" if logged else f"{e('no')} <b>Not logged in</b>"
    text = (
        f"{e('bolt')} <b>Bulk Approve - Old Pending Requests</b>\n\n"
        f"<blockquote>{e('lock')} <b>Login</b> - Apna account connect karo (apna API ID/HASH + number).\n"
        f"{e('unlock')} <b>Logout</b> - Account disconnect + session delete.\n"
        f"{e('star')} <b>Approve All</b> - Channel/group ka message forward karo, pending requests approve.\n"
        f"{e('book')} <b>Tutorial</b> - Kaise kaam karta hai.</blockquote>\n\n"
        f"{e('shield')} Status: {status}\n"
        f"<i>Ye sirf aapke login account se chalta hai. Aapka account us channel/group me admin hona chahiye.</i>"
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
        f"{e('info')} <b>Help / Tutorial</b>\n\n"
        f"<blockquote>"
        f"<b>1.</b> Bot ko channel/group me <b>admin</b> banao (Invite Users permission)\n"
        f"<b>2.</b> Ab <b>nayi</b> join requests auto approve + user ko DM\n"
        f"<b>3.</b> <b>Purani</b> requests: Bulk Approve → Login → Approve All → channel ka message forward karo\n"
        f"<b>4.</b> Login ke liye my.telegram.org → <b>API development tools</b> se apna API ID/HASH banao\n"
        f"<b>5.</b> My Settings se apna approve/reject DM message edit karo (premium emoji, photo/video sab chalega)"
        f"</blockquote>\n\n"
        f"{e('pen')} <code>{{name}}</code> = user ka naam, <code>{{chat}}</code> = channel ka naam"
    )
    return text, lambda: Markup([[btn("Back", "back", "go:home")]])


def s_settings(uid, fname):
    mode = eff(uid, "mode")
    dm_on = eff(uid, "dm")
    mode_txt = f"{e('ok')} APPROVE" if mode == "approve" else f"{e('no')} REJECT"
    dm_txt = "ON" if dm_on else "OFF"
    text = (
        f"{e('gear')} <b>My Settings</b>\n\n"
        f"{e('swap')} Mode: <b>{mode_txt}</b>\n"
        f"{e('mail')} DM to user: <b>{dm_txt}</b>\n\n"
        f"{e('ok')} <b>Approve message</b>\n<blockquote>{pv(tpl(uid, 'approve'))}</blockquote>\n"
        f"{e('no')} <b>Reject message</b>\n<blockquote>{pv(tpl(uid, 'reject'))}</blockquote>\n\n"
        f"<i>Naya message bhejo: premium emoji, bold/italic, photo/video/gif + caption sab chalega.</i>"
    )

    def kb():
        return Markup(
            [
                [btn("Edit Approve Msg", "pen", "act:e_ap", style="success"), btn("Edit Reject Msg", "pen", "act:e_rj", style="danger")],
                [btn("Preview ✅", "eye", "act:pv_ap"), btn("Preview ❌", "eye", "act:pv_rj")],
                [btn(f"Mode: {mode.upper()}", "swap", "act:t_mode"), btn(f"DM: {dm_txt}", "bell", "act:t_dm")],
                [btn("Reset to Default", "back", "act:reset"), btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_channels(uid, fname):
    mine = [(cid, c) for cid, c in DB["chats"].items() if c.get("owner") == uid]
    if mine:
        lines = "\n".join(f"{e('mega')} <b>{esc(c.get('title'))}</b> <code>{cid}</code>" for cid, c in mine[:40])
    else:
        lines = "<i>Abhi koi channel/group connected nahi.\nBot ko apne channel me admin banao.</i>"
    text = f"{e('list')} <b>My Channels</b>\n\n<blockquote>{lines}</blockquote>"
    return text, lambda: Markup([add_links(), [btn("Back", "back", "go:home")]])


def s_admin(uid, fname):
    s = DB["stats"]
    text = (
        f"{e('crown')} <b>Admin Panel</b>\n\n"
        f"<blockquote>{e('chart')} Users: <b>{len(DB['users'])}</b>\n"
        f"{e('mega')} Channels: <b>{len(DB['chats'])}</b>\n"
        f"{e('lock')} Logged-in accounts: <b>{len(DB['sessions'])}</b>\n"
        f"{e('ok')} Approved: <b>{s['approved']}</b>   {e('no')} Rejected: <b>{s['rejected']}</b></blockquote>\n\n"
        f"{e('ok')} <b>Default approve</b>\n<blockquote>{pv(DB['g'].get('approve_msg') or default_approve())}</blockquote>\n"
        f"<i>Default messages un users ke liye hain jinhone apna message set nahi kiya.</i>"
    )

    def kb():
        return Markup(
            [
                [btn("Default Approve", "pen", "act:g_ap"), btn("Default Reject", "pen", "act:g_rj")],
                [btn("Welcome Msg", "pen", "act:g_wl"), btn("Banner Image", "image", "act:banner")],
                [btn("Premium Emoji", "star", "go:a_emoji", style="primary"), btn("Links", "link", "go:a_links")],
                [
                    btn(f"Premium Text: {'ON' if DB['premium'] else 'OFF'}", "swap", "act:t_prem"),
                    btn(f"Button FX: {'ON' if DB['btn_fx'] else 'OFF'}", "swap", "act:t_fx"),
                ],
                [btn("Broadcast", "mega", "act:bc", style="success"), btn("Remove Banner", "no", "act:banner_rm")],
                [btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_a_emoji(uid, fname):
    done = sum(1 for k in EMO if DB["emoji"].get(k))
    text = (
        f"{e('star')} <b>Premium Emoji Setup</b>  ({done}/{len(EMO)})\n\n"
        f"<b>Single:</b> neeche kisi emoji key par tap karo, phir ek <b>premium emoji</b> bhejo.\n"
        f"<b>Bulk Fill:</b> ek message me saare premium emoji (key order me) bhejo, sab ek saath set ho jayenge.\n"
        f"{e('check')} = set ho chuka.\n\n"
        f"<i>Tip: koi bhi premium emoji bot ko bhejoge (admin, bina kisi mode ke) to uska ID mil jayega.</i>\n\n"
        f"<blockquote><i>Note: Telegram ki shart ke hisaab se bot ke messages me premium emoji dikhane ke liye bot-owner ke paas "
        f"Fragment username ya Premium hona chahiye, aur button icons bhi sirf kuch bots ko milte hain. "
        f"Kaam na kare to bot khud normal emoji par fallback karta hai.</i></blockquote>"
    )

    def kb():
        rows, row = [], []
        for k in EMO:
            mark = "☑️" if DB["emoji"].get(k) else EMO[k]
            row.append(Btn(f"{mark} {k}", callback_data=f"act:emo:{k}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn("Bulk Fill (in order)", "list", "act:emo_bulk", style="primary")])
        rows.append([btn("Reset All", "no", "act:emo_reset", style="danger"), btn("Back", "back", "go:admin")])
        return Markup(rows)

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
    "home": s_home, "bulk": s_bulk, "help": s_help, "settings": s_settings, "channels": s_channels,
    "admin": s_admin, "a_emoji": s_a_emoji, "a_links": s_a_links,
}
ADMIN_SCREENS = {"admin", "a_emoji", "a_links"}


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
    owner = DB["chats"].get(str(chat.id), {}).get("owner")
    mode = eff(owner, "mode") if owner else DB["g"]["mode"]
    dm_on = eff(owner, "dm") if owner else DB["g"]["dm"]
    kind = "approve" if mode == "approve" else "reject"
    try:
        if kind == "approve":
            await req.approve()
            DB["stats"]["approved"] += 1
        else:
            await req.decline()
            DB["stats"]["rejected"] += 1
        save()
    except Exception as ex:
        log.warning("join request error: %s", ex)
        return
    if dm_on:
        kb = None
        if chat.username:
            kb = Markup([[btn("Open " + (chat.title or "Channel")[:20], "mega", url=f"https://t.me/{chat.username}")]])
        context.application.create_task(
            dm(context.bot, req.user_chat_id, tpl(owner or 0, kind), user.first_name or "User", chat.title or "channel", kb)
        )


async def on_my_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.my_chat_member
    chat = m.chat
    if chat.type == "private":
        return
    new = m.new_chat_member
    if new.status == "administrator":
        DB["chats"][str(chat.id)] = {"owner": m.from_user.id, "title": chat.title}
        save_chat(chat.id)
        warn = ""
        if getattr(new, "can_invite_users", True) is False:
            warn = f"\n{e('bell')} <b>Warning:</b> bot ko <b>Invite Users</b> permission do, warna approve nahi hoga."
        try:
            await context.bot.send_message(
                m.from_user.id,
                f"{e('ok')} <b>{esc(chat.title)}</b> connected!\nAb nayi join requests auto handle hongi.{warn}",
                parse_mode=HTML,
            )
        except Exception:
            pass
    elif new.status in ("left", "kicked"):
        del_chat(chat.id)


# ------------------------------------------------------------------ commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    add_user(u.id)
    context.user_data.clear()
    await goto(context, u, update.effective_chat.id, "home")


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await goto(context, update.effective_user, update.effective_chat.id, "admin")


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await goto(context, update.effective_user, update.effective_chat.id, "bulk")


async def emojiid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("Koi bhi premium emoji (ek ya zyada) is chat me bhejo, main uska ID bata dunga.")


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

    # ---------------- user actions
    if act == "login":
        if not FERNET:
            return await q.answer("Owner ne SESSION_KEY set nahi kiya, login abhi band hai.", show_alert=True)
        await q.answer()
        await _drop_login(uid)
        return await prompt(
            context, chat_id, msg, "api_id",
            f"{e('lock')} <b>Login</b>\n\n"
            f"<blockquote>Login karne par aapke account ka session bot ke database me <b>encrypted</b> save hoga, "
            f"aur sirf aapke channel/group ki pending requests approve karne ke liye use hoga. "
            f"<b>Logout</b> dabate hi session Telegram se revoke + delete ho jata hai.</blockquote>\n\n"
            f"<b>Step 1/4:</b> my.telegram.org → <b>API development tools</b> se banaya hua apna <b>API ID</b> bhejo (sirf number).", "bulk",
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
            return await q.answer("Pehle Login karo!", show_alert=True)
        await q.answer()
        return await prompt(
            context, chat_id, msg, "await_forward",
            f"{e('mail')} <b>Approve All</b>\n\nApne channel/group ka koi bhi <b>message forward</b> karo.\n"
            f"<i>(Hidden-forward kaam nahi karta.)</i>", "bulk",
        )

    if act in ("e_ap", "e_rj"):
        await q.answer()
        kind = "APPROVE" if act == "e_ap" else "REJECT"
        return await prompt(
            context, chat_id, msg, "u_ap" if act == "e_ap" else "u_rj",
            f"{e('pen')} <b>Naya {kind} message bhejo</b>\n\n"
            f"<code>{{name}}</code> = user ka naam\n<code>{{chat}}</code> = channel ka naam\n"
            f"Bold/italic/premium emoji + photo/video/gif (caption ke saath) sab chalega - seedha bana ke bhejo.", "settings",
        )

    if act in ("t_mode", "t_dm"):
        c = ucfg(uid)
        if act == "t_mode":
            c["mode"] = "reject" if eff(uid, "mode") == "approve" else "approve"
        else:
            c["dm"] = not eff(uid, "dm")
        save_cfg(uid)
        await q.answer("Saved ✅")
        return await goto(context, user, chat_id, "settings", msg=msg)

    if act == "reset":
        del_cfg(uid)
        await q.answer("Reset ✅")
        return await goto(context, user, chat_id, "settings", msg=msg)

    if act in ("pv_ap", "pv_rj"):
        kind = "approve" if act == "pv_ap" else "reject"
        try:
            await send_tpl(context.bot, uid, tpl(uid, kind), user.first_name or "User", "Demo Channel", prefix="👁 <b>Preview:</b>\n\n")
            return await q.answer("Preview bhej diya ✅")
        except Exception as ex:
            return await q.answer(f"Preview error: {str(ex)[:150]}", show_alert=True)

    # ---------------- admin actions
    if not is_admin(uid):
        return await q.answer("Admin only", show_alert=True)
    await q.answer()

    if act in ("g_ap", "g_rj", "g_wl"):
        names = {"g_ap": "DEFAULT APPROVE", "g_rj": "DEFAULT REJECT", "g_wl": "WELCOME (/start)"}
        return await prompt(
            context, chat_id, msg, act,
            f"{e('pen')} <b>Naya {names[act]} message bhejo</b>\n\n"
            f"<code>{{name}}</code> / <code>{{chat}}</code> use kar sakte ho.\nPremium emoji seedha paste karo."
            + ("" if act == "g_wl" else "\nPhoto/video/gif + caption bhi chalega."), "admin",
        )
    if act == "banner":
        return await prompt(context, chat_id, msg, "banner", f"{e('image')} <b>Banner photo bhejo</b> (sab screens par dikhegi).", "admin")
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
            f"{e('mega')} <b>Broadcast message bhejo</b> (sab users ko jayega).\nPremium emoji + photo/video bhi chalega.", "admin",
        )
    if act.startswith("emo:"):
        key = act[4:]
        return await prompt(
            context, chat_id, msg, "emoji",
            f"{e('star')} <b>{esc(key)}</b> ke liye ek <b>premium emoji</b> bhejo.\n<i>(Ek hi message me ek premium emoji.)</i>", "a_emoji",
            emoji_key=key,
        )
    if act == "emo_bulk":
        order = ", ".join(f"{i + 1}.{k}" for i, k in enumerate(EMO))
        return await prompt(
            context, chat_id, msg, "emoji_bulk",
            f"{e('list')} <b>Bulk Fill</b>\n\nEk <b>single message</b> me premium emoji is order me bhejo "
            f"(normal text ya space beech me ho sakta hai):\n\n<blockquote>{esc(order)}</blockquote>\n"
            f"<i>Kam emoji bhejoge to utni keys set hongi (shuru se).</i>", "a_emoji",
        )
    if act == "emo_reset":
        DB["emoji"] = {}
        save()
        return await goto(context, user, chat_id, "a_emoji", msg=msg)
    if act in ("url_ch", "url_tut"):
        return await prompt(context, chat_id, msg, act, f"{e('link')} <b>URL bhejo</b> (https://t.me/...)", "a_links")
    if act == "url_clear":
        DB["channel_url"] = DB["tut_url"] = ""
        save()
        return await goto(context, user, chat_id, "a_links", msg=msg)


# ------------------------------------------------------------------ telethon (user login + bulk)
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
    cl = TelegramClient(StringSession(d["s"]), d["id"], d["hash"])
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
    # background me chalta hai taaki baaki users block na hon; ek user ka ek hi job
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
        return await render(ctx, chat_id, f"{e('no')} Session expire. Dobara Login karo.", back)
    await render(ctx, chat_id, f"{e('rocket')} <b>{esc(title)}</b>\n\n⏳ Pending requests process ho rahi hain...")
    try:
        await cl.get_dialogs(limit=300)
        peer = await cl.get_input_entity(tg_chat_id)
    except Exception as ex:
        return await render(
            ctx, chat_id,
            f"{e('no')} Chat nahi mila.\nAapka login account us chat me <b>admin</b> hona chahiye.\n<code>{esc(ex)}</code>", back,
        )
    if str(tg_chat_id) not in DB["chats"]:
        DB["chats"][str(tg_chat_id)] = {"owner": uid, "title": title}
        save_chat(tg_chat_id)
    approve = eff(uid, "mode") == "approve"
    t_tpl = tpl(uid, "approve" if approve else "reject")
    send_dm = eff(uid, "dm")
    done, stuck, last = 0, 0, 0.0

    async def one(u):
        try:
            await cl(HideChatJoinRequestRequest(peer=peer, user_id=u, approved=approve))
            return u
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception as ex:
            log.info("hide fail: %s", ex)
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
            return await render(ctx, chat_id, f"{e('no')} Error: <code>{esc(ex)}</code>\nProcessed: <b>{done}</b>", back)
        if not res.users:
            break
        before = done
        for i in range(0, len(res.users), 20):
            results = await asyncio.gather(*[one(u) for u in res.users[i : i + 20]])
            for u in results:
                if u:
                    done += 1
                    DB["stats"]["approved" if approve else "rejected"] += 1
                    if send_dm:
                        ctx.application.create_task(dm(ctx.bot, u.id, t_tpl, u.first_name or "User", title))
            if time.time() - last > 2.5:
                last = time.time()
                await render(ctx, chat_id, f"{e('bolt')} <b>{esc(title)}</b>\n\n⏳ Processed: <b>{done}</b>")
        save()
        if done == before:
            stuck += 1
            if stuck >= 2:
                break
    await render(
        ctx, chat_id,
        f"{e('party')} <b>Done!</b>\n\n<blockquote>{e('mega')} {esc(title)}\n{e('ok')} Total processed: <b>{done}</b></blockquote>", back,
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
        ctx, chat_id, f"{e('ok')} Broadcast done: <b>{ok}/{len(users)}</b>",
        lambda: Markup([[btn("Back", "back", "go:admin")]]),
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user
    uid = user.id
    chat_id = update.effective_chat.id
    ud = context.user_data
    state = ud.get("state")

    # ---- koi state nahi: admin ne premium emoji bheja to ID dikha do
    if not state:
        if is_admin(uid):
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

    # ---- bulk: forwarded message
    if state == "await_forward":
        chat = None
        fo = getattr(msg, "forward_origin", None)
        if fo is not None and hasattr(fo, "chat"):
            chat = fo.chat
        elif getattr(msg, "forward_from_chat", None):
            chat = msg.forward_from_chat
        if not chat:
            return await msg.reply_text("❌ Channel/group ka message forward karo (hidden forward kaam nahi karta).")
        ud.pop("state", None)
        await cleanup()
        context.application.create_task(bulk_guard(context, user, chat_id, chat.id, chat.title or "channel"))
        return

    # ---- login flow: API ID -> API HASH -> phone -> code -> (2FA)
    if state == "api_id":
        await cleanup()
        if not text.isdigit():
            return await prompt(context, chat_id, None, "api_id", f"{e('no')} API ID sirf number hota hai. Dobara bhejo.", "bulk")
        _login_tmp[uid] = {"api_id": int(text)}
        return await prompt(
            context, chat_id, None, "api_hash",
            f"{e('key')} <b>Step 2/4:</b> ab apna <b>API HASH</b> bhejo (32 characters).", "bulk",
        )

    if state == "api_hash":
        await cleanup()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", text):
            return await prompt(context, chat_id, None, "api_hash", f"{e('no')} API HASH 32 characters ka hota hai. Dobara bhejo.", "bulk")
        t = _login_tmp.get(uid)
        if not t:
            return await finish("bulk")
        t["api_hash"] = text
        return await prompt(
            context, chat_id, None, "phone",
            f"{e('phone')} <b>Step 3/4:</b> apna number bhejo (Without + and Without Space, jaise <code>91xxxxxxxxxx</code>).", "bulk",
        )

    if state == "phone":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "api_hash" not in t:
            return await finish("bulk")
        phone = "+" + re.sub(r"\D", "", text)
        cl = TelegramClient(StringSession(), t["api_id"], t["api_hash"])
        try:
            await cl.connect()
            sent = await cl.send_code_request(phone)
        except Exception as ex:
            try:
                await cl.disconnect()
            except Exception:
                pass
            _login_tmp.pop(uid, None)
            return await prompt(
                context, chat_id, None, "api_id",
                f"{e('no')} <code>{esc(ex)}</code>\n\nAPI ID/HASH ya number galat ho sakta hai. API ID se dobara shuru karo.", "bulk",
            )
        t.update(cl=cl, phone=phone, hash=sent.phone_code_hash)
        return await prompt(
            context, chat_id, None, "code",
            f"{e('key')} <b>Step 4/4: Code bhejo</b>\n\nTelegram par code aaya hai. <b>Spaces ke saath</b> bhejo: <code>1 2 3 4 5</code>\n"
            f"<i>(Bina space ke bhejoge to Telegram code expire kar dega.)</i>", "bulk",
        )

    if state == "code":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "cl" not in t:
            return await finish("bulk")
        try:
            await t["cl"].sign_in(t["phone"], re.sub(r"\D", "", text), phone_code_hash=t["hash"])
        except SessionPasswordNeededError:
            return await prompt(context, chat_id, None, "2fa", f"{e('lock')} <b>2-Step password</b> bhejo.", "bulk")
        except PhoneCodeInvalidError:
            return await prompt(context, chat_id, None, "code", f"{e('no')} Galat code. Dobara bhejo (spaces ke saath).", "bulk")
        except Exception as ex:
            await _drop_login(uid)
            return await prompt(context, chat_id, None, "api_id", f"{e('no')} <code>{esc(ex)}</code>\n\nAPI ID se dobara try karo.", "bulk")
        return await _login_done(context, user, chat_id)

    if state == "2fa":
        await cleanup()
        t = _login_tmp.get(uid)
        if not t or "cl" not in t:
            return await finish("bulk")
        try:
            await t["cl"].sign_in(password=text)
        except Exception as ex:
            return await prompt(context, chat_id, None, "2fa", f"{e('no')} <code>{esc(ex)}</code>\n\nPassword dobara bhejo.", "bulk")
        return await _login_done(context, user, chat_id)

    # ---- user: own messages (text/premium emoji/media)
    if state in ("u_ap", "u_rj"):
        t = capture(msg)
        if not (t["html"] or t["media"]):
            return
        await cleanup()
        ucfg(uid)["approve_msg" if state == "u_ap" else "reject_msg"] = t
        save_cfg(uid)
        return await finish()

    # ---- admin only states
    if not is_admin(uid):
        return

    if state in ("g_ap", "g_rj", "g_wl"):
        t = capture(msg)
        if not (t["html"] or t["media"]):
            return
        await cleanup()
        if state == "g_ap":
            DB["g"]["approve_msg"] = t
        elif state == "g_rj":
            DB["g"]["reject_msg"] = t
        else:
            DB["welcome_msg"] = t["html"]
        save()
        return await finish()

    if state == "banner":
        if not msg.photo:
            return await msg.reply_text("❌ Photo bhejo.")
        DB["banner"] = msg.photo[-1].file_id
        save()
        await cleanup()
        ud["panel"] = None
        return await finish()

    if state == "emoji":
        ids = custom_emoji_ids(msg)
        if not ids:
            return await msg.reply_text("❌ Premium (custom) emoji bhejo, normal emoji nahi.")
        DB["emoji"][ud.get("emoji_key")] = ids[0]
        save()
        await cleanup()
        return await finish()

    if state == "emoji_bulk":
        ids = custom_emoji_ids(msg)
        if not ids:
            return await msg.reply_text("❌ Premium (custom) emoji bhejo, normal emoji nahi.")
        for k, cid in zip(EMO, ids):
            DB["emoji"][k] = cid
        save()
        await cleanup()
        return await finish()

    if state in ("url_ch", "url_tut"):
        await cleanup()
        if not text.startswith("http"):
            return await msg.reply_text("❌ Valid URL bhejo (https://...)")
        DB["channel_url" if state == "url_ch" else "tut_url"] = text
        save()
        return await finish()

    if state == "broadcast":
        t = capture(msg)
        await cleanup()
        ud.pop("state", None)
        await render(context, chat_id, f"{e('mega')} Broadcast ho raha hai...")
        context.application.create_task(broadcast(context, chat_id, t))


async def _login_done(ctx, user, chat_id):
    t = _login_tmp.pop(user.id)
    DB["sessions"][str(user.id)] = {"s": t["cl"].session.save(), "id": t["api_id"], "hash": t["api_hash"]}
    save_session(user.id)
    _clients[user.id] = t["cl"]
    ctx.user_data.pop("state", None)
    await render(
        ctx, chat_id,
        f"{e('ok')} <b>Login successful!</b>\n\nAb <b>Approve All</b> dabao aur apne channel ka message forward karo.",
        lambda: Markup([[btn("Approve All", "star", "act:approve", style="success"), btn("Back", "back", "go:bulk")]]),
    )


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
    await run_web()  # Render port detect kar sake isliye pehle
    await load_db()
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler(["pending", "approve", "login"], pending_cmd))
    app.add_handler(CommandHandler("emojiid", emojiid_cmd))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

    await app.initialize()
    BOT_USER[0] = app.bot.username
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query", "chat_join_request", "my_chat_member"],
        drop_pending_updates=False,  # Render sleep/restart me aayi join requests miss na hon
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
