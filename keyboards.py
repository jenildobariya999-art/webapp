from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

BTN_BALANCE = "💰 Balance"
BTN_REFER = "👥 Refer & Earn"
BTN_BONUS = "🎁 Bonus"
BTN_WITHDRAW = "💸 Withdrawal"
BTN_UPI = "🏦 Link UPI"

MENU_BUTTONS = {BTN_BALANCE, BTN_REFER, BTN_BONUS, BTN_WITHDRAW, BTN_UPI}


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_REFER)],
            [KeyboardButton(text=BTN_BONUS), KeyboardButton(text=BTN_WITHDRAW)],
            [KeyboardButton(text=BTN_UPI)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Choose an option 👇",
    )


def wd_kb(wid: int, suffix: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Mark paid", callback_data=f"w:ok:{wid}{suffix}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"w:no:{wid}{suffix}"),
    ]])
