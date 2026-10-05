"""Private owner buttons; nonce callbacks fit Telegram's 64-byte limit."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def owner_menu(settings):
    rows = [
        [
            InlineKeyboardButton(text="Status", callback_data="view:dashboard"),
            InlineKeyboardButton(text="Positions", callback_data="view:positions"),
        ],
        [
            InlineKeyboardButton(text="Pause entries", callback_data="ctl:pause"),
            InlineKeyboardButton(text="Resume…", callback_data="ctl:resume"),
        ],
        [
            InlineKeyboardButton(text="News", callback_data="view:news"),
            InlineKeyboardButton(text="AI proposals", callback_data="view:suggestions"),
        ],
        [
            InlineKeyboardButton(text="Kill entries", callback_data="ctl:kill"),
            InlineKeyboardButton(text="Close captured owned…", callback_data="ctl:close_all"),
        ],
    ]
    if settings.telegram_miniapp_url:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text="Open owner dashboard", web_app=WebAppInfo(url=settings.telegram_miniapp_url)
                )
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation(token):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Confirm once", callback_data="confirm:" + token),
                InlineKeyboardButton(text="Cancel", callback_data="cancel:" + token),
            ]
        ]
    )
