"""bot.py – every Telegram-side feature, for ALL bots: gatekeeper (force-join), user handlers,
menu replies and the /admin panel. Each bot has its own data (everything is scoped by bot id)."""
import asyncio
import html
import logging
import re
import time

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import BaseFilter, Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatJoinRequest, ChatMemberUpdated,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import core

log = logging.getLogger("bot")
_bg: set = set()  # keeps background tasks alive


# ═══════════════════════════ gatekeeper: ban, bot ON/OFF, force-join ═══════════════════════════
async def send_join_prompt(bot: Bot, user_id: int, channels: list[dict]):
    """Join buttons: 2 per line, label/colour/icon editable. Optional photo + extra message."""
    bid = bot.id
    join_tpl = core.setting(bid, "join_btn")
    join_btns = []
    for i, ch in enumerate(channels, 1):
        if ch["kind"] == "social":
            label, iid = ch["title"] or "Link", ch["icon"]
        else:
            label = join_tpl.replace("{title}", ch["title"] or "Channel").replace("{n}", str(i))
            iid = ch["icon"] or core.icon(bid, "join_btn")
        join_btns.append(core.btn(label[:60], url=core.join_url(ch), icon_id=iid, style=core.style_of(bid, "join_btn")))
    rows = [join_btns[i:i + 2] for i in range(0, len(join_btns), 2)]
    rows.append([core.btn(core.setting(bid, "check_btn")[:60], cb="check",
                          icon_id=core.icon(bid, "check_btn"), style=core.style_of(bid, "check_btn"))])
    sent = await core.send_rich(bot, user_id, "force_text", core.setting(bid, "force_text"),
                                InlineKeyboardMarkup(inline_keyboard=rows))
    extra = core.setting(bid, "force_extra")
    if extra.strip():
        e = await core.send_rich(bot, user_id, "force_extra", extra)
        # remember the pair so pressing Claim on THIS prompt deletes exactly these two messages
        await core.execute(
            "INSERT INTO bot_prompts(bot_id, user_id, prompt_id, extra_id) VALUES(:b, :u, :p, :e) "
            "ON CONFLICT(bot_id, user_id, prompt_id) DO UPDATE SET extra_id=excluded.extra_id",
            b=bid, u=user_id, p=sent.message_id, e=e.message_id)


class GateMiddleware(BaseMiddleware):
    """Registers users, drops banned users, handles bot ON/OFF and force-join (per bot)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)
        bot: Bot = data["bot"]
        bid = bot.id

        if isinstance(event, Message) and event.chat.type != "private" and user.id not in core.ADMINS:
            return  # ignore groups for non-admins

        is_new = await core.upsert_user(bid, user)
        if is_new and user.id not in core.ADMINS:
            t = asyncio.create_task(core.notify_new_user(bot, user))
            _bg.add(t)
            t.add_done_callback(_bg.discard)

        if user.id in core.ADMINS:
            return await handler(event, data)

        row = await core.fetchone("SELECT banned, verified FROM bot_users WHERE bot_id=:b AND id=:i", b=bid, i=user.id)
        if row and row["banned"]:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned.", show_alert=True)
            return

        if core.setting(bid, "bot_enabled") != "1":  # bot OFF -> maintenance message
            if isinstance(event, CallbackQuery):
                await event.answer("🛠 Bot is under maintenance. Try again later.", show_alert=True)
            else:
                await event.answer(core.setting(bid, "off_text"))
            return

        if isinstance(event, CallbackQuery) and event.data == "check":
            return await handler(event, data)
        if core.setting(bid, "force_join") != "1":
            return await handler(event, data)

        missing = await core.missing_channels(bot, user.id)        # verified channels
        unchecked = await core.unchecked_channels(bid)             # display-only channels
        # unchecked channels are shown once (until the user presses Claim)
        if not missing and not (unchecked and not (row and row["verified"])):
            return await handler(event, data)

        await send_join_prompt(bot, user.id, await core.prompt_channels(bid, missing))
        if isinstance(event, CallbackQuery):
            await event.answer()





admin_router = Router(name="admin")
ANY = StateFilter("*")


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and u.id in core.ADMINS)


admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())


class AddChannel(StatesGroup):
    chat = State()


class EditLink(StatesGroup):
    link = State()


class AddUnchecked(StatesGroup):
    link = State()
    title = State()


class AddSocial(StatesGroup):
    link = State()
    title = State()


class ChBroadcast(StatesGroup):
    msg = State()
    confirm = State()


class Broadcast(StatesGroup):
    msg = State()
    buttons = State()
    confirm = State()


class MenuAdd(StatesGroup):
    name = State()
    reply = State()


class MenuEdit(StatesGroup):
    name = State()
    reply = State()


class EditText(StatesGroup):
    value = State()


class UserLookup(StatesGroup):
    query = State()


class AddAdmin(StatesGroup):
    uid = State()


# ───────────────────────── helpers ─────────────────────────
async def show(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    try:
        await cb.answer()
    except TelegramAPIError:
        pass


def back(to: str = "a:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data=to)]])


def menu_kb(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Stats", callback_data="a:stats")
    b.button(text="📢 Channels", callback_data="a:ch")
    b.button(text="📨 Broadcast", callback_data="a:bc")
    b.button(text="👥 Users", callback_data="a:users")
    b.button(text="⚙️ Settings", callback_data="a:set")
    if user_id in core.OWNERS:
        b.button(text="👮 Admins", callback_data="a:adm")
    b.adjust(2)
    return b.as_markup()


ADMIN_HOME = "🛠 <b>Admin panel</b>\n\nWhat would you like to manage?"


@admin_router.message(ANY, Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(ADMIN_HOME, reply_markup=menu_kb(m.from_user.id))


@admin_router.message(ANY, Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Cancelled.")


@admin_router.callback_query(F.data == "a:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, ADMIN_HOME, menu_kb(cb.from_user.id))


# ───────────────────────── stats ─────────────────────────
@admin_router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    bid = cb.bot.id
    total = await core.scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:bid_", bid_=bid)
    blocked = await core.scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:bid_ AND blocked=1", bid_=bid)
    banned = await core.scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:bid_ AND banned=1", bid_=bid)
    verified = await core.scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:bid_ AND verified=1", bid_=bid)
    chans = await core.scalar("SELECT COUNT(*) FROM bot_channels WHERE bot_id=:bid_", bid_=bid)
    reqs = await core.scalar("SELECT COUNT(*) FROM bot_requests WHERE bot_id=:bid_", bid_=bid)
    await show(
        cb,
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"✅ Reachable: <b>{total - blocked - banned}</b>\n"
        f"🚫 Blocked the bot: <b>{blocked}</b>\n"
        f"⛔ Banned: <b>{banned}</b>\n"
        f"🎯 Passed force-join (Claim): <b>{verified}</b>\n\n"
        f"📢 Force-join channels: <b>{chans}</b>\n"
        f"📨 Stored join requests: <b>{reqs}</b>\n\n"
        f"👮 Admins: <b>{len(core.ADMINS)}</b>",
        back(),
    )


# ───────────────────────── channels ─────────────────────────
_USERNAME_RE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?].*)?$")


async def build_links(bot: Bot, chat):
    """Auto-generate BOTH links for a channel: normal + join-request. Returns (normal, request, warning)."""
    normal = req = warn = None
    try:
        req = (await bot.create_chat_invite_link(
            chat.id, name="forcejoin-request", creates_join_request=True)).invite_link
    except TelegramAPIError as e:
        warn = f"request link: {e}"
    if chat.username:
        normal = f"https://t.me/{chat.username}"
    else:
        try:
            normal = (await bot.create_chat_invite_link(chat.id, name="forcejoin")).invite_link
        except TelegramAPIError as e:
            warn = (warn + "; " if warn else "") + f"normal link: {e}"
    return normal, req, warn


def normalize_link(t: str) -> str | None:
    t = t.strip()
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", t):
        return "https://t.me/" + t[1:]
    if t.startswith(("http://", "https://", "tg://")):
        return t
    if t.startswith(("t.me/", "telegram.me/")):
        return "https://" + t
    return None


async def ordered_channels(bid) -> list[dict]:
    return await core.fetch("SELECT * FROM bot_channels WHERE bot_id=:bid_ ORDER BY pos, added, chat_id", bid_=bid)


async def next_pos(bid) -> int:
    return int(await core.scalar("SELECT COALESCE(MAX(pos),0) FROM bot_channels WHERE bot_id=:bid_", bid_=bid) or 0) + 1


def ch_label(ch: dict) -> str:
    m = _USERNAME_RE.match(ch["link"] or "")
    if m and ch["kind"] == "channel" and m.group(1).lower() != "joinchat":
        name = "@" + m.group(1)
    else:
        name = ch["title"] or str(ch["chat_id"])
    mark = "🔗 " if ch["kind"] == "social" else ("☑️ " if ch["unchecked"] else ("📨 " if ch["request_mode"] else ""))
    return (mark + name)[:16]


def pb(emoji: str, text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=f"{emoji} {text}".strip(), callback_data=cb)


async def move_channel(bid, cid: int, delta: int):
    ids = [r["chat_id"] for r in await ordered_channels(bid)]
    if cid in ids:
        i = ids.index(cid)
        j = i + delta
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for pos, c in enumerate(ids, 1):
            await core.execute("UPDATE bot_channels SET pos=:p WHERE bot_id=:bid_ AND chat_id=:c", p=pos, c=c, bid_=bid)


async def render_channels(cb: CallbackQuery):
    bid = cb.bot.id
    rows = await ordered_channels(bid)
    kb = []
    for ch in rows:
        cid = ch["chat_id"]
        kb.append([
            InlineKeyboardButton(text=ch_label(ch), callback_data=f"a:chlink:{cid}"),
            pb("❌", "", f"a:chdel:{cid}"),
            pb("⬆️", "", f"a:chup:{cid}"),
            pb("⬇️", "", f"a:chdn:{cid}"),
            pb("👀", "", f"a:chview:{cid}"),
        ])
    only_pending = core.setting(bid, "join_mode") == "not_joined"
    kb += [
        [pb("➕", "Add Channel", "a:chadd")],
        [pb("➕", "Add Social Link", "a:chaddsoc")],
        [pb("➕", "Add Uncheck Channel", "a:chaddu")],
        [pb("📡", "Channel Broadcast", "a:chbc")],
        [pb("🎛", f"Join Mode: {'Not Joined Only' if only_pending else 'Show All Channels'}", "a:joinmode")],
        [pb("⬅️", "Back", "a:home")],
    ]
    await show(
        cb,
        "📢 <b>Force-join channels</b>\n\n"
        "Tap a <b>name</b> to change its link • ❌ delete • ⬆️⬇️ reorder • 👀 details\n"
        "📨 request mode  •  ☑️ unchecked  •  🔗 social link\n\n"
        f"🎛 <b>Join mode:</b> {'the join message shows only the channels the user has <u>not joined</u> yet' if only_pending else 'the join message shows <u>all</u> channels'}\n"
        f"Total: <b>{len(rows)}</b>",
        InlineKeyboardMarkup(inline_keyboard=kb),
    )


@admin_router.callback_query(F.data == "a:ch")
async def cb_channels(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_channels(cb)


@admin_router.callback_query(F.data.startswith("a:chdel:"))
async def cb_chdel(cb: CallbackQuery):
    bid = cb.bot.id
    cid = int(cb.data.split(":")[2])
    await core.execute("DELETE FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    await core.execute("DELETE FROM bot_requests WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    core.invalidate(bid, )
    await render_channels(cb)


@admin_router.callback_query(F.data.regexp(r"^a:ch(up|dn):-?\d+$"))
async def cb_chmove(cb: CallbackQuery):
    bid = cb.bot.id
    _, kind, cid = cb.data.split(":")
    await move_channel(bid, int(cid), -1 if kind == "chup" else 1)
    await render_channels(cb)


@admin_router.callback_query(F.data == "a:joinmode")
async def cb_joinmode(cb: CallbackQuery):
    bid = cb.bot.id
    await core.set_setting(bid, "join_mode", "all" if core.setting(bid, "join_mode") == "not_joined" else "not_joined")
    core.invalidate(bid, )
    await render_channels(cb)


# ---- tap on a channel name: straight away ask for the new link ----
def two_links(ch: dict) -> bool:
    """Real, verified channels carry two auto-generated links (normal + request)."""
    return ch["kind"] == "channel" and ch["chat_id"] < 0 and not ch["unchecked"]


@admin_router.callback_query(F.data.regexp(r"^a:chlink:-?\d+$"))
async def cb_chlink(cb: CallbackQuery, state: FSMContext):
    bid = cb.bot.id
    cid = int(cb.data.split(":")[2])
    ch = await core.fetchone("SELECT * FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    if not ch:
        return await render_channels(cb)
    # the link that is currently in use is the one that gets replaced
    which = "req" if (two_links(ch) and ch["request_mode"]) else "link"
    await state.set_state(EditLink.link)
    await state.update_data(cid=cid, which=which)
    cur = (ch["req_link"] if which == "req" else ch["link"]) or "—"
    kind = "📨 request link" if which == "req" else ("📢 normal link" if two_links(ch) else "link")
    rows = []
    if two_links(ch):
        rows.append([InlineKeyboardButton(text="♻️ Auto-generate new links", callback_data=f"a:chregen:{cid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:ch")])
    await show(
        cb,
        f"✏️ <b>{html.escape(ch['title'] or str(cid))}</b>\n\n"
        f"Current {kind} (in use):\n{html.escape(cur)}\n\n"
        "👉 Send the <b>new link</b> now (<code>https://t.me/…</code> or <code>@username</code>).\n"
        "It replaces this link and is used for users immediately.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_router.message(EditLink.link, F.text)
async def msg_editlink(m: Message, state: FSMContext):
    bid = m.bot.id
    d = await state.get_data()
    link = normalize_link(m.text)
    if not link:
        return await m.answer("❌ Send a valid link, e.g. <code>https://t.me/+AbCd…</code> or <code>@username</code>")
    is_req = d["which"] == "req"
    col = "req_link" if is_req else "link"  # fixed whitelist, never user input
    await core.execute(
        f"UPDATE bot_channels SET {col}=:l, request_mode=:m WHERE bot_id=:bid_ AND chat_id=:c",
        l=link, m=1 if is_req else 0, c=d["cid"], bid_=bid,
    )
    core.invalidate(bid, )
    await state.clear()
    await m.answer(
        f"✅ <b>Link updated</b> — users get the new link right away.",
        reply_markup=back("a:ch"),
    )


@admin_router.callback_query(F.data.regexp(r"^a:chregen:-?\d+$"))
async def cb_chregen(cb: CallbackQuery, state: FSMContext, bot: Bot):
    bid = bot.id
    cid = int(cb.data.split(":")[2])
    ch = await core.fetchone("SELECT * FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    try:
        chat = await bot.get_chat(cid)
    except TelegramAPIError as e:
        return await cb.answer(f"Failed: {e}"[:190], show_alert=True)
    normal, req, warn = await build_links(bot, chat)
    if not normal and not req:
        return await cb.answer(f"❌ {warn}"[:190], show_alert=True)
    normal, req = normal or ch["link"], req or ch["req_link"]
    await core.execute("UPDATE bot_channels SET link=:l, req_link=:r WHERE bot_id=:bid_ AND chat_id=:c", l=normal, r=req, c=cid, bid_=bid)
    core.invalidate(bid, )
    await state.clear()
    await show(
        cb,
        "♻️ <b>New links generated</b>\n\n"
        f"📢 Normal:\n{html.escape(normal or '—')}\n\n📨 Request:\n{html.escape(req or '—')}\n\n"
        f"In use: <b>{'request' if ch['request_mode'] else 'normal'}</b> link.",
        back("a:ch"),
    )


# ---- 👀 channel details ----
async def render_channel_view(cb: CallbackQuery, bot: Bot, cid: int):
    bid = bot.id
    ch = await core.fetchone("SELECT * FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    if not ch:
        return await render_channels(cb)
    if ch["kind"] == "social":
        typ = "🔗 Social link"
    elif ch["unchecked"]:
        typ = "☑️ Unchecked (display only)"
    elif ch["request_mode"]:
        typ = "📨 Request mode (users get the request link)"
    else:
        typ = "📢 Normal mode (users get the normal link)"
    text = (
        f"👀 <b>{html.escape(ch['title'] or '-')}</b>\n\n"
        f"🆔 <code>{cid}</code>\n📌 Type: {typ}\n🔗 {html.escape(core.join_url(ch))}"
    )
    if cid < 0:
        try:
            text += f"\n👥 Members: <b>{await bot.get_chat_member_count(cid)}</b>"
        except TelegramAPIError:
            text += "\n👥 Members: <i>n/a</i>"
        pend = await core.scalar("SELECT COUNT(*) FROM bot_requests WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
        if pend:
            text += f"\n📨 Pending join requests: <b>{pend}</b>"
    rows = [[InlineKeyboardButton(text="✏️ Change link", callback_data=f"a:chlink:{cid}")]]
    if two_links(ch) or (ch["kind"] == "channel" and cid < 0):
        line = []
        if not ch["unchecked"]:
            line.append(InlineKeyboardButton(
                text="📢 Use normal link" if ch["request_mode"] else "📨 Use request link",
                callback_data=f"a:chmode:{cid}"))
        line.append(InlineKeyboardButton(
            text="☑️ Uncheck" if not ch["unchecked"] else "✅ Check", callback_data=f"a:chchk:{cid}"))
        rows.append(line)
    rows.append([InlineKeyboardButton(text="🗑 Delete", callback_data=f"a:chdel:{cid}")])
    rows.append([pb("⬅️", "Back", "a:ch")])
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@admin_router.callback_query(F.data.regexp(r"^a:chview:-?\d+$"))
async def cb_chview(cb: CallbackQuery, bot: Bot):
    await render_channel_view(cb, bot, int(cb.data.split(":")[2]))


@admin_router.callback_query(F.data.startswith("a:chmode:"))
async def cb_chmode(cb: CallbackQuery, bot: Bot):
    """Switch which of the two links users receive (both already exist)."""
    bid = bot.id
    cid = int(cb.data.split(":")[2])
    ch = await core.fetchone("SELECT * FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    new = 0 if ch["request_mode"] else 1
    if new and not ch["req_link"]:
        try:
            _, req, warn = await build_links(bot, await bot.get_chat(cid))
        except TelegramAPIError as e:
            return await cb.answer(f"Failed: {e}"[:190], show_alert=True)
        if not req:
            return await cb.answer(f"❌ Couldn't create a request link. {warn}"[:190], show_alert=True)
        await core.execute("UPDATE bot_channels SET req_link=:r WHERE bot_id=:bid_ AND chat_id=:c", r=req, c=cid, bid_=bid)
    await core.execute("UPDATE bot_channels SET request_mode=:m WHERE bot_id=:bid_ AND chat_id=:c", m=new, c=cid, bid_=bid)
    core.invalidate(bid, )
    await render_channel_view(cb, bot, cid)


@admin_router.callback_query(F.data.startswith("a:chchk:"))
async def cb_chchk(cb: CallbackQuery, bot: Bot):
    """Toggle between 'checked' (verified) and 'unchecked' (display only)."""
    bid = bot.id
    cid = int(cb.data.split(":")[2])
    ch = await core.fetchone("SELECT * FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    if ch["unchecked"]:
        try:
            me = await bot.get_chat_member(cid, bot.id)
            if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                raise ValueError
        except (TelegramAPIError, ValueError):
            return await cb.answer("❌ To check a channel the bot must be an admin there.", show_alert=True)
        await core.execute("UPDATE bot_channels SET unchecked=0 WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
    else:
        await core.execute("UPDATE bot_channels SET unchecked=1 WHERE bot_id=:bid_ AND chat_id=:c", c=cid, bid_=bid)
        await core.execute("UPDATE bot_users SET verified=0 WHERE bot_id=:bid_", bid_=bid)  # show it to everyone once
    core.invalidate(bid, )
    await render_channel_view(cb, bot, cid)


# ---- 📡 channel broadcast (post into your channels) ----
@admin_router.callback_query(F.data == "a:chbc")
async def cb_chbc(cb: CallbackQuery, state: FSMContext):
    bid = cb.bot.id
    rows = [c for c in await ordered_channels(bid) if c["chat_id"] < 0]
    if not rows:
        return await cb.answer("Add at least one real channel first.", show_alert=True)
    await state.set_state(ChBroadcast.msg)
    await show(
        cb,
        "📡 <b>Channel Broadcast</b>\n\n"
        f"Send the post you want to publish in your <b>{len(rows)}</b> channel(s) "
        "(text, photo, video… formatting & premium emoji are kept).\n\n"
        "⚠️ The bot needs permission to <b>post messages</b> in those channels.",
        back("a:ch"),
    )


@admin_router.message(ChBroadcast.msg)
async def msg_chbc(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    try:
        await bot.copy_message(m.chat.id, m.chat.id, m.message_id)
    except TelegramAPIError as e:
        await state.clear()
        return await m.answer(f"❌ Preview failed: {html.escape(str(e))}")
    n = len([c for c in await ordered_channels(bid) if c["chat_id"] < 0])
    b = InlineKeyboardBuilder()
    b.button(text=f"🚀 Post in {n} channel(s)", callback_data="a:chbcgo")
    b.button(text="❌ Cancel", callback_data="a:ch")
    b.adjust(1)
    await state.set_state(ChBroadcast.confirm)
    await m.answer("👆 <b>Preview</b> — publish it?", reply_markup=b.as_markup())


@admin_router.callback_query(ChBroadcast.confirm, F.data == "a:chbcgo")
async def cb_chbcgo(cb: CallbackQuery, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    await state.clear()
    ok, failed = [], []
    for ch in [c for c in await ordered_channels(bid) if c["chat_id"] < 0]:
        try:
            await bot.copy_message(ch["chat_id"], d["src_chat"], d["src_msg"])
            ok.append(ch["title"])
        except TelegramAPIError as e:
            failed.append(f"{ch['title']} ({str(e)[:40]})")
        await asyncio.sleep(0.3)
    text = f"📡 <b>Channel broadcast done</b>\n\n✅ Posted: <b>{len(ok)}</b>"
    if failed:
        text += "\n\n⚠️ Failed:\n" + "\n".join("• " + html.escape(x) for x in failed)
    await show(cb, text, back("a:ch"))


# ---- ➕ add a channel: only username / ID, both links are generated automatically ----
@admin_router.callback_query(F.data == "a:chadd")
async def cb_chadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.chat)
    await show(
        cb,
        "➕ <b>Add a channel</b>\n\nSend the channel <b>@username</b> or numeric <b>ID</b> "
        "(e.g. <code>-1001234567890</code>) — or forward any post from it.\n\n"
        "⚠️ The bot must be an <b>admin</b> there (with “Invite users via link”).\n"
        "✨ The bot creates the <b>normal link</b> and the <b>request link</b> automatically.",
        back("a:ch"),
    )


@admin_router.message(AddChannel.chat)
async def msg_chadd(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    ref = None
    fo = m.forward_origin
    if fo is not None and getattr(fo, "type", None) == "channel":
        ref = fo.chat.id
    elif m.text:
        t = m.text.strip()
        if t.lstrip("-").isdigit():
            ref = int(t)
        else:
            ref = "@" + t.split("t.me/")[-1].lstrip("@")
    if ref is None:
        return await m.answer("Send @username, numeric ID, or forward a channel post.")
    try:
        chat = await bot.get_chat(ref)
        me = await bot.get_chat_member(chat.id, bot.id)
    except TelegramAPIError as e:
        return await m.answer(
            f"❌ {html.escape(str(e))}\n\nMake sure the ID/username is right and the bot is an admin. "
            "For private channels forward a post or send the numeric ID."
        )
    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return await m.answer("❌ The bot is not an admin in that chat. Promote it and send again.")

    normal, req, warn = await build_links(bot, chat)
    if not normal and not req:
        return await m.answer(
            f"❌ Couldn't create invite links: {html.escape(warn or '')}\n"
            "Give the bot the “Invite users via link” permission and try again."
        )
    title = chat.title or str(chat.id)
    await core.execute(
        "INSERT INTO bot_channels(bot_id, chat_id, title, link, req_link, request_mode, unchecked, kind, pos) "
        "VALUES(:bid_, :c, :t, :l, :r, 0, 0, 'channel', :p) "
        "ON CONFLICT(bot_id, chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
        "req_link=excluded.req_link, unchecked=0, kind='channel'",
        c=chat.id, t=title, l=normal or req, r=req, p=await next_pos(bid), bid_=bid,
    )
    core.invalidate(bid, )
    await state.clear()
    text = (
        f"✅ Added <b>{html.escape(title)}</b>\n\n"
        f"📢 Normal link:\n{html.escape(normal or '—')}\n\n"
        f"📨 Request link:\n{html.escape(req or '—')}\n\n"
        "Users get the normal link by default. Open 👀 on the channel to switch to the request link."
    )
    if warn:
        text += f"\n\n⚠️ {html.escape(warn)}"
    await m.answer(text, reply_markup=back("a:ch"))


# ---- ☑️ unchecked channel / 🔗 social link (display-only buttons) ----
async def save_display_only(bid, chat_id: int, title: str, link: str, kind: str = "channel", icon: str | None = None):
    await core.execute(
        "INSERT INTO bot_channels(bot_id, chat_id, title, link, request_mode, unchecked, kind, pos, icon) "
        "VALUES(:bid_, :c, :t, :l, 0, 1, :k, :p, :i) "
        "ON CONFLICT(bot_id, chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
        "request_mode=0, unchecked=1, kind=excluded.kind, icon=excluded.icon",
        c=chat_id, t=title, l=link, k=kind, p=await next_pos(bid), i=icon, bid_=bid,
    )
    await core.execute("UPDATE bot_users SET verified=0 WHERE bot_id=:bid_", bid_=bid)  # every user sees the new button once
    core.invalidate(bid, )


def synthetic_id() -> int:
    # positive id: real channel ids are always negative, so no clash
    return int(time.time() * 1000)


@admin_router.callback_query(F.data == "a:chaddu")
async def cb_chaddu(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddUnchecked.link)
    await show(
        cb,
        "☑️ <b>Add an uncheck channel</b>\n\n"
        "Users see a <b>Join</b> button for it, but the bot <u>won't verify</u> that they joined. "
        "The bot doesn't need to be admin there.\n\n"
        "Send the channel <b>link</b> (public link or private invite link), its <b>@username</b>, "
        "or forward a post from it.",
        back("a:ch"),
    )


@admin_router.message(AddUnchecked.link)
async def msg_chaddu(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    chat_id, title, link = d.get("chat_id"), d.get("title"), None
    t = (m.text or "").strip()
    fo = m.forward_origin

    if fo is not None and getattr(fo, "type", None) == "channel":
        chat_id, title = fo.chat.id, fo.chat.title
        try:
            c = await bot.get_chat(chat_id)
            title = c.title or title
            link = f"https://t.me/{c.username}" if c.username else None
        except TelegramAPIError:
            pass
        if not link:
            try:
                link = (await bot.create_chat_invite_link(chat_id, name="forcejoin")).invite_link
            except TelegramAPIError:
                pass
    elif t:
        username = t.lstrip("@") if re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", t) else None
        if not username:
            mm = _USERNAME_RE.match(t)
            username = mm.group(1) if mm else None
        if username and username.lower() != "joinchat":
            link = f"https://t.me/{username}"
            try:
                c = await bot.get_chat("@" + username)
                chat_id, title = c.id, (c.title or title)
            except TelegramAPIError:
                pass
        elif t.startswith(("http://", "https://")):
            link = t

    if not link:
        if chat_id and title:
            await state.update_data(chat_id=chat_id, title=title)
            return await m.answer(
                f"I found <b>{html.escape(title)}</b> but couldn't create a link.\n"
                "Please send its <b>invite link</b> (https://t.me/+…)."
            )
        return await m.answer("❌ Send a valid link, an @username, or forward a post from the channel.")

    if title:
        await save_display_only(bid, chat_id or synthetic_id(), title, link)
        await state.clear()
        return await m.answer(f"✅ Added <b>{html.escape(title)}</b> as ☑️ unchecked.", reply_markup=back("a:ch"))

    await state.update_data(link=link, chat_id=chat_id)
    await state.set_state(AddUnchecked.title)
    await m.answer("✍️ Now send the <b>name</b> of this channel (it replaces <code>{title}</code> in the Join button label).")


@admin_router.message(AddUnchecked.title, F.text)
async def msg_chaddu_title(m: Message, state: FSMContext):
    bid = m.bot.id
    d = await state.get_data()
    title = m.text.strip()[:60]
    await save_display_only(bid, d.get("chat_id") or synthetic_id(), title, d["link"])
    await state.clear()
    await m.answer(f"✅ Added <b>{html.escape(title)}</b> as ☑️ unchecked.", reply_markup=back("a:ch"))


@admin_router.callback_query(F.data == "a:chaddsoc")
async def cb_chaddsoc(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddSocial.link)
    await show(
        cb,
        "🔗 <b>Add a social link</b>\n\nA plain link button (Instagram, YouTube, website…). "
        "Never verified.\n\nSend the full link, e.g. <code>https://instagram.com/yourpage</code>",
        back("a:ch"),
    )


@admin_router.message(AddSocial.link, F.text)
async def msg_chaddsoc(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.startswith(("http://", "https://", "tg://")):
        return await m.answer("❌ Send a full link starting with <code>https://</code>")
    await state.update_data(link=t)
    await state.set_state(AddSocial.title)
    await m.answer(
        "✍️ Now send the <b>button text</b>, e.g. <code>Instagram</code>.\n"
        "💎 Add a <b>premium emoji</b> in your message and it becomes the button's icon."
    )


@admin_router.message(AddSocial.title, F.text)
async def msg_chaddsoc_title(m: Message, state: FSMContext):
    bid = m.bot.id
    d = await state.get_data()
    label, iid = core.split_label(m)
    if not label:
        return await m.answer("❌ Please include some text (not only an emoji).")
    await save_display_only(bid, synthetic_id(), label[:60], d["link"], kind="social", icon=iid)
    await state.clear()
    await m.answer(f"✅ Social link <b>{html.escape(label)}</b> added.", reply_markup=back("a:ch"))


# ───────────────────────── settings ─────────────────────────
TOGGLES = {
    "bot_enabled": "🤖 Bot",
    "force_join": "🔒 Force join",
    "auto_approve": "📨 Auto-approve requests",
    "notify_new_user": "🔔 New-user alerts",
}
TEXTS = {
    "welcome": "👋 Welcome message",
    "force_text": "🔒 Force-join message",
    "force_extra": "➕ Extra message (after force-join)",
    "off_text": "🛠 Maintenance message",
    "join_btn": "🔘 Join button",
    "check_btn": "🔘 Claim button",
}
BTN_KEYS = {"join_btn", "check_btn"}  # short button labels (plain text, optional premium-emoji icon)
LABELS = TEXTS
MEDIA_KEYS = {"welcome", "force_text", "force_extra"}  # these can carry a photo
HINTS = {
    "welcome": "{name} {id}",
    "off_text": "{name}",
    "join_btn": "{title} {n}",
}


def rows_kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_settings(cb: CallbackQuery):
    bid = cb.bot.id
    b = InlineKeyboardBuilder()
    for key, label in TOGGLES.items():
        on = core.setting(bid, key) == "1"
        b.button(text=f"{label}: {'ON ✅' if on else 'OFF ❌'}", callback_data=f"a:tog:{key}")
    b.button(text="✏️ Texts", callback_data="a:txts")
    b.button(text="🧭 Menu buttons", callback_data="a:menu")
    b.button(text="🎨 Button colors", callback_data="a:styles")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1, 1, 1, 1, 2, 1, 1)
    await show(
        cb,
        "⚙️ <b>Settings</b>\n\nTap a switch to turn it ON/OFF.\n"
        "🤖 <b>Bot OFF</b> shows the maintenance message to all users (admins can still use it).\n"
        "🔔 <b>New-user alerts</b> message every admin when someone starts the bot.",
        b.as_markup(),
    )


@admin_router.callback_query(F.data == "a:set")
async def cb_settings(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_settings(cb)


@admin_router.callback_query(F.data.startswith("a:tog:"))
async def cb_toggle(cb: CallbackQuery):
    bid = cb.bot.id
    key = cb.data.split(":")[2]
    if key in TOGGLES:
        cur = core.setting(bid, key)
        await core.set_setting(bid, key, "0" if cur == "1" else "1")
        core.invalidate(bid, )
    await render_settings(cb)


# ---- texts (messages + Join / Claim button labels) ----
@admin_router.callback_query(F.data == "a:txts")
async def cb_texts(cb: CallbackQuery, state: FSMContext):
    bid = cb.bot.id
    await state.clear()
    b = InlineKeyboardBuilder()
    for key, label in TEXTS.items():
        mark = "📷 " if core.setting(bid, f"media:{key}") else ("💎 " if core.icon(bid, key) else "")
        b.button(text=mark + label, callback_data=f"a:txt:{key}")
    b.button(text="⬅️ Back", callback_data="a:set")
    b.adjust(1)
    await show(
        cb,
        "✏️ <b>Edit texts</b>\n\nPick a message or button. Send the new text <b>formatted in Telegram</b> "
        "(bold, italic, links, <b>premium emoji</b>) — it's detected automatically, <u>no HTML needed</u>.\n\n"
        "📷 Force-join, welcome and extra messages also accept a <b>photo with caption</b>.\n"
        "🔘 <b>Join / Claim</b> buttons: change their label (a premium emoji in it becomes the button icon).\n"
        "You always get a live preview, and ♻️ <b>Reset to default</b> restores the original.",
        b.as_markup(),
    )


async def send_preview(bot: Bot, chat_id: int, key: str, tg_user):
    """Show it exactly as users will see it (rendered, never raw HTML)."""
    bid = bot.id
    if key in BTN_KEYS:
        label = core.setting(bid, key)
        iid = core.icon(bid, key)
        if key == "join_btn":
            label = label.replace("{title}", "Channel name").replace("{n}", "1")
            btn = core.btn(label[:60], url="https://t.me/telegram", icon_id=iid, style=core.style_of(bid, key))
        else:
            btn = core.btn(label[:60], cb="noop", icon_id=iid, style=core.style_of(bid, key))
        return await bot.send_message(chat_id, "👁 <b>Preview</b>", reply_markup=rows_kb([[btn]]))
    tpl = core.setting(bid, key)
    if not tpl.strip():
        return await bot.send_message(chat_id, "ℹ️ <i>Not set — this extra message is currently disabled.</i>")
    text = core.render(tpl, await core.get_user(bid, tg_user.id))
    try:
        await core.send_rich(bot, chat_id, key, text)
    except TelegramBadRequest:
        await bot.send_message(chat_id, "⚠️ This text has invalid formatting:\n\n" + text, parse_mode=None)


@admin_router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:txt:"))
async def cb_txt(cb: CallbackQuery, state: FSMContext, bot: Bot):
    bid = bot.id
    key = cb.data.split(":")[2]
    if key not in LABELS:
        return await cb.answer()
    await state.set_state(EditText.value)
    await state.update_data(key=key)
    rows = [[InlineKeyboardButton(text="♻️ Reset to default", callback_data=f"a:txtdef:{key}")]]
    if core.setting(bid, f"media:{key}"):
        rows.append([InlineKeyboardButton(text="🗑 Remove photo", callback_data=f"a:txtnophoto:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:txts")])
    if key in BTN_KEYS:
        how = ("✍️ Send the new label.\n💎 Include a <b>premium emoji</b> and it becomes the button icon "
               "(without one, any icon is removed).")
    elif key in MEDIA_KEYS:
        how = "✍️ Send the new message — <b>text</b>, or a <b>photo with caption</b>."
    else:
        how = "✍️ Send the new message (formatting & premium emoji are kept)."
    await show(
        cb,
        f"✏️ <b>{LABELS[key]}</b>\n\n🧩 Placeholders: <code>{HINTS.get(key, 'none')}</code>\n\n"
        f"👇 The current version is previewed below.\n{how}",
        rows_kb(rows),
    )
    await send_preview(bot, cb.message.chat.id, key, cb.from_user)


@admin_router.callback_query(F.data.startswith("a:txtdef:"))
async def cb_txtdef(cb: CallbackQuery, state: FSMContext, bot: Bot):
    bid = bot.id
    key = cb.data.split(":")[2]
    if key not in LABELS:
        return await cb.answer()
    await core.reset_setting(bid, key)
    await core.reset_setting(bid, f"media:{key}")
    if key in BTN_KEYS:
        await core.clear_icon(bid, key)
    await state.clear()
    await show(cb, f"♻️ <b>{LABELS[key]}</b> was reset to the default.\n\n👇 Preview:", back("a:txts"))
    await send_preview(bot, cb.message.chat.id, key, cb.from_user)


@admin_router.callback_query(F.data.startswith("a:txtnophoto:"))
async def cb_txtnophoto(cb: CallbackQuery, state: FSMContext, bot: Bot):
    bid = bot.id
    key = cb.data.split(":")[2]
    await core.reset_setting(bid, f"media:{key}")
    await state.clear()
    await show(cb, "🗑 Photo removed.\n\n👇 Preview:", back("a:txts"))
    await send_preview(bot, cb.message.chat.id, key, cb.from_user)


@admin_router.message(EditText.value, F.text)
async def msg_txt(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    key = d["key"]
    if key in BTN_KEYS:
        label, iid = core.split_label(m)
        if not label:
            return await m.answer("❌ Please include some text (not only an emoji).")
        await core.set_setting(bid, key, label[:60])
        if iid:
            await core.set_icon(bid, key, iid)
        else:
            await core.clear_icon(bid, key)
    else:
        await core.set_setting(bid, key, m.html_text)  # entities (bold, links, premium emoji…) -> HTML automatically
    await state.clear()
    await m.answer("✅ <b>Saved!</b> This is how it looks:", reply_markup=back("a:txts"))
    await send_preview(bot, m.chat.id, key, m.from_user)


@admin_router.message(EditText.value, F.photo)
async def msg_txt_photo(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    key = d["key"]
    if key not in MEDIA_KEYS:
        return await m.answer("📷 A photo isn't supported here — please send text.")
    await core.set_setting(bid, f"media:{key}", m.photo[-1].file_id)
    if m.caption:
        await core.set_setting(bid, key, m.html_text)  # caption incl. premium emoji
    await state.clear()
    await m.answer("✅ <b>Saved!</b> This is how it looks:", reply_markup=back("a:txts"))
    await send_preview(bot, m.chat.id, key, m.from_user)


# ---- 🧭 menu buttons: name + reply + layout ----
def extract_reply(m: Message):
    """(html text, photo file_id) of an admin message — formatting & premium emoji are kept automatically."""
    photo = m.photo[-1].file_id if m.photo else None
    text = m.html_text if (m.text or m.caption) else None
    return text, photo


def layout_text(bid) -> str:
    lines = []
    for i, line in enumerate(core.layout_rows(core.MENU.get(bid, [])), 1):
        lines.append(f"Line {i}:  " + "  |  ".join(html.escape(b["label"]) for b in line))
    return "\n".join(lines) or "<i>No buttons yet.</i>"


async def render_menu(cb: CallbackQuery):
    bid = cb.bot.id
    rows = []
    for i, b in enumerate(core.MENU.get(bid, []), 1):
        rows.append([InlineKeyboardButton(text=f"{i}. {b['label']}"[:60], callback_data=f"a:mb:{b['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Add button", callback_data="a:mbadd"),
                 InlineKeyboardButton(text="📐 Layout", callback_data="a:mblay")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:set")])
    await show(
        cb,
        "🧭 <b>Menu buttons</b>\n\nThese are the buttons users see at the bottom of the chat. "
        "Tap one to change its <b>name</b>, the <b>reply</b> it sends (text, photo, premium emoji) or its <b>color</b>.\n\n"
        f"<b>Current layout</b>\n{layout_text(bid)}\n\n"
        "📐 <b>Layout</b> decides how many buttons sit on one line.\n"
        "<i>Users see changes the next time they send /start.</i>",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---- 📐 layout: how many buttons per line, order ----
async def move_menu(bid, mbid: int, delta: int):
    ids = [b["id"] for b in core.MENU.get(bid, [])]
    if mbid in ids:
        i = ids.index(mbid)
        j = i + delta
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for pos, x in enumerate(ids, 1):
            await core.execute("UPDATE bot_menu SET pos=:p WHERE bot_id=:bid_ AND id=:i", p=pos, i=x, bid_=bid)
        await core.load_menu(bid, )


async def render_layout(cb: CallbackQuery):
    bid = cb.bot.id
    rows = [[InlineKeyboardButton(text=f"{n} per line", callback_data=f"a:mbpre:{n}") for n in (1, 2, 3)]]
    for i, b in enumerate(core.MENU.get(bid, [])):
        if i == 0:
            toggle = InlineKeyboardButton(text="🔒 line 1", callback_data="noop")
        else:
            toggle = InlineKeyboardButton(
                text="⤵️ new line" if b["newline"] else "↔️ same line", callback_data=f"a:mbnl:{b['id']}")
        rows.append([
            InlineKeyboardButton(text=b["label"][:18], callback_data=f"a:mb:{b['id']}"),
            InlineKeyboardButton(text="⬆️", callback_data=f"a:mbup:{b['id']}"),
            InlineKeyboardButton(text="⬇️", callback_data=f"a:mbdn:{b['id']}"),
            toggle,
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:menu")])
    await show(
        cb,
        "📐 <b>Menu layout</b>\n\n"
        "• <b>1 / 2 / 3 per line</b> — quick presets\n"
        "• ⬆️⬇️ — change the order\n"
        "• <b>↔️ same line</b> puts a button next to the one before it, <b>⤵️ new line</b> starts a new line "
        "(max 3 per line)\n\n"
        f"<b>Current layout</b>\n{layout_text(bid)}\n\n"
        "<i>Users see changes the next time they send /start.</i>",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_router.callback_query(F.data == "a:mblay")
async def cb_mblay(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_layout(cb)


@admin_router.callback_query(F.data.regexp(r"^a:mbpre:[123]$"))
async def cb_mbpre(cb: CallbackQuery):
    bid = cb.bot.id
    n = int(cb.data.split(":")[2])
    for i, b in enumerate(core.MENU.get(bid, [])):
        await core.execute("UPDATE bot_menu SET newline=:n WHERE bot_id=:bid_ AND id=:i", n=1 if i % n == 0 else 0, i=b["id"], bid_=bid)
    await core.load_menu(bid, )
    await render_layout(cb)


@admin_router.callback_query(F.data.regexp(r"^a:mb(up|dn):\d+$"))
async def cb_mbmove(cb: CallbackQuery):
    bid = cb.bot.id
    _, kind, mbid = cb.data.split(":")
    await move_menu(bid, int(mbid), -1 if kind == "mbup" else 1)
    await render_layout(cb)


@admin_router.callback_query(F.data.regexp(r"^a:mbnl:\d+$"))
async def cb_mbnl(cb: CallbackQuery):
    bid = cb.bot.id
    mbid = int(cb.data.split(":")[2])
    items = [dict(b) for b in core.MENU.get(bid, [])]
    idx = next((i for i, b in enumerate(items) if b["id"] == mbid), None)
    if idx is None or idx == 0:
        return await cb.answer()
    items[idx]["newline"] = 0 if items[idx]["newline"] else 1
    if max(len(r) for r in core.layout_rows(items, cap=99)) > core.MENU_MAX_PER_LINE:
        return await cb.answer(f"A line can hold at most {core.MENU_MAX_PER_LINE} buttons.", show_alert=True)
    await core.execute("UPDATE bot_menu SET newline=:n WHERE bot_id=:bid_ AND id=:i", n=items[idx]["newline"], i=mbid, bid_=bid)
    await core.load_menu(bid, )
    await render_layout(cb)


# ---- one button: name / reply / color / delete ----
async def send_mb_preview(bot: Bot, chat_id: int, b: dict, tg_user):
    """The reply exactly as users get it."""
    bid = bot.id
    u = await core.get_user(bid, tg_user.id)
    text = core.render(b["reply"] or "", u)
    try:
        await core.send_photo_text(bot, chat_id, b["photo"], text)
    except TelegramBadRequest:
        await bot.send_message(chat_id, "⚠️ This reply has invalid formatting:\n\n" + text, parse_mode=None)


async def render_mb(cb: CallbackQuery, bot: Bot, mbid: int, preview: bool = True):
    bid = bot.id
    b = await core.fetchone("SELECT * FROM bot_menu WHERE bot_id=:bid_ AND id=:i", i=mbid, bid_=bid)
    if not b:
        return await render_menu(cb)
    st = b["style"] or ""
    rows = [
        [InlineKeyboardButton(text="✏️ Name", callback_data=f"a:mbname:{mbid}"),
         InlineKeyboardButton(text="✏️ Reply", callback_data=f"a:mbreply:{mbid}")],
        [core.btn(f"{STYLE_DOT.get(st, '⚪')} Color", cb=f"a:mbstyle:{mbid}", style=st or None),
         InlineKeyboardButton(text="🗑 Delete", callback_data=f"a:mbdel:{mbid}")],
    ]
    if b["photo"]:
        rows.append([InlineKeyboardButton(text="🗑 Remove photo", callback_data=f"a:mbnophoto:{mbid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:menu")])
    await show(
        cb,
        f"🧭 <b>{html.escape(b['label'])}</b>\n\n👇 The reply users get is previewed below.\n"
        "🎨 <b>Color</b> cycles ⚪ → 🔵 → 🟢 → 🔴 for this button.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if preview:
        await send_mb_preview(bot, cb.message.chat.id, b, cb.from_user)


@admin_router.callback_query(F.data == "a:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_menu(cb)


@admin_router.callback_query(F.data.regexp(r"^a:mb:\d+$"))
async def cb_mb(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await render_mb(cb, bot, int(cb.data.split(":")[2]))


@admin_router.callback_query(F.data.startswith("a:mbstyle:"))
async def cb_mbstyle(cb: CallbackQuery, bot: Bot):
    bid = bot.id
    parts = cb.data.split(":")
    mbid, from_colors = int(parts[2]), len(parts) > 3  # ':c' suffix = tapped on the Button colors page
    b = await core.fetchone("SELECT style FROM bot_menu WHERE bot_id=:bid_ AND id=:i", i=mbid, bid_=bid)
    if b:
        cur = b["style"] if b["style"] in STYLE_CYCLE else ""
        nxt = STYLE_CYCLE[(STYLE_CYCLE.index(cur) + 1) % len(STYLE_CYCLE)]
        await core.execute("UPDATE bot_menu SET style=:s WHERE bot_id=:bid_ AND id=:i", s=nxt, i=mbid, bid_=bid)
        await core.load_menu(bid, )
    if from_colors:
        return await render_styles(cb)
    await render_mb(cb, bot, mbid, preview=False)


@admin_router.callback_query(F.data.startswith("a:mbdel:"))
async def cb_mbdel(cb: CallbackQuery):
    bid = cb.bot.id
    await core.execute("DELETE FROM bot_menu WHERE bot_id=:bid_ AND id=:i", i=int(cb.data.split(":")[2]), bid_=bid)
    await core.load_menu(bid, )
    await render_menu(cb)


@admin_router.callback_query(F.data.startswith("a:mbnophoto:"))
async def cb_mbnophoto(cb: CallbackQuery, bot: Bot):
    bid = bot.id
    mbid = int(cb.data.split(":")[2])
    await core.execute("UPDATE bot_menu SET photo=NULL WHERE bot_id=:bid_ AND id=:i", i=mbid, bid_=bid)
    await core.load_menu(bid, )
    await render_mb(cb, bot, mbid)


# -- edit name --
@admin_router.callback_query(F.data.startswith("a:mbname:"))
async def cb_mbname(cb: CallbackQuery, state: FSMContext):
    mbid = int(cb.data.split(":")[2])
    await state.set_state(MenuEdit.name)
    await state.update_data(mbid=mbid)
    await show(cb, "✏️ Send the <b>new name</b> for this button.", back(f"a:mb:{mbid}"))


@admin_router.message(MenuEdit.name, F.text)
async def msg_mbname(m: Message, state: FSMContext):
    bid = m.bot.id
    d = await state.get_data()
    label = " ".join(m.text.split())[:60]
    if not label:
        return await m.answer("❌ Please send a name.")
    if any(x["label"] == label and x["id"] != d["mbid"] for x in core.MENU.get(bid, [])):
        return await m.answer("❌ Another button already has this name. Send a different one.")
    await core.execute("UPDATE bot_menu SET label=:l WHERE bot_id=:bid_ AND id=:i", l=label, i=d["mbid"], bid_=bid)
    await core.load_menu(bid, )
    await state.clear()
    await m.answer("✅ <b>Name updated.</b>", reply_markup=back(f"a:mb:{d['mbid']}"))


# -- edit reply --
@admin_router.callback_query(F.data.startswith("a:mbreply:"))
async def cb_mbreply(cb: CallbackQuery, state: FSMContext):
    mbid = int(cb.data.split(":")[2])
    await state.set_state(MenuEdit.reply)
    await state.update_data(mbid=mbid)
    await show(
        cb,
        "✏️ Send the <b>new reply</b> — <b>text</b>, or a <b>photo with caption</b>.\n"
        "Bold, italic, links and <b>premium emoji</b> are kept automatically (no HTML needed).\n"
        "🧩 Placeholders: <code>{name} {id}</code>",
        back(f"a:mb:{mbid}"),
    )


@admin_router.message(MenuEdit.reply, F.text | F.photo)
async def msg_mbreply(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    text, photo = extract_reply(m)
    if photo:
        await core.execute("UPDATE bot_menu SET photo=:p WHERE bot_id=:bid_ AND id=:i", p=photo, i=d["mbid"], bid_=bid)
    if text is not None:
        await core.execute("UPDATE bot_menu SET reply=:r WHERE bot_id=:bid_ AND id=:i", r=text, i=d["mbid"], bid_=bid)
    await core.load_menu(bid, )
    await state.clear()
    await m.answer("✅ <b>Reply saved!</b> This is how it looks:", reply_markup=back(f"a:mb:{d['mbid']}"))
    b = await core.fetchone("SELECT * FROM bot_menu WHERE bot_id=:bid_ AND id=:i", i=d["mbid"], bid_=bid)
    if b:
        await send_mb_preview(bot, m.chat.id, b, m.from_user)


# -- add a new button: name, then reply --
@admin_router.callback_query(F.data == "a:mbadd")
async def cb_mbadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(MenuAdd.name)
    await show(cb, "➕ <b>New menu button</b>\n\nSend the button <b>name</b>, e.g. <code>New Yono App</code>.", back("a:menu"))


@admin_router.message(MenuAdd.name, F.text)
async def msg_mbadd_name(m: Message, state: FSMContext):
    bid = m.bot.id
    label = " ".join(m.text.split())[:60]
    if not label:
        return await m.answer("❌ Please send a name.")
    if any(x["label"] == label for x in core.MENU.get(bid, [])):
        return await m.answer("❌ A button with this name already exists. Send a different name.")
    await state.update_data(label=label)
    await state.set_state(MenuAdd.reply)
    await m.answer(
        "✍️ Now send the <b>reply</b> users get when they tap it — text, or a photo with caption "
        "(formatting & premium emoji are kept)."
    )


@admin_router.message(MenuAdd.reply, F.text | F.photo)
async def msg_mbadd_reply(m: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    d = await state.get_data()
    text, photo = extract_reply(m)
    pos = int(await core.scalar("SELECT COALESCE(MAX(pos),0) FROM bot_menu WHERE bot_id=:bid_", bid_=bid) or 0) + 1
    mbid = await core.insert_returning(
        "INSERT INTO bot_menu(bot_id, label, reply, photo, pos, newline) VALUES(:bid_, :l, :r, :p, :o, 1)",
        l=d["label"], r=text or "", p=photo, o=pos, bid_=bid,
    )
    await core.load_menu(bid, )
    await state.clear()
    await m.answer(
        f"✅ Button <b>{html.escape(d['label'])}</b> added (on its own line — change it in 📐 Layout). "
        "Users see it after their next /start.",
        reply_markup=back("a:menu"),
    )
    b = await core.fetchone("SELECT * FROM bot_menu WHERE bot_id=:bid_ AND id=:i", i=mbid, bid_=bid)
    if b:
        await send_mb_preview(bot, m.chat.id, b, m.from_user)


# ---- 🎨 button colors (primary / success / danger) ----
STYLE_KEYS = {
    "join_btn": "Join button",
    "check_btn": "Claim button",
}
STYLE_CYCLE = ["", "primary", "success", "danger"]
STYLE_DOT = {"": "⚪", "primary": "🔵", "success": "🟢", "danger": "🔴"}


async def render_styles(cb: CallbackQuery):
    bid = cb.bot.id
    rows = []
    for key, label in STYLE_KEYS.items():
        cur = core.style_of(bid, key) or ""
        # each button wears its own colour, so this page is a live preview
        rows.append([core.btn(f"{STYLE_DOT[cur]} {label}", cb=f"a:style:{key}", style=cur or None)])
    for b in core.MENU.get(bid, []):  # menu buttons are listed here automatically
        cur = b["style"] if b["style"] in core.STYLES else ""
        rows.append([core.btn(f"{STYLE_DOT[cur]} {b['label']}"[:60], cb=f"a:mbstyle:{b['id']}:c", style=cur or None)])
    rows.append([InlineKeyboardButton(text="♻️ Reset colors", callback_data="a:styledef")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="a:set")])
    await show(
        cb,
        "🎨 <b>Button colors</b>\n\nTap a button to change its color:\n"
        "⚪ default → 🔵 primary → 🟢 success → 🔴 danger\n\n"
        "Join is 🔵 and Claim is 🟢 by default. Your <b>menu buttons</b> appear here automatically "
        "(new ones too). Menu colors reach users the next time they send /start.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_router.callback_query(F.data == "a:styles")
async def cb_styles(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_styles(cb)


@admin_router.callback_query(F.data.regexp(r"^a:style:[a-z_]+$"))
async def cb_style(cb: CallbackQuery):
    bid = cb.bot.id
    key = cb.data.split(":")[2]
    if key in STYLE_KEYS:
        cur = core.style_of(bid, key) or ""
        await core.set_setting(bid, f"style:{key}", STYLE_CYCLE[(STYLE_CYCLE.index(cur) + 1) % len(STYLE_CYCLE)])
    await render_styles(cb)


@admin_router.callback_query(F.data == "a:styledef")
async def cb_styledef(cb: CallbackQuery):
    bid = cb.bot.id
    for key in STYLE_KEYS:
        await core.reset_setting(bid, f"style:{key}")
    await core.execute("UPDATE bot_menu SET style='' WHERE bot_id=:bid_", bid_=bid)
    await core.load_menu(bid, )
    await render_styles(cb)


# ───────────────────────── users ─────────────────────────
@admin_router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔎 Find user", callback_data="a:ufind")
    b.button(text="📥 Export user IDs", callback_data="a:uexp")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1)
    await show(cb, "👥 <b>Users</b>\n\nFind a user to ban or unban.", b.as_markup())


@admin_router.callback_query(F.data == "a:uexp")
async def cb_export(cb: CallbackQuery):
    bid = cb.bot.id
    rows = await core.fetch("SELECT id FROM bot_users WHERE bot_id=:bid_ ORDER BY id", bid_=bid)
    data = "\n".join(str(r["id"]) for r in rows).encode()
    await cb.message.answer_document(BufferedInputFile(data, "users.txt"), caption=f"📥 {len(rows)} users")
    await cb.answer()


@admin_router.callback_query(F.data == "a:ufind")
async def cb_ufind(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserLookup.query)
    await show(cb, "🔎 Send a user <b>ID</b> or <b>@username</b>.", back("a:users"))


def user_card(u: dict) -> tuple[str, InlineKeyboardMarkup]:
    name = html.escape(u.get("first_name") or "-")
    uname = f"@{u['username']}" if u.get("username") else "-"
    status = "⛔ banned" if u["banned"] else ("🚫 blocked bot" if u["blocked"] else "✅ active")
    b = InlineKeyboardBuilder()
    if u["banned"]:
        b.button(text="✅ Unban", callback_data=f"a:unban:{u['id']}")
    else:
        b.button(text="⛔ Ban", callback_data=f"a:ban:{u['id']}")
    b.button(text="⬅️ Back", callback_data="a:users")
    b.adjust(2)
    text = (
        f"👤 <b>{name}</b>\n"
        f"🆔 <code>{u['id']}</code>\n"
        f"🔗 {uname}\n"
        f"📌 Status: {status}"
    )
    return text, b.as_markup()


@admin_router.message(UserLookup.query, F.text)
async def msg_ufind(m: Message, state: FSMContext):
    bid = m.bot.id
    t = m.text.strip()
    if t.lstrip("-").isdigit():
        u = await core.fetchone("SELECT * FROM bot_users WHERE bot_id=:bid_ AND id=:i", i=int(t), bid_=bid)
    else:
        u = await core.fetchone("SELECT * FROM bot_users WHERE bot_id=:bid_ AND LOWER(username)=:u", u=t.lstrip("@").lower(), bid_=bid)
    if not u:
        return await m.answer("User not found (they must have started the bot at least once).")
    await state.clear()
    text, kb = user_card(u)
    await m.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data.regexp(r"^a:(ban|unban):-?\d+$"))
async def cb_ban(cb: CallbackQuery):
    bid = cb.bot.id
    _, action, uid = cb.data.split(":")
    uid = int(uid)
    if uid in core.ADMINS:
        return await cb.answer("You can't ban an admin.", show_alert=True)
    await core.execute("UPDATE bot_users SET banned=:b WHERE bot_id=:bid_ AND id=:i", b=1 if action == "ban" else 0, i=uid, bid_=bid)
    text, kb = user_card(await core.get_user(bid, uid))
    await show(cb, text, kb)


# ───────────────────────── admins (owners only) ─────────────────────────
async def render_admins(cb: CallbackQuery):
    rows = await core.fetch("SELECT user_id FROM admins ORDER BY user_id")
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"🗑 {r['user_id']}", callback_data=f"a:admdel:{r['user_id']}")
    b.button(text="➕ Add admin", callback_data="a:admadd")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(2)
    owners = ", ".join(f"<code>{o}</code>" for o in sorted(core.OWNERS)) or "-"
    await show(cb, f"👮 <b>Admins</b>\n\nOwners (from env): {owners}\nTap an ID to remove that admin.", b.as_markup())


@admin_router.callback_query(F.data == "a:adm", F.from_user.id.in_(core.OWNERS))
async def cb_admins(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_admins(cb)


@admin_router.callback_query(F.data == "a:admadd", F.from_user.id.in_(core.OWNERS))
async def cb_admadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddAdmin.uid)
    await show(cb, "Send the Telegram <b>user ID</b> of the new admin.", back("a:adm"))


@admin_router.message(AddAdmin.uid, F.text, F.from_user.id.in_(core.OWNERS))
async def msg_admadd(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit():
        return await m.answer("Send a numeric user ID.")
    await core.execute("INSERT INTO admins(user_id) VALUES(:u) ON CONFLICT DO NOTHING", u=int(t))
    await core.load_admins()
    await state.clear()
    await m.answer(f"✅ <code>{t}</code> is now an admin.", reply_markup=back("a:adm"))


@admin_router.callback_query(F.data.startswith("a:admdel:"), F.from_user.id.in_(core.OWNERS))
async def cb_admdel(cb: CallbackQuery):
    await core.execute("DELETE FROM admins WHERE user_id=:u", u=int(cb.data.split(":")[2]))
    await core.load_admins()
    await render_admins(cb)


# ───────────────────────── broadcast ─────────────────────────
BC: dict[int, dict] = {}  # one broadcast state per bot


def bc_state(bid: int) -> dict:
    return BC.setdefault(bid, {"task": None, "cancel": False})


def bc_running(bid: int) -> bool:
    t = bc_state(bid)["task"]
    return t is not None and not t.done()


def build_markup(buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, url=u)] for t, u in buttons])


@admin_router.callback_query(F.data == "a:bc")
async def cb_bc(cb: CallbackQuery, state: FSMContext):
    bid = cb.bot.id
    await state.clear()
    if bc_running(bid):
        b = InlineKeyboardBuilder()
        b.button(text="🛑 Stop broadcast", callback_data="a:bcstop")
        b.button(text="⬅️ Back", callback_data="a:home")
        b.adjust(1)
        return await show(cb, "⏳ A broadcast is currently running.", b.as_markup())
    await state.set_state(Broadcast.msg)
    await show(
        cb,
        "📨 <b>Broadcast</b>\n\n"
        "Send the message you want to deliver to everyone — text, photo, video, file… anything.\n\n"
        "✨ Format it right inside Telegram: <b>bold</b>, <i>italic</i>, links and <b>premium emoji</b> "
        "are all kept exactly as you send them. <u>No HTML needed.</u>",
        back(),
    )


@admin_router.callback_query(F.data == "a:bcstop")
async def cb_bcstop(cb: CallbackQuery):
    bc_state(cb.bot.id)["cancel"] = True
    await cb.answer("Stopping…", show_alert=True)


@admin_router.message(Broadcast.msg)
async def msg_bc(m: Message, state: FSMContext):
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id, buttons=[])
    await state.set_state(Broadcast.buttons)
    b = InlineKeyboardBuilder()
    b.button(text="⏭ Skip (no buttons)", callback_data="a:bcskip")
    b.button(text="❌ Cancel", callback_data="a:home")
    b.adjust(1)
    await m.answer(
        "🔘 <b>Add link buttons?</b>\n\nSend one per line:\n"
        "<code>Button text | https://example.com</code>\n\nOr skip.",
        reply_markup=b.as_markup(),
    )


async def bc_preview(bot: Bot, chat_id: int, state: FSMContext):
    bid = bot.id
    d = await state.get_data()
    try:
        await bot.copy_message(chat_id, d["src_chat"], d["src_msg"], reply_markup=build_markup(d.get("buttons")))
    except TelegramAPIError as e:
        await bot.send_message(chat_id, f"❌ Preview failed: {html.escape(str(e))}")
        await state.clear()
        return
    n = await core.scalar("SELECT COUNT(*) FROM bot_users WHERE bot_id=:bid_ AND blocked=0 AND banned=0", bid_=bid)
    b = InlineKeyboardBuilder()
    b.button(text=f"🚀 Send to {n} users", callback_data="a:bcgo")
    b.button(text="❌ Cancel", callback_data="a:home")
    b.adjust(1)
    await state.set_state(Broadcast.confirm)
    await bot.send_message(chat_id, "👆 <b>Preview</b> — everything look good?", reply_markup=b.as_markup())


@admin_router.callback_query(Broadcast.buttons, F.data == "a:bcskip")
async def cb_bcskip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    await bc_preview(bot, cb.message.chat.id, state)


@admin_router.message(Broadcast.buttons, F.text)
async def msg_bcbtn(m: Message, state: FSMContext, bot: Bot):
    buttons = []
    for line in m.text.splitlines():
        if "|" not in line:
            continue
        t, u = (x.strip() for x in line.split("|", 1))
        if t and u.startswith(("http://", "https://", "tg://")):
            buttons.append([t, u])
    if not buttons:
        return await m.answer("No valid buttons found. Use <code>Text | https://link</code> or press Skip.")
    await state.update_data(buttons=buttons)
    await bc_preview(bot, m.chat.id, state)


@admin_router.callback_query(Broadcast.confirm, F.data == "a:bcgo")
async def cb_bcgo(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if bc_running(bot.id):
        return await cb.answer("A broadcast is already running.", show_alert=True)
    d = await state.get_data()
    await state.clear()
    status = await cb.message.edit_text("⏳ Starting broadcast…")
    bc_state(bot.id)["task"] = asyncio.create_task(
        run_broadcast(bot, status, d["src_chat"], d["src_msg"], build_markup(d.get("buttons")))
    )
    await cb.answer()


async def run_broadcast(bot: Bot, status: Message, src_chat: int, src_msg: int, markup):
    bid = bot.id
    ids = [r["id"] for r in await core.fetch("SELECT id FROM bot_users WHERE bot_id=:bid_ AND blocked=0 AND banned=0", bid_=bid)]
    stats = {"ok": 0, "blocked": 0, "failed": 0}
    bc_state(bid)["cancel"] = False

    async def send(uid: int):
        for _ in range(2):
            try:
                # copy_message keeps text formatting + premium emoji exactly as the admin sent it
                await bot.copy_message(uid, src_chat, src_msg, reply_markup=markup)
                stats["ok"] += 1
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await core.execute("UPDATE bot_users SET blocked=1 WHERE bot_id=:bid_ AND id=:i", i=uid, bid_=bid)
                stats["blocked"] += 1
                return
            except TelegramAPIError:
                stats["failed"] += 1
                return
        stats["failed"] += 1

    total = len(ids)
    done_batches = 0
    for i in range(0, total, 25):  # ~25 msgs / second, under Telegram's 30/s limit
        if bc_state(bid)["cancel"]:
            break
        await asyncio.gather(*(send(uid) for uid in ids[i:i + 25]))
        done_batches += 1
        await asyncio.sleep(1)
        if done_batches % 8 == 0:
            try:
                await status.edit_text(f"⏳ Broadcasting… {min(i + 25, total)}/{total}")
            except TelegramAPIError:
                pass

    title = "🛑 <b>Broadcast stopped</b>" if bc_state(bid)["cancel"] else "✅ <b>Broadcast finished</b>"
    text = (
        f"{title}\n\n📬 Delivered: <b>{stats['ok']}</b>\n🚫 Blocked bot: <b>{stats['blocked']}</b>\n"
        f"⚠️ Failed: <b>{stats['failed']}</b>\n👥 Targeted: <b>{total}</b>"
    )
    try:
        await status.edit_text(text, reply_markup=back())
    except TelegramAPIError:
        await bot.send_message(status.chat.id, text)





menu_router = Router(name="menu")
ANY = StateFilter("*")


class CustomBtn(BaseFilter):
    """Matches the label of a menu button and hands that button to the handler."""

    async def __call__(self, m: Message):
        bid = m.bot.id
        t = (m.text or "").strip()
        for b in core.MENU.get(bid, []):
            if b["label"] == t:
                return {"mbtn": b}
        return False


@menu_router.message(ANY, Command("cancel"))
async def user_cancel(m: Message, state: FSMContext):
    bid = m.bot.id
    await state.clear()
    await m.answer("✖️ Cancelled.", reply_markup=core.main_kb(bid))


@menu_router.message(ANY, CustomBtn())
async def custom_button(m: Message, state: FSMContext, bot: Bot, mbtn: dict):
    bid = bot.id
    await state.clear()
    u = await core.get_user(bid, m.from_user.id)
    text = core.render(mbtn["reply"] or "", u)
    await core.send_photo_text(bot, m.chat.id, mbtn["photo"], text)





user_router = Router(name="user")


async def welcome_text(bid, uid: int) -> str:
    u = await core.get_user(bid, uid)
    return core.render(await core.get_setting(bid, "welcome"), u)


@user_router.message(StateFilter("*"), CommandStart())
async def cmd_start(msg: Message, state: FSMContext, bot: Bot):
    bid = bot.id
    await state.clear()
    await core.send_rich(bot, msg.chat.id, "welcome", await welcome_text(bid, msg.from_user.id), core.main_kb(bid))


@user_router.callback_query(F.data == "check")
async def check_cb(cb: CallbackQuery, bot: Bot):
    bid = bot.id
    uid = cb.from_user.id
    missing = await core.missing_channels(bot, uid, use_cache=False)
    # Claim always removes THIS join message and its extra message (only these two, only on Claim)
    await core.delete_prompt(bot, uid, cb.message.message_id)
    if missing:
        # no pop-up: just show a fresh join message (+ extra message)
        await send_join_prompt(bot, uid, await core.prompt_channels(bid, missing))
        return await cb.answer()
    await core.execute("UPDATE bot_users SET verified=1 WHERE bot_id=:bid_ AND id=:u", u=uid, bid_=bid)
    await core.send_rich(bot, uid, "welcome", await welcome_text(bid, uid), core.main_kb(bid))
    await cb.answer()


@user_router.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    """A user requested to join one of our channels -> remember it (counts as 'joined')."""
    bid = req.bot.id
    ch = await core.fetchone("SELECT chat_id FROM bot_channels WHERE bot_id=:bid_ AND chat_id=:c", c=req.chat.id, bid_=bid)
    if not ch:
        return
    await core.execute(
        "INSERT INTO bot_requests(bot_id, user_id, chat_id) VALUES(:bid_, :u, :c) ON CONFLICT DO NOTHING",
        u=req.from_user.id, c=req.chat.id, bid_=bid,
    )
    core.invalidate(bid, req.from_user.id)
    if await core.get_setting(bid, "auto_approve") == "1":
        try:
            await req.approve()
        except TelegramAPIError as e:
            log.warning("auto-approve failed: %s", e)


@user_router.chat_member()
async def on_chat_member(ev: ChatMemberUpdated):
    """Clean up stale join requests when the user actually joins / leaves / is removed."""
    bid = ev.bot.id
    status = ev.new_chat_member.status
    if status in (
        ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR,
        ChatMemberStatus.LEFT, ChatMemberStatus.KICKED,
    ):
        uid = ev.new_chat_member.user.id
        await core.execute("DELETE FROM bot_requests WHERE bot_id=:bid_ AND user_id=:u AND chat_id=:c", u=uid, c=ev.chat.id, bid_=bid)
        core.invalidate(bid, uid)


# ═══════════════════════════ wiring ═══════════════════════════
def register(dp):
    """Attach the middleware and all routers to a Dispatcher (one Dispatcher serves every bot)."""
    gate = GateMiddleware()
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)
    dp.include_router(admin_router)  # admin first so its FSM states win
    dp.include_router(menu_router)
    dp.include_router(user_router)
