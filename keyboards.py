from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

import icons
import services


def main_kb():
    """The user menu. Buttons, their order and the layout (how many per line) come from the admin panel."""
    rows = []
    for line in services.layout_rows(services.MENU):
        btns = []
        for b in line:
            extra = {"style": b["style"]} if b["style"] in icons.STYLES else {}
            btns.append(KeyboardButton(text=b["label"], **extra))
        rows.append(btns)
    if not rows:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Choose an option 👇")
