import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatJoinRequest, ChatMemberUpdated, Message

import db
import services
from keyboards import main_kb

log = logging.getLogger(__name__)
router = Router(name="user")


async def welcome_text(uid: int) -> str:
    u = await services.get_user(uid)
    return services.render(await services.get_setting("welcome"), u)


@router.message(StateFilter("*"), CommandStart())
async def cmd_start(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await services.credit_referral(bot, msg.from_user.id)
    await services.send_rich(bot, msg.chat.id, "welcome", await welcome_text(msg.from_user.id), main_kb())


@router.callback_query(F.data == "check")
async def check_cb(cb: CallbackQuery, bot: Bot):
    missing = await services.missing_channels(bot, cb.from_user.id, use_cache=False)
    if missing:
        names = ", ".join(c["title"] or "channel" for c in missing)
        return await cb.answer(f"❌ You still need to join: {names}"[:190], show_alert=True)
    try:
        await cb.message.delete()
    except TelegramBadRequest:
        pass
    await db.execute("UPDATE users SET verified=1 WHERE id=:u", u=cb.from_user.id)
    await services.credit_referral(bot, cb.from_user.id)
    await services.send_rich(bot, cb.from_user.id, "welcome", await welcome_text(cb.from_user.id), main_kb())
    await cb.answer("✅ Verified!")


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    """A user requested to join one of our channels -> remember it (counts as 'joined')."""
    ch = await db.fetchone("SELECT chat_id FROM channels WHERE chat_id=:c", c=req.chat.id)
    if not ch:
        return
    await services.upsert_user(req.from_user)
    await db.execute(
        "INSERT INTO join_requests(user_id, chat_id) VALUES(:u, :c) ON CONFLICT DO NOTHING",
        u=req.from_user.id, c=req.chat.id,
    )
    services.invalidate(req.from_user.id)
    if await services.get_setting("auto_approve") == "1":
        try:
            await req.approve()
        except TelegramAPIError as e:
            log.warning("auto-approve failed: %s", e)


@router.chat_member()
async def on_chat_member(ev: ChatMemberUpdated):
    """Clean up stale join requests when the user actually joins / leaves / is removed."""
    status = ev.new_chat_member.status
    if status in (
        ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR,
        ChatMemberStatus.LEFT, ChatMemberStatus.KICKED,
    ):
        uid = ev.new_chat_member.user.id
        await db.execute("DELETE FROM join_requests WHERE user_id=:u AND chat_id=:c", u=uid, c=ev.chat.id)
        services.invalidate(uid)
