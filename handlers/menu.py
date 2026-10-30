"""User menu: every button sends the reply text that the admin set for it (Settings → Menu buttons)."""
import logging

from aiogram import Bot, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import services
from keyboards import main_kb

log = logging.getLogger(__name__)
router = Router(name="menu")
ANY = StateFilter("*")


class CustomBtn(BaseFilter):
    """Matches the label of a menu button and hands that button to the handler."""

    async def __call__(self, m: Message):
        t = (m.text or "").strip()
        for b in services.MENU:
            if b["label"] == t:
                return {"mbtn": b}
        return False


@router.message(ANY, Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Cancelled.", reply_markup=main_kb())


@router.message(ANY, CustomBtn())
async def custom_button(m: Message, state: FSMContext, bot: Bot, mbtn: dict):
    await state.clear()
    u = await services.get_user(m.from_user.id)
    text = services.render(mbtn["reply"] or "", u)
    await services.send_photo_text(bot, m.chat.id, mbtn["photo"], text)
