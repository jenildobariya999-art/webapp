"""User menu: Balance, Refer & Earn, Bonus, Withdrawal, Link UPI."""
import html
import logging
import re
import time
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import services
from keyboards import main_kb, wd_kb

log = logging.getLogger(__name__)
router = Router(name="menu")
ANY = StateFilter("*")  # menu buttons work even if the user is in the middle of a flow

class MenuBtn(BaseFilter):
    """Matches a menu button by its (admin-editable) label — the old default label also keeps working."""

    def __init__(self, key: str):
        self.key = key

    async def __call__(self, m: Message) -> bool:
        if services.setting("wallet_on") != "1":
            return False  # wallet menu is switched off
        t = (m.text or "").strip()
        return bool(t) and t in {services.setting(self.key), services.DEFAULTS[self.key]}


class CustomBtn(BaseFilter):
    """Matches the label of a custom menu button and hands that button to the handler."""

    async def __call__(self, m: Message):
        t = (m.text or "").strip()
        for b in services.MENU:
            if b["label"] == t:
                return {"mbtn": b}
        return False


UPI_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z0-9]{2,64}$")


class Withdraw(StatesGroup):
    amount = State()


class LinkUpi(StatesGroup):
    upi = State()


def cancel_kb(extra: list | None = None) -> InlineKeyboardMarkup:
    rows = [extra] if extra else []
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(ANY, Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Cancelled.", reply_markup=main_kb())


@router.callback_query(F.data == "flow:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("✖️ Cancelled.")
    except Exception:
        pass
    await cb.answer()


# ───────────── custom menu buttons (reply text is edited in the admin panel) ─────────────
@router.message(ANY, CustomBtn())
async def custom_button(m: Message, state: FSMContext, bot: Bot, mbtn: dict):
    await state.clear()
    u = await services.get_user(m.from_user.id)
    text = services.render(mbtn["reply"] or "", u)
    await services.send_photo_text(bot, m.chat.id, mbtn["photo"], text)


# ───────────── 💰 Balance ─────────────
@router.message(ANY, MenuBtn("btn_balance"))
async def balance(m: Message, state: FSMContext):
    await state.clear()
    u = await services.get_user(m.from_user.id)
    minw = await services.money_setting("min_withdraw")
    upi = f"<code>{html.escape(u['upi'])}</code>" if u["upi"] else "<i>Not linked yet</i>"
    await m.answer(
        "💰 <b>Your Wallet</b>\n\n"
        f"💵 Balance: <b>₹{services.fmt(u['balance'])}</b>\n"
        f"👥 Referrals: <b>{u['refs']}</b>\n"
        f"🏦 UPI: {upi}\n\n"
        f"💸 Minimum withdrawal: <b>₹{services.fmt(minw)}</b>"
    )


# ───────────── 👥 Refer & Earn ─────────────
@router.message(ANY, MenuBtn("btn_refer"))
async def refer(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    u = await services.get_user(m.from_user.id)
    reward = await services.money_setting("refer_reward")
    link = f"https://t.me/{(await bot.me()).username}?start=ref_{m.from_user.id}"
    text = services.render(
        await services.get_setting("refer_text"), u, link=link, reward=services.fmt(reward)
    )
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('🎁 Join this bot and earn real money! 💰')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 Share your link", url=share)]])
    await m.answer(text, reply_markup=kb)


# ───────────── 🎁 Bonus ─────────────
@router.message(ANY, MenuBtn("btn_bonus"))
async def bonus(m: Message, state: FSMContext):
    await state.clear()
    amount = await services.money_setting("bonus_amount")
    hours = await services.int_setting("bonus_hours")
    if amount <= 0:
        return await m.answer("🎁 <b>Bonus</b>\n\nThe bonus is not available right now. Check back soon!")
    now = int(time.time())
    cooldown = max(hours, 1) * 3600
    ok = await services.db.execute(
        "UPDATE users SET balance=balance+:a, last_bonus=:n WHERE id=:u AND last_bonus<=:t",
        a=amount, n=now, u=m.from_user.id, t=now - cooldown,
    )
    u = await services.get_user(m.from_user.id)
    if ok:
        return await m.answer(
            "🎁 <b>Bonus claimed!</b>\n\n"
            f"✨ <b>+₹{services.fmt(amount)}</b> added to your wallet\n"
            f"💰 New balance: <b>₹{services.fmt(u['balance'])}</b>\n\n"
            f"⏳ Come back in <b>{hours}h</b> for the next one!"
        )
    left = max(u["last_bonus"] + cooldown - now, 0)
    await m.answer(
        "⏳ <b>Bonus already claimed</b>\n\n"
        f"Your next bonus is ready in <b>{left // 3600}h {(left % 3600) // 60}m</b>.\n"
        "Meanwhile, invite friends to earn more! 👥"
    )


# ───────────── 🏦 Link UPI ─────────────
@router.message(ANY, MenuBtn("btn_upi"))
async def upi_start(m: Message, state: FSMContext):
    u = await services.get_user(m.from_user.id)
    await state.set_state(LinkUpi.upi)
    cur = f"\n\n✅ Current UPI: <code>{html.escape(u['upi'])}</code>" if u["upi"] else ""
    await m.answer(
        "🏦 <b>Link your UPI</b>\n\n"
        "Send your UPI ID, for example: <code>name@upi</code>\n"
        f"⚠️ Double-check it — withdrawals are paid to this ID.{cur}",
        reply_markup=cancel_kb(),
    )


@router.message(LinkUpi.upi, F.text)
async def upi_save(m: Message, state: FSMContext):
    v = m.text.strip()
    if not UPI_RE.match(v):
        return await m.answer("❌ <b>Invalid UPI ID</b>\n\nPlease send it like <code>name@upi</code>, or /cancel.")
    await services.db.execute("UPDATE users SET upi=:v WHERE id=:u", v=v, u=m.from_user.id)
    await state.clear()
    await m.answer(f"✅ <b>UPI linked successfully</b>\n\n🏦 <code>{html.escape(v)}</code>", reply_markup=main_kb())


# ───────────── 💸 Withdrawal ─────────────
@router.message(ANY, MenuBtn("btn_withdraw"))
async def wd_start(m: Message, state: FSMContext):
    await state.clear()
    if await services.get_setting("withdraw_on") != "1":
        return await m.answer("🔧 <b>Withdrawals are paused</b>\n\nThey'll be back soon — please check again later.")
    u = await services.get_user(m.from_user.id)
    if not u["upi"]:
        return await m.answer("⚠️ <b>UPI not linked</b>\n\nTap <b>🏦 Link UPI</b> first, then come back to withdraw.")
    minw = await services.money_setting("min_withdraw")
    if u["balance"] < max(minw, 1):
        return await m.answer(
            "❌ <b>Not enough balance</b>\n\n"
            f"💵 Your balance: <b>₹{services.fmt(u['balance'])}</b>\n"
            f"💸 Minimum withdrawal: <b>₹{services.fmt(minw)}</b>\n\n"
            "Invite friends & claim your bonus to reach it! 🚀"
        )
    await state.set_state(Withdraw.amount)
    all_btn = [InlineKeyboardButton(text=f"💸 Withdraw all (₹{services.fmt(u['balance'])})", callback_data="wd:all")]
    await m.answer(
        "💸 <b>Withdrawal</b>\n\n"
        f"💵 Balance: <b>₹{services.fmt(u['balance'])}</b>\n"
        f"🏦 UPI: <code>{html.escape(u['upi'])}</code>\n"
        f"📉 Minimum: <b>₹{services.fmt(minw)}</b>\n\n"
        "✍️ Send the amount you want to withdraw:",
        reply_markup=cancel_kb(all_btn),
    )


async def submit_withdrawal(bot: Bot, uid: int, amount: int) -> str:
    u = await services.get_user(uid)
    minw = await services.money_setting("min_withdraw")
    if not u["upi"]:
        return "⚠️ Please link your UPI first."
    if amount < minw or amount <= 0:
        return f"❌ Minimum withdrawal is <b>₹{services.fmt(minw)}</b>."
    ok = await services.db.execute(
        "UPDATE users SET balance=balance-:a WHERE id=:u AND balance>=:a", a=amount, u=uid
    )
    if not ok:
        return "❌ <b>Insufficient balance.</b>"
    wid = await services.db.insert_returning(
        "INSERT INTO withdrawals(user_id, amount, upi) VALUES(:u, :a, :upi)", u=uid, a=amount, upi=u["upi"]
    )
    uname = f" @{u['username']}" if u["username"] else ""
    text = (
        "💸 <b>New withdrawal request</b>\n\n"
        f"🆔 Request: <b>#{wid}</b>\n"
        f"👤 {html.escape(u['first_name'] or '-')}{uname} (<code>{uid}</code>)\n"
        f"💵 Amount: <b>₹{services.fmt(amount)}</b>\n"
        f"🏦 UPI: <code>{html.escape(u['upi'])}</code>"
    )
    for aid in list(config.ADMINS):
        try:
            await bot.send_message(aid, text, reply_markup=wd_kb(wid))
        except Exception:
            pass
    return (
        "✅ <b>Withdrawal requested!</b>\n\n"
        f"🆔 Request: <b>#{wid}</b>\n"
        f"💵 Amount: <b>₹{services.fmt(amount)}</b>\n"
        f"🏦 UPI: <code>{html.escape(u['upi'])}</code>\n\n"
        "⏳ We'll notify you as soon as it's processed."
    )


@router.message(Withdraw.amount, F.text)
async def wd_amount(m: Message, state: FSMContext, bot: Bot):
    amt = services.to_paise(m.text)
    if amt is None or amt <= 0:
        return await m.answer("❌ Please send a valid amount, e.g. <code>100</code> — or /cancel.")
    await state.clear()
    await m.answer(await submit_withdrawal(bot, m.from_user.id, amt), reply_markup=main_kb())


@router.callback_query(Withdraw.amount, F.data == "wd:all")
async def wd_all(cb: CallbackQuery, state: FSMContext, bot: Bot):
    u = await services.get_user(cb.from_user.id)
    await state.clear()
    text = await submit_withdrawal(bot, cb.from_user.id, u["balance"])
    try:
        await cb.message.edit_text(text)
    except Exception:
        await cb.message.answer(text)
    await cb.answer()
