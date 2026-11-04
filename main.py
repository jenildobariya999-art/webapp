"""
💎 Auto Approve Join Request Bot  (Render + MongoDB, v5 - per-user login)

NEW requests : bot khud APPROVE karta hai + user ko DM (message admin ka set kiya hua). Reject nahi.
OLD pending  : user Login (apna API ID/HASH + phone) karke apne channel ki pending requests bulk approve kare
USER side    : bot add karo, apne channel ka auto-approve ON/OFF, pending approve (login). Sirf request-related.
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

# har emoji key kahan dikhta hai (Guided Setup me admin ko ye dikhta hai)
EMO_WHERE = {
    "bot": "Welcome message ka top", "gem": "Welcome / Admin title", "bolt": "Bulk Approve button + titles",
    "ok": "Approve message + success", "no": "Cancel buttons + errors + OFF status", "lock": "Login button",
    "unlock": "Logout button", "gear": "Admin settings", "mega": "Channels list + Add to Channel button",
    "group": "Add to Group button", "back": "Har Back button", "star": "Welcome points + Approve All button",
    "fire": "Approve message + Private Channel button", "rocket": "Bulk approve start", "info": "Help button",
    "chart": "Admin stats", "pen": "Edit buttons", "mail": "DM / Broadcast", "crown": "Admin Panel",
    "check": "Welcome ke tick", "phone": "Login - number step", "key": "Login - code/hash step",
    "book": "Tutorial button", "list": "My Channels + Bulk Fill", "eye": "Preview button",
    "shield": "Safety notes", "party": "Approve message (Congratulations)", "swap": "Toggle buttons",
    "bell": "Warnings / DM toggle", "image": "Banner button", "link": "Links button",
}

# Default premium emoji pack: neeche IDs EMO ke key order me (1..28) lagti hain.
# bell / image / link ke liye abhi ID nahi -> normal emoji dikhega (Admin Panel -> Premium Emoji se set kar sakte ho).
_DEFAULT_IDS = [
    "5372981976804366741", "6158677579965407023", "6158890438544597850", "6237704110628413424",
    "4956612582816351459", "5832546462478635761", "6066366333203783372", "6298820412918734860",
    "5440460938136300137", "5352759161945867747", "5954135079662916434", "5420315771991497307",
    "6291981609507755102", "4958506272551863292", "6158813567219932663", "4958725487682650920",
    "5195033767969839232", "5407025283456835913", "5330115548900501467", "6158877450563494862",
    "5197269100878907942", "5442979945045335455", "5197288647275071607", "6242319667168287353",
    "5015299982316536836", "6160983049690422056", "5017243970414052392", "6158822075550145902",
]
DEFAULT_EMOJI = dict(zip(list(EMO), _DEFAULT_IDS))

# ------------------------------------------------------------------ storage (MongoDB)
GLOBAL_KEYS = ["g", "welcome_msg", "banner", "emoji", "premium", "btn_fx", "channel_url", "tut_url", "stats"]
DB = {
    "users": set(),
    "sessions": {},  # uid -> {"s": telethon string session, "id": api_id, "hash": api_hash}
    "chats": {},  # chat_id -> {owner, title}
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
        f"{e('bot')} <b>Hey {{name}}!</b> {e('party')}\n\n"
        f"{e('gem')} <b>Free Auto Approve Bot</b>\n"
        f"Ye bot aapke channel/group ki join requests <b>apne aap approve</b> karta hai.\n\n"
        f"<blockquote>"
        f"{e('group')} <b>Step 1:</b> Bot ko apne channel/group me <b>admin</b> banao (Invite Users permission)\n"
        f"{e('bolt')} <b>Step 2:</b> Bas! Nayi join requests <b>auto approve</b> hongi\n"
        f"{e('list')} <b>Manage:</b> My Channels me apne channel ka auto approve ON/OFF karo\n"
        f"{e('lock')} <b>Purani pending requests:</b> Bulk Approve → Login → Approve All"
        f"</blockquote>\n\n"
        f"{e('ok')} Powered By <b>Piro</b>"
    )


def default_approve():
    return (
        f"{e('party')} <b>Congratulations {{name}}!</b>\n\n"
        f"{e('ok')} Aapki join request <b>{{chat}}</b> me <b>approve</b> ho gayi hai.\n"
        f"{e('fire')} Welcome aboard!"
    )


def tpl(uid=0, kind="approve"):
    # sirf approve message - admin set karta hai (global)
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
    status = f"{e('ok')} <b>Logged in</b>" if logged else f"{e('no')} <b>Not logged in</b>"
    text = (
        f"{e('bolt')} <b>Bulk Approve - Purani Pending Requests</b>\n\n"
        f"Jo requests bot ke admin banne se <b>pehle</b> se pending hain, unhe ek saath approve karne ke liye:\n\n"
        f"<blockquote>"
        f"<b>1.</b> {e('lock')} <b>Login</b> dabao (apna API ID/HASH + number)\n"
        f"<b>2.</b> {e('star')} <b>Approve All</b> dabao\n"
        f"<b>3.</b> Apne channel/group ka koi bhi <b>message forward</b> karo\n"
        f"<b>4.</b> {e('ok')} Saari pending requests approve ho jayengi"
        f"</blockquote>\n\n"
        f"{e('shield')} Status: {status}\n"
        f"<i>Aapka account us channel/group me admin hona chahiye. Logout dabate hi session delete ho jata hai. "
        f"Nayi requests iske bina bhi auto approve hoti hain.</i>"
    )

    def kb():
        tut = btn("Tutorial", "book", url=DB["tut_url"]) if DB["tut_url"] else btn("Help", "book", "go:help")
        return Markup(
            [
                [btn("Login", "lock", "act:login", style="primary"), btn("Logout", "unlock", "act:logout", style="danger")],
                [btn("Approve All", "star", "act:approve", style="success")],
                [tut, btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_help(uid, fname):
    text = (
        f"{e('info')} <b>Help / Tutorial</b>\n\n"
        f"<blockquote>"
        f"{e('group')} <b>Nayi requests (automatic)</b>\n"
        f"Bot ko channel/group me <b>admin</b> banao (Invite Users permission). Bas - har nayi join request apne aap approve hogi.\n\n"
        f"{e('list')} <b>Apna channel manage karo</b>\n"
        f"My Channels me har channel ka auto approve <b>ON/OFF</b> kar sakte ho. OFF karne par nayi requests pending rahengi.\n\n"
        f"{e('bolt')} <b>Purani pending requests</b>\n"
        f"Bulk Approve → Login → Approve All → channel ka message forward karo. "
        f"Ya My Channels me channel ke saamne <b>Pending</b> button dabao (Login ke baad).\n"
        f"Login ke liye my.telegram.org → <b>API development tools</b> se apna API ID/HASH banao."
        f"</blockquote>"
    )
    return text, lambda: Markup([[btn("Back", "back", "go:home")]])


def s_channels(uid, fname):
    mine = [(cid, c) for cid, c in DB["chats"].items() if c.get("owner") == uid][:10]
    if mine:
        lines = "\n".join(
            f"{e('mega')} <b>{esc(c.get('title'))}</b> - {'🟢 ON' if c.get('auto', True) else '🔴 OFF'}" for cid, c in mine
        )
        body = f"<blockquote>{lines}</blockquote>"
    else:
        body = "<i>Abhi koi channel/group connected nahi.\nBot ko apne channel/group me admin banao, wo yahan aa jayega.</i>"
    text = (
        f"{e('list')} <b>My Channels</b>\n\n{body}\n\n"
        f"<b>Buttons ka matlab:</b>\n"
        f"{e('ok')} <b>Channel ka naam</b> - us channel ka auto approve ON/OFF\n"
        f"{e('bolt')} <b>Pending</b> - us channel ki purani pending requests approve (pehle Bulk Approve me Login karo)"
    )

    def kb():
        rows = []
        for cid, c in mine:
            on = c.get("auto", True)
            rows.append(
                [
                    btn(f"{(c.get('title') or 'Channel')[:18]}: {'ON' if on else 'OFF'}", "ok" if on else "no", f"act:ch_t:{cid}"),
                    btn("Pending", "bolt", f"act:ch_p:{cid}"),
                ]
            )
        rows.append(add_links())
        rows.append([btn("Back", "back", "go:home")])
        return Markup(rows)

    return text, kb


def s_admin(uid, fname):
    s = DB["stats"]
    dm_on = DB["g"]["dm"]
    text = (
        f"{e('crown')} <b>Admin Panel</b>\n\n"
        f"<blockquote>{e('chart')} Users: <b>{len(DB['users'])}</b>\n"
        f"{e('mega')} Channels: <b>{len(DB['chats'])}</b>\n"
        f"{e('lock')} Logged-in accounts: <b>{len(DB['sessions'])}</b>\n"
        f"{e('ok')} Total approved: <b>{s['approved']}</b></blockquote>\n\n"
        f"{e('mail')} Approve hone par DM: <b>{'ON' if dm_on else 'OFF'}</b>\n\n"
        f"{e('ok')} <b>Approve DM message</b>\n<blockquote>{pv(DB['g'].get('approve_msg') or default_approve())}</blockquote>\n"
        f"<i>Messages, emoji aur links sirf admin set karta hai. Users apne channel ka sirf auto approve ON/OFF aur pending approve kar sakte hain.</i>"
    )

    def kb():
        return Markup(
            [
                [btn("Approve Msg", "pen", "act:g_ap", style="success"), btn("Preview", "eye", "act:pv_ap")],
                [btn("Welcome Msg", "pen", "act:g_wl"), btn("Banner Image", "image", "act:banner")],
                [btn("Premium Emoji", "star", "go:a_emoji", style="primary"), btn("Links", "link", "go:a_links")],
                [
                    btn(f"DM: {'ON' if dm_on else 'OFF'}", "bell", "act:t_dm"),
                    btn(f"Premium Text: {'ON' if DB['premium'] else 'OFF'}", "swap", "act:t_prem"),
                ],
                [btn(f"Button FX: {'ON' if DB['btn_fx'] else 'OFF'}", "swap", "act:t_fx"), btn("Remove Banner", "no", "act:banner_rm")],
                [btn("Broadcast", "mega", "act:bc", style="success")],
                [btn("Back", "back", "go:home")],
            ]
        )

    return text, kb


def s_a_emoji(uid, fname):
    done = sum(1 for k in EMO if DB["emoji"].get(k))
    text = (
        f"{e('star')} <b>Premium Emoji Setup</b>  ({done}/{len(EMO)} set)\n"
        f"<i>Default pack pehle se set hai. Test dabake dekho; kuch galat jagah dikhe to Guided Setup se badal do.</i>\n\n"
        f"<blockquote>"
        f"<b>1.</b> <b>Guided Setup</b> dabao - bot ek-ek jagah batayega, aap ek premium emoji bhejo "
        f"(ek message me kai bhejoge to agli keys bhi saath me fill hongi)\n"
        f"<b>2.</b> Ya <b>Bulk Fill</b> - ek message me saare premium emoji key order me bhejo\n"
        f"<b>3.</b> <b>Test</b> dabao - emoji dikh jayein to setup done {e('ok')}"
        f"</blockquote>\n\n"
        f"{e('bell')} <b>Zaroori:</b> Telegram ki shart ke hisaab se bot ke messages me premium emoji tabhi dikhte hain jab "
        f"bot-owner ke paas Fragment username ho (ya Telegram ka rule uss waqt jo ho). Test me normal emoji dikhein to wahi wajah hai - "
        f"bot kuch bigaadega nahi, khud normal emoji par fallback karta hai.\n\n"
        f"{e('check')} = set ho chuka. Neeche kisi key par tap karke sirf wahi emoji bhi badal sakte ho.\n"
        f"<i>Tip: bina kisi mode ke premium emoji bot ko bhejoge to uska ID mil jayega.</i>"
    )

    def kb():
        rows = [
            [btn("Guided Setup", "rocket", "act:emo_guided", style="primary")],
            [btn("Bulk Fill", "list", "act:emo_bulk"), btn("Test", "eye", "act:emo_test", style="success")],
        ]
        row = []
        for k in EMO:
            mark = "☑️" if DB["emoji"].get(k) else EMO[k]
            row.append(Btn(f"{mark} {k}", callback_data=f"act:emo:{k}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn("Reset Default", "swap", "act:emo_reset", style="danger"), btn("Back", "back", "go:admin")])
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
    "home": s_home, "bulk": s_bulk, "help": s_help, "channels": s_channels,
    "admin": s_admin, "a_emoji": s_a_emoji, "a_links": s_a_links,
}
ADMIN_SCREENS = {"admin", "a_emoji", "a_links"}


async def goto(ctx, user, chat_id, name, msg=None):
    if name in ADMIN_SCREENS and not is_admin(user.id):
        name = "home"
    text, kb = SCREENS[name](user.id, user.first_name or "User")
    await render(ctx, chat_id, text, kb, msg=msg)


async def ask_emoji(ctx, user, chat_id, i, msg=None):
    keys = list(EMO)
    if i >= len(keys):
        ctx.user_data.pop("state", None)
        return await goto(ctx, user, chat_id, "a_emoji", msg=msg)
    k = keys[i]
    ud = ctx.user_data
    ud["state"] = "emoji_guided"
    ud["ret"] = "a_emoji"
    ud["emo_i"] = i
    cur = "✅ set hai" if DB["emoji"].get(k) else "⬜ abhi set nahi"
    text = (
        f"{e('star')} <b>Premium Emoji Setup  {i + 1}/{len(keys)}</b>\n\n"
        f"<blockquote>Key: <b>{esc(k)}</b>   (normal emoji: {EMO[k]})\n"
        f"Kahan dikhega: <b>{esc(EMO_WHERE.get(k, '-'))}</b>\nStatus: {cur}</blockquote>\n\n"
        f"Ab is jagah ke liye ek <b>premium emoji</b> bhejo.\n"
        f"<i>Ek message me kai premium emoji bhejoge to agli keys bhi isi order me fill ho jayengi.</i>"
    )
    await render(
        ctx, chat_id, text,
        lambda: Markup([[btn("Skip", "swap", "act:emo_skip"), btn("Stop", "no", "go:a_emoji", style="danger")]]),
        msg=msg,
    )


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
        return  # channel owner ne auto approve OFF kiya hai - request pending rahegi
    try:
        await req.approve()
        DB["stats"]["approved"] += 1
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
        DB["chats"][str(chat.id)] = {"owner": m.from_user.id, "title": chat.title, "auto": old.get("auto", True)}
        save_chat(chat.id)
        warn = ""
        if getattr(new, "can_invite_users", True) is False:
            warn = f"\n{e('bell')} <b>Warning:</b> bot ko <b>Invite Users</b> permission do, warna approve nahi hoga."
        try:
            await context.bot.send_message(
                m.from_user.id,
                f"{e('ok')} <b>{esc(chat.title)}</b> connected!\n\n"
                f"{e('bolt')} Ab iski nayi join requests <b>auto approve</b> hongi.\n"
                f"{e('list')} ON/OFF aur pending approve ke liye: /start → <b>My Channels</b>{warn}",
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

    # ---------------- user: apne channel ki request-related settings
    if act.startswith("ch_t:") or act.startswith("ch_p:"):
        cid = act.split(":", 1)[1]
        info = DB["chats"].get(cid)
        if not info or not (info.get("owner") == uid or is_admin(uid)):
            return await q.answer("Ye channel aapka nahi hai.", show_alert=True)
        if act.startswith("ch_t:"):
            info["auto"] = not info.get("auto", True)
            save_chat(cid)
            await q.answer("Auto approve " + ("ON ✅" if info["auto"] else "OFF ⛔"))
            return await goto(context, user, chat_id, "channels", msg=msg)
        if str(uid) not in DB["sessions"]:
            return await q.answer("Pehle Bulk Approve me Login karo.", show_alert=True)
        await q.answer("Pending approve shuru...")
        context.application.create_task(bulk_guard(context, user, chat_id, int(cid), info.get("title") or "channel"))
        return

    # ---------------- admin actions
    if not is_admin(uid):
        return await q.answer("Admin only", show_alert=True)
    await q.answer()

    if act == "pv_ap":
        kind = "approve"
        try:
            await send_tpl(context.bot, uid, tpl(0, kind), user.first_name or "User", "Demo Channel", prefix="👁 <b>Preview:</b>\n\n")
        except Exception as ex:
            await context.bot.send_message(uid, f"❌ Preview error: {esc(str(ex)[:200])}", parse_mode=HTML)
        return
    if act == "t_dm":
        DB["g"]["dm"] = not DB["g"]["dm"]
        save()
        return await goto(context, user, chat_id, "admin", msg=msg)
    if act == "emo_guided":
        return await ask_emoji(context, user, chat_id, 0, msg=msg)
    if act == "emo_skip":
        return await ask_emoji(context, user, chat_id, int(ud.get("emo_i", 0)) + 1)
    if act == "emo_test":
        row = " ".join(e(k) for k in EMO)
        txt = (
            f"💎 <b>Premium Emoji Test</b>\n\n{row}\n\n"
            f"<blockquote>Upar premium (animated) emoji dikh rahe hain to setup perfect ✅\n"
            f"Sirf normal emoji dikhein to Telegram bot ko premium emoji nahi dikhane de raha "
            f"(Fragment username wali shart) - tab bot normal emoji hi use karega.</blockquote>"
        )
        try:
            await context.bot.send_message(uid, txt, parse_mode=HTML)
        except Exception:
            await context.bot.send_message(uid, strip_custom(txt), parse_mode=HTML)
        return
    if act in ("g_ap", "g_wl"):
        names = {"g_ap": "APPROVE DM", "g_wl": "WELCOME (/start)"}
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
        DB["emoji"] = dict(DEFAULT_EMOJI)
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
    approve = True  # pending bulk hamesha approve
    t_tpl = tpl()
    send_dm = DB["g"]["dm"]
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
                    DB["stats"]["approved"] += 1
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

    # ---- admin only states
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

    if state == "emoji_guided":
        ids = custom_emoji_ids(msg)
        if not ids:
            return await msg.reply_text("❌ Premium (custom) emoji bhejo, normal emoji nahi. Ya Skip dabao.")
        keys = list(EMO)
        i = int(ud.get("emo_i", 0))
        for j, cid in enumerate(ids):
            if i + j < len(keys):
                DB["emoji"][keys[i + j]] = cid
        save()
        await cleanup()
        return await ask_emoji(context, user, chat_id, i + len(ids))

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
