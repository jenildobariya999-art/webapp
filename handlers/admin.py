"""Admin panel (/admin): stats, channels, broadcast, users, withdrawals, settings, admins."""
import asyncio
import html
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import icons
import services
from keyboards import wd_kb

log = logging.getLogger(__name__)
router = Router(name="admin")
ANY = StateFilter("*")


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and u.id in config.ADMINS)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


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


class EditText(StatesGroup):
    value = State()


class EditNum(StatesGroup):
    value = State()


class UserLookup(StatesGroup):
    query = State()


class AdjustBal(StatesGroup):
    amount = State()


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
    b.button(text="💸 Withdrawals", callback_data="a:wd")
    b.button(text="⚙️ Settings", callback_data="a:set")
    if user_id in config.OWNERS:
        b.button(text="👮 Admins", callback_data="a:adm")
    b.adjust(2)
    return b.as_markup()


ADMIN_HOME = "🛠 <b>Admin panel</b>\n\nWhat would you like to manage?"


@router.message(ANY, Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(ADMIN_HOME, reply_markup=menu_kb(m.from_user.id))


@router.message(ANY, Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Cancelled.")


@router.callback_query(F.data == "a:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, ADMIN_HOME, menu_kb(cb.from_user.id))


# ───────────────────────── stats ─────────────────────────
@router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    total = await db.scalar("SELECT COUNT(*) FROM users")
    blocked = await db.scalar("SELECT COUNT(*) FROM users WHERE blocked=1")
    banned = await db.scalar("SELECT COUNT(*) FROM users WHERE banned=1")
    chans = await db.scalar("SELECT COUNT(*) FROM channels")
    reqs = await db.scalar("SELECT COUNT(*) FROM join_requests")
    owed = int(await db.scalar("SELECT COALESCE(SUM(balance),0) FROM users") or 0)
    paid = int(await db.scalar("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='paid'") or 0)
    pend = await db.fetchone("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS s FROM withdrawals WHERE status='pending'")
    await show(
        cb,
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"✅ Reachable: <b>{total - blocked - banned}</b>\n"
        f"🚫 Blocked the bot: <b>{blocked}</b>\n"
        f"⛔ Banned: <b>{banned}</b>\n\n"
        f"📢 Force-join channels: <b>{chans}</b>\n"
        f"📨 Stored join requests: <b>{reqs}</b>\n\n"
        f"💰 Wallet balances (owed): <b>₹{services.fmt(owed)}</b>\n"
        f"⏳ Pending withdrawals: <b>{pend['n']}</b> (₹{services.fmt(pend['s'])})\n"
        f"✅ Total paid out: <b>₹{services.fmt(paid)}</b>\n\n"
        f"👮 Admins: <b>{len(config.ADMINS)}</b>",
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


async def ordered_channels() -> list[dict]:
    return await db.fetch("SELECT * FROM channels ORDER BY pos, added, chat_id")


async def next_pos() -> int:
    return int(await db.scalar("SELECT COALESCE(MAX(pos),0) FROM channels") or 0) + 1


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


async def move_channel(cid: int, delta: int):
    ids = [r["chat_id"] for r in await ordered_channels()]
    if cid in ids:
        i = ids.index(cid)
        j = i + delta
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for pos, c in enumerate(ids, 1):
            await db.execute("UPDATE channels SET pos=:p WHERE chat_id=:c", p=pos, c=c)


async def render_channels(cb: CallbackQuery):
    rows = await ordered_channels()
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
    any_mode = services.setting("join_mode") == "any"
    kb += [
        [pb("➕", "Add Channel", "a:chadd")],
        [pb("➕", "Add Social Link", "a:chaddsoc")],
        [pb("➕", "Add Uncheck Channel", "a:chaddu")],
        [pb("📡", "Channel Broadcast", "a:chbc")],
        [pb("🎛", f"Join Mode: {'Any One Channel' if any_mode else 'All Channels'}", "a:joinmode")],
        [pb("⬅️", "Back", "a:home")],
    ]
    await show(
        cb,
        "📢 <b>Force-join channels</b>\n\n"
        "Tap a <b>name</b> to change its link • ❌ delete • ⬆️⬇️ reorder • 👀 details\n"
        "📨 request mode  •  ☑️ unchecked  •  🔗 social link\n\n"
        f"🎛 <b>Join mode:</b> {'users must join <u>any one</u> channel' if any_mode else 'users must join <u>all</u> channels'}\n"
        f"Total: <b>{len(rows)}</b>",
        InlineKeyboardMarkup(inline_keyboard=kb),
    )


@router.callback_query(F.data == "a:ch")
async def cb_channels(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_channels(cb)


@router.callback_query(F.data.startswith("a:chdel:"))
async def cb_chdel(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    await db.execute("DELETE FROM channels WHERE chat_id=:c", c=cid)
    await db.execute("DELETE FROM join_requests WHERE chat_id=:c", c=cid)
    services.invalidate()
    await render_channels(cb)


@router.callback_query(F.data.regexp(r"^a:ch(up|dn):-?\d+$"))
async def cb_chmove(cb: CallbackQuery):
    _, kind, cid = cb.data.split(":")
    await move_channel(int(cid), -1 if kind == "chup" else 1)
    await render_channels(cb)


@router.callback_query(F.data == "a:joinmode")
async def cb_joinmode(cb: CallbackQuery):
    await services.set_setting("join_mode", "all" if services.setting("join_mode") == "any" else "any")
    services.invalidate()
    await render_channels(cb)


# ---- tap on a channel name: see + change its link(s) ----
def two_links(ch: dict) -> bool:
    """Real, verified channels carry two auto-generated links (normal + request)."""
    return ch["kind"] == "channel" and ch["chat_id"] < 0 and not ch["unchecked"]


async def render_link_editor(cb: CallbackQuery, cid: int):
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
    if not ch:
        return await render_channels(cb)
    title = html.escape(ch["title"] or str(cid))
    rows = []
    if two_links(ch):
        req_on = bool(ch["request_mode"])
        text = (
            f"🔗 <b>{title}</b>\n\n"
            f"📢 <b>Normal link</b>{'' if req_on else '  ✅ in use'}\n{html.escape(ch['link'])}\n\n"
            f"📨 <b>Request link</b>{'  ✅ in use' if req_on else ''}\n{html.escape(ch['req_link'] or '—')}\n\n"
            "Tap a button to replace a link, or auto-generate fresh ones.\n"
            "<i>Switch which one users get from 👀 details.</i>"
        )
        rows.append([InlineKeyboardButton(text="✏️ Normal link", callback_data=f"a:chedit:{cid}:link"),
                     InlineKeyboardButton(text="✏️ Request link", callback_data=f"a:chedit:{cid}:req")])
        rows.append([InlineKeyboardButton(text="♻️ Auto-generate both", callback_data=f"a:chregen:{cid}")])
    else:
        text = f"🔗 <b>{title}</b>\n\nCurrent link:\n{html.escape(ch['link'])}\n\nTap the button to replace it."
        rows.append([InlineKeyboardButton(text="✏️ Change link", callback_data=f"a:chedit:{cid}:link")])
    rows.append([pb("⬅️", "Back", "a:ch")])
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.regexp(r"^a:chlink:-?\d+$"))
async def cb_chlink(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_link_editor(cb, int(cb.data.split(":")[2]))


@router.callback_query(F.data.regexp(r"^a:chedit:-?\d+:(link|req)$"))
async def cb_chedit(cb: CallbackQuery, state: FSMContext):
    _, _, cid, which = cb.data.split(":")
    await state.set_state(EditLink.link)
    await state.update_data(cid=int(cid), which=which)
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=int(cid))
    cur = (ch["req_link"] if which == "req" else ch["link"]) if ch else ""
    await show(
        cb,
        f"✏️ <b>New {'request ' if which == 'req' else ''}link</b>\n\n"
        f"Current:\n{html.escape(cur or '—')}\n\n"
        "Send the new link (<code>https://t.me/…</code> or <code>@username</code>).\n"
        "✅ It will be used for users immediately.",
        back(f"a:chlink:{cid}"),
    )


@router.message(EditLink.link, F.text)
async def msg_editlink(m: Message, state: FSMContext):
    d = await state.get_data()
    link = normalize_link(m.text)
    if not link:
        return await m.answer("❌ Send a valid link, e.g. <code>https://t.me/+AbCd…</code> or <code>@username</code>")
    is_req = d["which"] == "req"
    col = "req_link" if is_req else "link"  # fixed whitelist, never user input
    # the link you just changed becomes the one users get, automatically
    await db.execute(
        f"UPDATE channels SET {col}=:l, request_mode=:m WHERE chat_id=:c",
        l=link, m=1 if is_req else 0, c=d["cid"],
    )
    services.invalidate()
    await state.clear()
    await m.answer(
        f"✅ <b>{'Request' if is_req else 'Normal'} link updated</b> and now in use — users get it right away.",
        reply_markup=back(f"a:chlink:{d['cid']}"),
    )


@router.callback_query(F.data.regexp(r"^a:chregen:-?\d+$"))
async def cb_chregen(cb: CallbackQuery, bot: Bot):
    cid = int(cb.data.split(":")[2])
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    try:
        chat = await bot.get_chat(cid)
    except TelegramAPIError as e:
        return await cb.answer(f"Failed: {e}"[:190], show_alert=True)
    normal, req, warn = await build_links(bot, chat)
    if not normal and not req:
        return await cb.answer(f"❌ {warn}"[:190], show_alert=True)
    await db.execute(
        "UPDATE channels SET link=:l, req_link=:r WHERE chat_id=:c",
        l=normal or ch["link"], r=req or ch["req_link"], c=cid,
    )
    services.invalidate()
    await render_link_editor(cb, cid)


# ---- 👀 channel details ----
async def render_channel_view(cb: CallbackQuery, bot: Bot, cid: int):
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
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
        f"🆔 <code>{cid}</code>\n📌 Type: {typ}\n🔗 {html.escape(services.join_url(ch))}"
    )
    if cid < 0:
        try:
            text += f"\n👥 Members: <b>{await bot.get_chat_member_count(cid)}</b>"
        except TelegramAPIError:
            text += "\n👥 Members: <i>n/a</i>"
        pend = await db.scalar("SELECT COUNT(*) FROM join_requests WHERE chat_id=:c", c=cid)
        if pend:
            text += f"\n📨 Pending join requests: <b>{pend}</b>"
    rows = [[InlineKeyboardButton(text="🔗 Links", callback_data=f"a:chlink:{cid}")]]
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


@router.callback_query(F.data.regexp(r"^a:chview:-?\d+$"))
async def cb_chview(cb: CallbackQuery, bot: Bot):
    await render_channel_view(cb, bot, int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("a:chmode:"))
async def cb_chmode(cb: CallbackQuery, bot: Bot):
    """Switch which of the two links users receive (both already exist)."""
    cid = int(cb.data.split(":")[2])
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
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
        await db.execute("UPDATE channels SET req_link=:r WHERE chat_id=:c", r=req, c=cid)
    await db.execute("UPDATE channels SET request_mode=:m WHERE chat_id=:c", m=new, c=cid)
    services.invalidate()
    await render_channel_view(cb, bot, cid)


@router.callback_query(F.data.startswith("a:chchk:"))
async def cb_chchk(cb: CallbackQuery, bot: Bot):
    """Toggle between 'checked' (verified) and 'unchecked' (display only)."""
    cid = int(cb.data.split(":")[2])
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    if ch["unchecked"]:
        try:
            me = await bot.get_chat_member(cid, bot.id)
            if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                raise ValueError
        except (TelegramAPIError, ValueError):
            return await cb.answer("❌ To check a channel the bot must be an admin there.", show_alert=True)
        await db.execute("UPDATE channels SET unchecked=0 WHERE chat_id=:c", c=cid)
    else:
        await db.execute("UPDATE channels SET unchecked=1 WHERE chat_id=:c", c=cid)
        await db.execute("UPDATE users SET verified=0")  # show it to everyone once
    services.invalidate()
    await render_channel_view(cb, bot, cid)


# ---- 📡 channel broadcast (post into your channels) ----
@router.callback_query(F.data == "a:chbc")
async def cb_chbc(cb: CallbackQuery, state: FSMContext):
    rows = [c for c in await ordered_channels() if c["chat_id"] < 0]
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


@router.message(ChBroadcast.msg)
async def msg_chbc(m: Message, state: FSMContext, bot: Bot):
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    try:
        await bot.copy_message(m.chat.id, m.chat.id, m.message_id)
    except TelegramAPIError as e:
        await state.clear()
        return await m.answer(f"❌ Preview failed: {html.escape(str(e))}")
    n = len([c for c in await ordered_channels() if c["chat_id"] < 0])
    b = InlineKeyboardBuilder()
    b.button(text=f"🚀 Post in {n} channel(s)", callback_data="a:chbcgo")
    b.button(text="❌ Cancel", callback_data="a:ch")
    b.adjust(1)
    await state.set_state(ChBroadcast.confirm)
    await m.answer("👆 <b>Preview</b> — publish it?", reply_markup=b.as_markup())


@router.callback_query(ChBroadcast.confirm, F.data == "a:chbcgo")
async def cb_chbcgo(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    ok, failed = [], []
    for ch in [c for c in await ordered_channels() if c["chat_id"] < 0]:
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
@router.callback_query(F.data == "a:chadd")
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


@router.message(AddChannel.chat)
async def msg_chadd(m: Message, state: FSMContext, bot: Bot):
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
    await db.execute(
        "INSERT INTO channels(chat_id, title, link, req_link, request_mode, unchecked, kind, pos) "
        "VALUES(:c, :t, :l, :r, 0, 0, 'channel', :p) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
        "req_link=excluded.req_link, unchecked=0, kind='channel'",
        c=chat.id, t=title, l=normal or req, r=req, p=await next_pos(),
    )
    services.invalidate()
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
async def save_display_only(chat_id: int, title: str, link: str, kind: str = "channel", icon: str | None = None):
    await db.execute(
        "INSERT INTO channels(chat_id, title, link, request_mode, unchecked, kind, pos, icon) "
        "VALUES(:c, :t, :l, 0, 1, :k, :p, :i) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
        "request_mode=0, unchecked=1, kind=excluded.kind, icon=excluded.icon",
        c=chat_id, t=title, l=link, k=kind, p=await next_pos(), i=icon,
    )
    await db.execute("UPDATE users SET verified=0")  # every user sees the new button once
    services.invalidate()


def synthetic_id() -> int:
    # positive id: real channel ids are always negative, so no clash
    return int(time.time() * 1000)


@router.callback_query(F.data == "a:chaddu")
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


@router.message(AddUnchecked.link)
async def msg_chaddu(m: Message, state: FSMContext, bot: Bot):
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
        await save_display_only(chat_id or synthetic_id(), title, link)
        await state.clear()
        return await m.answer(f"✅ Added <b>{html.escape(title)}</b> as ☑️ unchecked.", reply_markup=back("a:ch"))

    await state.update_data(link=link, chat_id=chat_id)
    await state.set_state(AddUnchecked.title)
    await m.answer("✍️ Now send the <b>name</b> of this channel (it replaces <code>{title}</code> in the Join button label).")


@router.message(AddUnchecked.title, F.text)
async def msg_chaddu_title(m: Message, state: FSMContext):
    d = await state.get_data()
    title = m.text.strip()[:60]
    await save_display_only(d.get("chat_id") or synthetic_id(), title, d["link"])
    await state.clear()
    await m.answer(f"✅ Added <b>{html.escape(title)}</b> as ☑️ unchecked.", reply_markup=back("a:ch"))


@router.callback_query(F.data == "a:chaddsoc")
async def cb_chaddsoc(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddSocial.link)
    await show(
        cb,
        "🔗 <b>Add a social link</b>\n\nA plain link button (Instagram, YouTube, website…). "
        "Never verified.\n\nSend the full link, e.g. <code>https://instagram.com/yourpage</code>",
        back("a:ch"),
    )


@router.message(AddSocial.link, F.text)
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


@router.message(AddSocial.title, F.text)
async def msg_chaddsoc_title(m: Message, state: FSMContext):
    d = await state.get_data()
    label, iid = icons.split_label(m)
    if not label:
        return await m.answer("❌ Please include some text (not only an emoji).")
    await save_display_only(synthetic_id(), label[:60], d["link"], kind="social", icon=iid)
    await state.clear()
    await m.answer(f"✅ Social link <b>{html.escape(label)}</b> added.", reply_markup=back("a:ch"))


# ───────────────────────── settings ─────────────────────────
TOGGLES = {
    "bot_enabled": "🤖 Bot",
    "force_join": "🔒 Force join",
    "auto_approve": "📨 Auto-approve requests",
    "withdraw_on": "💸 Withdrawals",
    "notify_new_user": "🔔 New-user alerts",
}
TEXTS = {
    "welcome": "👋 Welcome message",
    "force_text": "🔒 Force-join message",
    "force_extra": "➕ Extra message (after force-join)",
    "off_text": "🛠 Maintenance message",
    "refer_text": "👥 Refer & Earn message",
    "join_btn": "🔘 Join button",
    "check_btn": "🔘 Claim button",
}
BTN_KEYS = {"join_btn", "check_btn"}  # short button labels (plain text, optional premium-emoji icon)
LABELS = TEXTS
MEDIA_KEYS = {"welcome", "force_text", "force_extra"}  # these can carry a photo
NUMS = {
    "refer_reward": ("👥 Referral reward", "₹"),
    "bonus_amount": ("🎁 Bonus amount", "₹"),
    "bonus_hours": ("⏳ Bonus cooldown", "hours"),
    "min_withdraw": ("📉 Min. withdrawal", "₹"),
}
HINTS = {
    "welcome": "{name} {balance} {refs} {id}",
    "off_text": "{name}",
    "refer_text": "{link} {reward} {refs} {name}",
    "join_btn": "{title} {n}",
}


def rows_kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_settings(cb: CallbackQuery):
    b = InlineKeyboardBuilder()
    for key, label in TOGGLES.items():
        on = services.setting(key) == "1"
        b.button(text=f"{label}: {'ON ✅' if on else 'OFF ❌'}", callback_data=f"a:tog:{key}")
    b.button(text="✏️ Texts", callback_data="a:txts")
    b.button(text="💰 Rewards & limits", callback_data="a:nums")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1, 1, 1, 1, 1, 2, 1)
    await show(
        cb,
        "⚙️ <b>Settings</b>\n\nTap a switch to turn it ON/OFF.\n"
        "🤖 <b>Bot OFF</b> shows the maintenance message to all users (admins can still use it).\n"
        "🔔 <b>New-user alerts</b> message every admin when someone starts the bot.",
        b.as_markup(),
    )


@router.callback_query(F.data == "a:set")
async def cb_settings(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_settings(cb)


@router.callback_query(F.data.startswith("a:tog:"))
async def cb_toggle(cb: CallbackQuery):
    key = cb.data.split(":")[2]
    if key in TOGGLES:
        cur = services.setting(key)
        await services.set_setting(key, "0" if cur == "1" else "1")
        services.invalidate()
    await render_settings(cb)


# ---- texts (messages + Join / Claim button labels) ----
@router.callback_query(F.data == "a:txts")
async def cb_texts(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    for key, label in TEXTS.items():
        mark = "📷 " if services.setting(f"media:{key}") else ("💎 " if icons.icon(key) else "")
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
    if key in BTN_KEYS:
        label = services.setting(key)
        iid = icons.icon(key)
        if key == "join_btn":
            label = label.replace("{title}", "Channel name").replace("{n}", "1")
            btn = icons.btn(label[:60], url="https://t.me/telegram", icon_id=iid)
        else:
            btn = icons.btn(label[:60], cb="noop", icon_id=iid)
        return await bot.send_message(chat_id, "👁 <b>Preview</b>", reply_markup=rows_kb([[btn]]))
    tpl = services.setting(key)
    if not tpl.strip():
        return await bot.send_message(chat_id, "ℹ️ <i>Not set — this extra message is currently disabled.</i>")
    u = await services.get_user(tg_user.id)
    extra = {}
    if key == "refer_text":
        extra = {
            "link": f"https://t.me/{(await bot.me()).username}?start=ref_{tg_user.id}",
            "reward": services.fmt(await services.money_setting("refer_reward")),
        }
    text = services.render(tpl, u, **extra)
    try:
        await services.send_rich(bot, chat_id, key, text)
    except TelegramBadRequest:
        await bot.send_message(chat_id, "⚠️ This text has invalid formatting:\n\n" + text, parse_mode=None)


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer("👁 Preview only")


@router.callback_query(F.data.startswith("a:txt:"))
async def cb_txt(cb: CallbackQuery, state: FSMContext, bot: Bot):
    key = cb.data.split(":")[2]
    if key not in LABELS:
        return await cb.answer()
    await state.set_state(EditText.value)
    await state.update_data(key=key)
    rows = [[InlineKeyboardButton(text="♻️ Reset to default", callback_data=f"a:txtdef:{key}")]]
    if services.setting(f"media:{key}"):
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


@router.callback_query(F.data.startswith("a:txtdef:"))
async def cb_txtdef(cb: CallbackQuery, state: FSMContext, bot: Bot):
    key = cb.data.split(":")[2]
    if key not in LABELS:
        return await cb.answer()
    await services.reset_setting(key)
    await services.reset_setting(f"media:{key}")
    if key in BTN_KEYS:
        await icons.clear_icon(key)
    await state.clear()
    await show(cb, f"♻️ <b>{LABELS[key]}</b> was reset to the default.\n\n👇 Preview:", back("a:txts"))
    await send_preview(bot, cb.message.chat.id, key, cb.from_user)


@router.callback_query(F.data.startswith("a:txtnophoto:"))
async def cb_txtnophoto(cb: CallbackQuery, state: FSMContext, bot: Bot):
    key = cb.data.split(":")[2]
    await services.reset_setting(f"media:{key}")
    await state.clear()
    await show(cb, "🗑 Photo removed.\n\n👇 Preview:", back("a:txts"))
    await send_preview(bot, cb.message.chat.id, key, cb.from_user)


@router.message(EditText.value, F.text)
async def msg_txt(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    key = d["key"]
    if key in BTN_KEYS:
        label, iid = icons.split_label(m)
        if not label:
            return await m.answer("❌ Please include some text (not only an emoji).")
        await services.set_setting(key, label[:60])
        if iid:
            await icons.set_icon(key, iid)
        else:
            await icons.clear_icon(key)
    else:
        await services.set_setting(key, m.html_text)  # entities (bold, links, premium emoji…) -> HTML automatically
    await state.clear()
    await m.answer("✅ <b>Saved!</b> This is how it looks:", reply_markup=back("a:txts"))
    await send_preview(bot, m.chat.id, key, m.from_user)


@router.message(EditText.value, F.photo)
async def msg_txt_photo(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    key = d["key"]
    if key not in MEDIA_KEYS:
        return await m.answer("📷 A photo isn't supported here — please send text.")
    await services.set_setting(f"media:{key}", m.photo[-1].file_id)
    if m.caption:
        await services.set_setting(key, m.html_text)  # caption incl. premium emoji
    await state.clear()
    await m.answer("✅ <b>Saved!</b> This is how it looks:", reply_markup=back("a:txts"))
    await send_preview(bot, m.chat.id, key, m.from_user)


# ---- numbers ----
@router.callback_query(F.data == "a:nums")
async def cb_nums(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    for key, (label, unit) in NUMS.items():
        val = await services.get_setting(key)
        shown = f"₹{val}" if unit == "₹" else f"{val} {unit}"
        b.button(text=f"{label}: {shown}", callback_data=f"a:num:{key}")
    b.button(text="⬅️ Back", callback_data="a:set")
    b.adjust(1)
    await show(cb, "💰 <b>Rewards & limits</b>\n\nTap a value to change it.", b.as_markup())


@router.callback_query(F.data.startswith("a:num:"))
async def cb_num(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in NUMS:
        return await cb.answer()
    await state.set_state(EditNum.value)
    await state.update_data(key=key)
    label, unit = NUMS[key]
    b = InlineKeyboardBuilder()
    b.button(text=f"♻️ Default ({services.DEFAULTS[key]} {unit})", callback_data=f"a:numdef:{key}")
    b.button(text="⬅️ Back", callback_data="a:nums")
    b.adjust(1)
    await show(
        cb,
        f"{label}\n\nCurrent: <b>{await services.get_setting(key)}</b> {unit}\n\nSend the new number:",
        b.as_markup(),
    )


@router.callback_query(F.data.startswith("a:numdef:"))
async def cb_numdef(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key in NUMS:
        await services.reset_setting(key)
    await cb_nums(cb, state)


@router.message(EditNum.value, F.text)
async def msg_num(m: Message, state: FSMContext):
    d = await state.get_data()
    key = d["key"]
    p = services.to_paise(m.text)
    if p is None or p < 0:
        return await m.answer("❌ Send a valid number, e.g. <code>5</code> or <code>2.50</code>")
    if key == "bonus_hours":
        if p < 100:
            return await m.answer("❌ Cooldown must be at least 1 hour.")
        value = str(p // 100)
    else:
        value = services.fmt(p) or "0"
    await services.set_setting(key, value)
    await state.clear()
    await m.answer(f"✅ Updated to <b>{value}</b>", reply_markup=back("a:nums"))


# ───────────────────────── users ─────────────────────────
@router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔎 Find user", callback_data="a:ufind")
    b.button(text="📥 Export user IDs", callback_data="a:uexp")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1)
    await show(cb, "👥 <b>Users</b>\n\nFind a user to ban, unban or adjust their balance.", b.as_markup())


@router.callback_query(F.data == "a:uexp")
async def cb_export(cb: CallbackQuery):
    rows = await db.fetch("SELECT id FROM users ORDER BY id")
    data = "\n".join(str(r["id"]) for r in rows).encode()
    await cb.message.answer_document(BufferedInputFile(data, "users.txt"), caption=f"📥 {len(rows)} users")
    await cb.answer()


@router.callback_query(F.data == "a:ufind")
async def cb_ufind(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserLookup.query)
    await show(cb, "🔎 Send a user <b>ID</b> or <b>@username</b>.", back("a:users"))


def user_card(u: dict) -> tuple[str, InlineKeyboardMarkup]:
    name = html.escape(u.get("first_name") or "-")
    uname = f"@{u['username']}" if u.get("username") else "-"
    status = "⛔ banned" if u["banned"] else ("🚫 blocked bot" if u["blocked"] else "✅ active")
    upi = f"<code>{html.escape(u['upi'])}</code>" if u.get("upi") else "-"
    b = InlineKeyboardBuilder()
    if u["banned"]:
        b.button(text="✅ Unban", callback_data=f"a:unban:{u['id']}")
    else:
        b.button(text="⛔ Ban", callback_data=f"a:ban:{u['id']}")
    b.button(text="💰 Add / deduct balance", callback_data=f"a:bal:{u['id']}")
    b.button(text="⬅️ Back", callback_data="a:users")
    b.adjust(2, 1)
    text = (
        f"👤 <b>{name}</b>\n"
        f"🆔 <code>{u['id']}</code>\n"
        f"🔗 {uname}\n"
        f"📌 Status: {status}\n\n"
        f"💰 Balance: <b>₹{services.fmt(u['balance'])}</b>\n"
        f"👥 Referrals: <b>{u['refs']}</b>\n"
        f"🏦 UPI: {upi}"
    )
    return text, b.as_markup()


@router.message(UserLookup.query, F.text)
async def msg_ufind(m: Message, state: FSMContext):
    t = m.text.strip()
    if t.lstrip("-").isdigit():
        u = await db.fetchone("SELECT * FROM users WHERE id=:i", i=int(t))
    else:
        u = await db.fetchone("SELECT * FROM users WHERE LOWER(username)=:u", u=t.lstrip("@").lower())
    if not u:
        return await m.answer("User not found (they must have started the bot at least once).")
    await state.clear()
    text, kb = user_card(u)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^a:(ban|unban):-?\d+$"))
async def cb_ban(cb: CallbackQuery):
    _, action, uid = cb.data.split(":")
    uid = int(uid)
    if uid in config.ADMINS:
        return await cb.answer("You can't ban an admin.", show_alert=True)
    await db.execute("UPDATE users SET banned=:b WHERE id=:i", b=1 if action == "ban" else 0, i=uid)
    text, kb = user_card(await services.get_user(uid))
    await show(cb, text, kb)


@router.callback_query(F.data.regexp(r"^a:bal:-?\d+$"))
async def cb_bal(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[2])
    await state.set_state(AdjustBal.amount)
    await state.update_data(uid=uid)
    await show(
        cb,
        "💰 <b>Adjust balance</b>\n\nSend an amount in ₹.\n"
        "Use <code>50</code> to add or <code>-20</code> to deduct.",
        back("a:users"),
    )


@router.message(AdjustBal.amount, F.text)
async def msg_bal(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    delta = services.to_paise(m.text)
    if not delta:
        return await m.answer("❌ Send a non-zero number like <code>50</code> or <code>-20</code>.")
    ok = await db.execute(
        "UPDATE users SET balance=balance+:d WHERE id=:u AND balance+:d>=0", d=delta, u=d["uid"]
    )
    if not ok:
        return await m.answer("❌ That would make the balance negative (or the user doesn't exist).")
    await state.clear()
    text, kb = user_card(await services.get_user(d["uid"]))
    await m.answer("✅ Balance updated.\n\n" + text, reply_markup=kb)
    try:
        sign = "+" if delta > 0 else "-"
        await bot.send_message(d["uid"], f"💰 Your wallet was updated: <b>{sign}₹{services.fmt(abs(delta))}</b>")
    except TelegramAPIError:
        pass


# ───────────────────────── withdrawals ─────────────────────────
async def render_withdrawals(cb: CallbackQuery):
    rows = await db.fetch("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id LIMIT 10")
    if not rows:
        return await show(cb, "💸 <b>Withdrawals</b>\n\n✨ No pending requests. All clear!", back())
    lines = ["💸 <b>Pending withdrawals</b>\n"]
    b = InlineKeyboardBuilder()
    for w in rows:
        lines.append(
            f"#{w['id']} • <code>{w['user_id']}</code> • <b>₹{services.fmt(w['amount'])}</b>\n"
            f"    🏦 <code>{html.escape(w['upi'] or '-')}</code>"
        )
        b.row(
            InlineKeyboardButton(text=f"✅ #{w['id']} ₹{services.fmt(w['amount'])}", callback_data=f"w:ok:{w['id']}:l"),
            InlineKeyboardButton(text=f"❌ #{w['id']}", callback_data=f"w:no:{w['id']}:l"),
        )
    b.row(InlineKeyboardButton(text="⬅️ Back", callback_data="a:home"))
    await show(cb, "\n".join(lines), b.as_markup())


@router.callback_query(F.data == "a:wd")
async def cb_wd(cb: CallbackQuery):
    await render_withdrawals(cb)


@router.callback_query(F.data.regexp(r"^w:(ok|no):\d+(:l)?$"))
async def cb_wd_action(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    ok, wid, from_list = parts[1] == "ok", int(parts[2]), len(parts) > 3
    changed = await db.execute(
        "UPDATE withdrawals SET status=:s WHERE id=:i AND status='pending'",
        s="paid" if ok else "rejected", i=wid,
    )
    if not changed:
        if from_list:
            return await render_withdrawals(cb)
        return await cb.answer("Already processed.", show_alert=True)
    w = await db.fetchone("SELECT * FROM withdrawals WHERE id=:i", i=wid)
    if not ok:
        await db.execute("UPDATE users SET balance=balance+:a WHERE id=:u", a=w["amount"], u=w["user_id"])
    try:
        if ok:
            await bot.send_message(
                w["user_id"],
                "✅ <b>Withdrawal paid!</b>\n\n"
                f"🆔 Request #{wid}\n💵 Amount: <b>₹{services.fmt(w['amount'])}</b>\n"
                f"🏦 UPI: <code>{html.escape(w['upi'] or '-')}</code>\n\nThank you for using our bot! 💙",
            )
        else:
            await bot.send_message(
                w["user_id"],
                "❌ <b>Withdrawal rejected</b>\n\n"
                f"🆔 Request #{wid}\n💰 <b>₹{services.fmt(w['amount'])}</b> has been refunded to your wallet.\n"
                "Please contact support if you have questions.",
            )
    except TelegramAPIError:
        pass
    if from_list:
        return await render_withdrawals(cb)
    mark = "✅ <b>Marked as paid</b>" if ok else "❌ <b>Rejected & refunded</b>"
    try:
        await cb.message.edit_text(f"{cb.message.html_text}\n\n{mark} by {html.escape(cb.from_user.full_name)}", reply_markup=None)
    except TelegramBadRequest:
        pass
    await cb.answer("Done")


# ───────────────────────── admins (owners only) ─────────────────────────
async def render_admins(cb: CallbackQuery):
    rows = await db.fetch("SELECT user_id FROM admins ORDER BY user_id")
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"🗑 {r['user_id']}", callback_data=f"a:admdel:{r['user_id']}")
    b.button(text="➕ Add admin", callback_data="a:admadd")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(2)
    owners = ", ".join(f"<code>{o}</code>" for o in sorted(config.OWNERS)) or "-"
    await show(cb, f"👮 <b>Admins</b>\n\nOwners (from env): {owners}\nTap an ID to remove that admin.", b.as_markup())


@router.callback_query(F.data == "a:adm", F.from_user.id.in_(config.OWNERS))
async def cb_admins(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_admins(cb)


@router.callback_query(F.data == "a:admadd", F.from_user.id.in_(config.OWNERS))
async def cb_admadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddAdmin.uid)
    await show(cb, "Send the Telegram <b>user ID</b> of the new admin.", back("a:adm"))


@router.message(AddAdmin.uid, F.text, F.from_user.id.in_(config.OWNERS))
async def msg_admadd(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit():
        return await m.answer("Send a numeric user ID.")
    await db.execute("INSERT INTO admins(user_id) VALUES(:u) ON CONFLICT DO NOTHING", u=int(t))
    await services.load_admins()
    await state.clear()
    await m.answer(f"✅ <code>{t}</code> is now an admin.", reply_markup=back("a:adm"))


@router.callback_query(F.data.startswith("a:admdel:"), F.from_user.id.in_(config.OWNERS))
async def cb_admdel(cb: CallbackQuery):
    await db.execute("DELETE FROM admins WHERE user_id=:u", u=int(cb.data.split(":")[2]))
    await services.load_admins()
    await render_admins(cb)


# ───────────────────────── broadcast ─────────────────────────
BC: dict = {"task": None, "cancel": False}


def bc_running() -> bool:
    t = BC["task"]
    return t is not None and not t.done()


def build_markup(buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, url=u)] for t, u in buttons])


@router.callback_query(F.data == "a:bc")
async def cb_bc(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if bc_running():
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


@router.callback_query(F.data == "a:bcstop")
async def cb_bcstop(cb: CallbackQuery):
    BC["cancel"] = True
    await cb.answer("Stopping…", show_alert=True)


@router.message(Broadcast.msg)
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
    d = await state.get_data()
    try:
        await bot.copy_message(chat_id, d["src_chat"], d["src_msg"], reply_markup=build_markup(d.get("buttons")))
    except TelegramAPIError as e:
        await bot.send_message(chat_id, f"❌ Preview failed: {html.escape(str(e))}")
        await state.clear()
        return
    n = await db.scalar("SELECT COUNT(*) FROM users WHERE blocked=0 AND banned=0")
    b = InlineKeyboardBuilder()
    b.button(text=f"🚀 Send to {n} users", callback_data="a:bcgo")
    b.button(text="❌ Cancel", callback_data="a:home")
    b.adjust(1)
    await state.set_state(Broadcast.confirm)
    await bot.send_message(chat_id, "👆 <b>Preview</b> — everything look good?", reply_markup=b.as_markup())


@router.callback_query(Broadcast.buttons, F.data == "a:bcskip")
async def cb_bcskip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    await bc_preview(bot, cb.message.chat.id, state)


@router.message(Broadcast.buttons, F.text)
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


@router.callback_query(Broadcast.confirm, F.data == "a:bcgo")
async def cb_bcgo(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if bc_running():
        return await cb.answer("A broadcast is already running.", show_alert=True)
    d = await state.get_data()
    await state.clear()
    status = await cb.message.edit_text("⏳ Starting broadcast…")
    BC["task"] = asyncio.create_task(
        run_broadcast(bot, status, d["src_chat"], d["src_msg"], build_markup(d.get("buttons")))
    )
    await cb.answer()


async def run_broadcast(bot: Bot, status: Message, src_chat: int, src_msg: int, markup):
    ids = [r["id"] for r in await db.fetch("SELECT id FROM users WHERE blocked=0 AND banned=0")]
    stats = {"ok": 0, "blocked": 0, "failed": 0}
    BC["cancel"] = False

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
                await db.execute("UPDATE users SET blocked=1 WHERE id=:i", i=uid)
                stats["blocked"] += 1
                return
            except TelegramAPIError:
                stats["failed"] += 1
                return
        stats["failed"] += 1

    total = len(ids)
    done_batches = 0
    for i in range(0, total, 25):  # ~25 msgs / second, under Telegram's 30/s limit
        if BC["cancel"]:
            break
        await asyncio.gather(*(send(uid) for uid in ids[i:i + 25]))
        done_batches += 1
        await asyncio.sleep(1)
        if done_batches % 8 == 0:
            try:
                await status.edit_text(f"⏳ Broadcasting… {min(i + 25, total)}/{total}")
            except TelegramAPIError:
                pass

    title = "🛑 <b>Broadcast stopped</b>" if BC["cancel"] else "✅ <b>Broadcast finished</b>"
    text = (
        f"{title}\n\n📬 Delivered: <b>{stats['ok']}</b>\n🚫 Blocked bot: <b>{stats['blocked']}</b>\n"
        f"⚠️ Failed: <b>{stats['failed']}</b>\n👥 Targeted: <b>{total}</b>"
    )
    try:
        await status.edit_text(text, reply_markup=back())
    except TelegramAPIError:
        await bot.send_message(status.chat.id, text)
