from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import CallbackAction


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_ADMIN_SIGNALISTS), KeyboardButton(text=texts.MENU_ADMIN_DESTINATIONS)],
                  [KeyboardButton(text=texts.MENU_MAIN)],
                  [KeyboardButton(text=texts.MENU_ADMIN_STATUS), KeyboardButton(text=texts.MENU_HELP)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def signalist_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_MAIN)],
                  [KeyboardButton(text=texts.MENU_SIGNALIST_STATUS), KeyboardButton(text=texts.MENU_HELP)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_menu() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def status_navigation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BUTTON_REFRESH, callback_data=CallbackAction.REFRESH_STATUS.value),
        InlineKeyboardButton(text=texts.BUTTON_BACK, callback_data=CallbackAction.MENU.value),
    ]])
