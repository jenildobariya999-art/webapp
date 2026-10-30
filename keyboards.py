from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import icons
import services

MENU_KEYS = ("btn_balance", "btn_refer", "btn_bonus", "btn_withdraw", "btn_upi")


def _key(key: str) -> KeyboardButton:
    iid, st = icons.icon(key), icons.style_of(key)
    extra = {}
    if iid:
        extra["icon_custom_emoji_id"] = iid
    if st:
        extra["style"] = st
    return KeyboardButton(text=services.setting(key), **extra)


def main_kb() -> ReplyKeyboardMarkup:
    """The 5-button user menu. Labels and premium icons are editable in the admin panel."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [_key("btn_balance"), _key("btn_refer")],
            [_key("btn_bonus"), _key("btn_withdraw")],
            [_key("btn_upi")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Choose an option 👇",
    )


def wd_kb(wid: int, suffix: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Mark paid", callback_data=f"w:ok:{wid}{suffix}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"w:no:{wid}{suffix}"),
    ]])
