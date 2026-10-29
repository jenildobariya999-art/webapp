"""Admin panel (/admin): stats, channels, broadcast, users, settings, admins."""
import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command
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
import services

log = logging.getLogger(__name__)
router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and u.id in config.ADMINS)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AddChannel(StatesGroup):
    chat = State()
    mode = State()


class Broadcast(StatesGroup):
    msg = State()
    buttons = State()
    confirm = State()


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
    await cb.answer()


def back(to: str = "a:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data=to)]])


def menu_kb(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Stats", callback_data="a:stats")
    b.button(text="📢 Channels", callback_data="a:ch")
    b.button(text="📨 Broadcast", callback_data="a:bc")
    b.button(text="👥 Users", callback_data="a:users")
    b.button(text="⚙️ Settings", callback_data="a:set")
    if user_id in config.OWNERS:
        b.button(text="👮 Admins", callback_data="a:adm")
    b.adjust(2)
    return b.as_markup()


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=menu_kb(m.from_user.id))


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.", reply_markup=menu_kb(m.from_user.id))


@router.callback_query(F.data == "a:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "🛠 <b>Admin panel</b>", menu_kb(cb.from_user.id))


# ───────────────────────── stats ─────────────────────────
@router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    total = await db.scalar("SELECT COUNT(*) FROM users")
    blocked = await db.scalar("SELECT COUNT(*) FROM users WHERE blocked=1")
    banned = await db.scalar("SELECT COUNT(*) FROM users WHERE banned=1")
    chans = await db.scalar("SELECT COUNT(*) FROM channels")
    reqs = await db.scalar("SELECT COUNT(*) FROM join_requests")
    await show(
        cb,
        "📊 <b>Stats</b>\n\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"✅ Reachable: <b>{total - blocked - banned}</b>\n"
        f"🚫 Blocked the bot: <b>{blocked}</b>\n"
        f"⛔ Banned: <b>{banned}</b>\n"
        f"📢 Force-join channels: <b>{chans}</b>\n"
        f"📨 Pending join requests stored: <b>{reqs}</b>\n"
        f"👮 Admins: <b>{len(config.ADMINS)}</b>",
        back(),
    )


# ───────────────────────── channels ─────────────────────────
async def make_link(bot: Bot, chat, request_mode: int) -> str:
    if request_mode:
        inv = await bot.create_chat_invite_link(chat.id, name="forcejoin-request", creates_join_request=True)
        return inv.invite_link
    if chat.username:
        return f"https://t.me/{chat.username}"
    inv = await bot.create_chat_invite_link(chat.id, name="forcejoin")
    return inv.invite_link


async def render_channels(cb: CallbackQuery):
    rows = await db.fetch("SELECT * FROM channels ORDER BY added, chat_id")
    b = InlineKeyboardBuilder()
    for ch in rows:
        tag = "📨" if ch["request_mode"] else "📢"
        b.row(
            InlineKeyboardButton(text=f"{tag} {ch['title']}"[:30], callback_data=f"a:chinfo:{ch['chat_id']}"),
            InlineKeyboardButton(text="🔄 Mode", callback_data=f"a:chmode:{ch['chat_id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"a:chdel:{ch['chat_id']}"),
        )
    b.row(InlineKeyboardButton(text="➕ Add channel", callback_data="a:chadd"))
    b.row(InlineKeyboardButton(text="⬅️ Back", callback_data="a:home"))
    await show(
        cb,
        "📢 <b>Force-join channels</b>\n\n"
        "📢 = normal join   📨 = join-request (a pending request passes)\n"
        "Tap the name for its link, 🔄 to switch mode, 🗑 to delete.\n\n"
        f"Total: <b>{len(rows)}</b>",
        b.as_markup(),
    )


@router.callback_query(F.data == "a:ch")
async def cb_channels(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_channels(cb)


@router.callback_query(F.data.startswith("a:chinfo:"))
async def cb_chinfo(cb: CallbackQuery):
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=int(cb.data.split(":")[2]))
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    mode = "Join request" if ch["request_mode"] else "Normal"
    await cb.answer(f"{ch['title']}\nMode: {mode}\n{ch['link']}"[:190], show_alert=True)


@router.callback_query(F.data.startswith("a:chdel:"))
async def cb_chdel(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    await db.execute("DELETE FROM channels WHERE chat_id=:c", c=cid)
    await db.execute("DELETE FROM join_requests WHERE chat_id=:c", c=cid)
    services.invalidate()
    await render_channels(cb)


@router.callback_query(F.data.startswith("a:chmode:"))
async def cb_chmode(cb: CallbackQuery, bot: Bot):
    cid = int(cb.data.split(":")[2])
    ch = await db.fetchone("SELECT * FROM channels WHERE chat_id=:c", c=cid)
    if not ch:
        return await cb.answer("Not found", show_alert=True)
    new = 0 if ch["request_mode"] else 1
    try:
        chat = await bot.get_chat(cid)
        link = await make_link(bot, chat, new)
    except TelegramAPIError as e:
        return await cb.answer(f"Failed: {e}"[:190], show_alert=True)
    await db.execute("UPDATE channels SET request_mode=:m, link=:l WHERE chat_id=:c", m=new, l=link, c=cid)
    if not new:
        await db.execute("DELETE FROM join_requests WHERE chat_id=:c", c=cid)
    services.invalidate()
    await render_channels(cb)


@router.callback_query(F.data == "a:chadd")
async def cb_chadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.chat)
    await show(
        cb,
        "➕ <b>Add channel</b>\n\nSend the channel <b>@username</b> / numeric <b>ID</b> "
        "(e.g. <code>-1001234567890</code>) — or simply <b>forward any post</b> from it.\n\n"
        "The bot must already be an <b>admin</b> there (with “Invite users via link” for request channels).",
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
    await state.update_data(chat_id=chat.id, title=chat.title or str(chat.id))
    await state.set_state(AddChannel.mode)
    b = InlineKeyboardBuilder()
    b.button(text="📢 Normal join", callback_data="a:mode:0")
    b.button(text="📨 Join request", callback_data="a:mode:1")
    b.button(text="❌ Cancel", callback_data="a:ch")
    b.adjust(2, 1)
    await m.answer(f"Channel: <b>{html.escape(chat.title or '')}</b>\nChoose how users must join:", reply_markup=b.as_markup())


@router.callback_query(AddChannel.mode, F.data.startswith("a:mode:"))
async def cb_chmode_pick(cb: CallbackQuery, state: FSMContext, bot: Bot):
    mode = int(cb.data.split(":")[2])
    d = await state.get_data()
    try:
        chat = await bot.get_chat(d["chat_id"])
        link = await make_link(bot, chat, mode)
    except TelegramAPIError as e:
        await state.clear()
        return await show(
            cb, f"❌ Couldn't create the invite link: {html.escape(str(e))}\n"
                "Give the bot the “Invite users via link” permission.", back("a:ch"),
        )
    await db.execute(
        "INSERT INTO channels(chat_id, title, link, request_mode) VALUES(:c, :t, :l, :m) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
        "request_mode=excluded.request_mode",
        c=d["chat_id"], t=d["title"], l=link, m=mode,
    )
    services.invalidate()
    await state.clear()
    await show(cb, f"✅ Added <b>{html.escape(d['title'])}</b>", back("a:ch"))


# ───────────────────────── settings ─────────────────────────
EDITABLE = {"welcome": "Welcome message", "force_text": "Force-join message"}


async def render_settings(cb: CallbackQuery):
    fj = await services.get_setting("force_join") == "1"
    aa = await services.get_setting("auto_approve") == "1"
    b = InlineKeyboardBuilder()
    b.button(text=f"Force join: {'ON ✅' if fj else 'OFF ❌'}", callback_data="a:tog:force_join")
    b.button(text=f"Auto-approve join requests: {'ON ✅' if aa else 'OFF ❌'}", callback_data="a:tog:auto_approve")
    b.button(text="✏️ Welcome message", callback_data="a:txt:welcome")
    b.button(text="✏️ Force-join message", callback_data="a:txt:force_text")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1)
    await show(cb, "⚙️ <b>Settings</b>", b.as_markup())


@router.callback_query(F.data == "a:set")
async def cb_settings(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_settings(cb)


@router.callback_query(F.data.startswith("a:tog:"))
async def cb_toggle(cb: CallbackQuery):
    key = cb.data.split(":")[2]
    if key in ("force_join", "auto_approve"):
        cur = await services.get_setting(key)
        await services.set_setting(key, "0" if cur == "1" else "1")
        services.invalidate()
    await render_settings(cb)


@router.callback_query(F.data.startswith("a:txt:"))
async def cb_txt(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in EDITABLE:
        return await cb.answer()
    await state.set_state(EditText.value)
    await state.update_data(key=key)
    cur = html.escape(await services.get_setting(key))
    await show(
        cb,
        f"✏️ <b>{EDITABLE[key]}</b>\n\nCurrent:\n<code>{cur}</code>\n\nSend the new text (formatting is kept).",
        back("a:set"),
    )


@router.message(EditText.value, F.text)
async def msg_txt(m: Message, state: FSMContext):
    d = await state.get_data()
    await services.set_setting(d["key"], m.html_text)
    await state.clear()
    await m.answer("✅ Saved.", reply_markup=back("a:set"))


# ───────────────────────── users ─────────────────────────
@router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔎 Find / ban / unban", callback_data="a:ufind")
    b.button(text="📥 Export user IDs", callback_data="a:uexp")
    b.button(text="⬅️ Back", callback_data="a:home")
    b.adjust(1)
    await show(cb, "👥 <b>Users</b>", b.as_markup())


@router.callback_query(F.data == "a:uexp")
async def cb_export(cb: CallbackQuery):
    rows = await db.fetch("SELECT id FROM users ORDER BY id")
    data = "\n".join(str(r["id"]) for r in rows).encode()
    await cb.message.answer_document(BufferedInputFile(data, "users.txt"), caption=f"{len(rows)} users")
    await cb.answer()


@router.callback_query(F.data == "a:ufind")
async def cb_ufind(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserLookup.query)
    await show(cb, "Send a user <b>ID</b> or <b>@username</b>.", back("a:users"))


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
    b.adjust(1)
    return f"👤 <b>{name}</b>\nID: <code>{u['id']}</code>\nUsername: {uname}\nStatus: {status}", b.as_markup()


@router.message(UserLookup.query, F.text)
async def msg_ufind(m: Message, state: FSMContext):
    t = m.text.strip()
    if t.lstrip("-").isdigit():
        u = await db.fetchone("SELECT * FROM users WHERE id=:i", i=int(t))
    else:
        u = await db.fetchone("SELECT * FROM users WHERE LOWER(username)=:u", u=t.lstrip("@").lower())
    if not u:
        return await m.answer("User not found in the database (they must have started the bot).")
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
    u = await db.fetchone("SELECT * FROM users WHERE id=:i", i=uid)
    text, kb = user_card(u)
    await show(cb, text, kb)


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
    await show(cb, f"👮 <b>Admins</b>\n\nOwners (from env): {owners}\nTap an ID to remove it.", b.as_markup())


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
    await show(cb, "📨 <b>Broadcast</b>\n\nSend the message you want to broadcast (text, photo, video, file… anything).", back())


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
        "Add URL buttons? Send one per line:\n<code>Button text | https://example.com</code>\n\nOr skip.",
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
    await bot.send_message(chat_id, "👆 Preview. Confirm broadcast?", reply_markup=b.as_markup())


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

    title = "🛑 Broadcast stopped" if BC["cancel"] else "✅ Broadcast finished"
    text = (
        f"{title}\n\nDelivered: <b>{stats['ok']}</b>\nBlocked bot: <b>{stats['blocked']}</b>\n"
        f"Failed: <b>{stats['failed']}</b>\nTotal targeted: <b>{total}</b>"
    )
    try:
        await status.edit_text(text, reply_markup=back())
    except TelegramAPIError:
        await bot.send_message(status.chat.id, text)
