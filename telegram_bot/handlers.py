"""Actual aiogram 3 handlers sharing the guarded owner service, no broker bypass."""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.owner_identity import OwnerInterfaceError
from telegram_bot.formatters import HELP, action_text, render
from telegram_bot.keyboards import confirmation, owner_menu

READS = {
    "status": "dashboard",
    "dashboard": "dashboard",
    "positions": "positions",
    "trades": "trades",
    "signals": "signals",
    "news": "news",
    "suggestions": "suggestions",
    "settings": "settings",
    "logs": "logs",
    "ai": "ai_journal",
    "ai_fallback_status": "ai_fallback",
    "limits": "limits",
}
ALERT_LEVELS = {"critical": "CRITICAL", "error": "ERROR", "warning": "WARNING", "info": "INFO"}


def _request_id(event):
    if isinstance(event, CallbackQuery):
        identity = "callback:" + event.id
    else:
        identity = f"message:{event.chat.id}:{event.message_id}:{event.date.isoformat()}"
    return str(uuid5(NAMESPACE_URL, "reflex-owner-v1:" + identity))


def _ids(args, count):
    parts = (args or "").split()
    if len(parts) != count or any(
        not re.fullmatch(r"[1-9][0-9]{0,18}", p) or int(p) > 2**63 - 1 for p in parts
    ):
        raise OwnerInterfaceError("invalid_command_arguments", 422)
    return tuple(map(int, parts))


async def _reply(message, text, keyboard=None):
    # Never log/repr an outgoing reply, callback token, SDK exception or update.
    try:
        return await message.answer(text[:3800], parse_mode=None, reply_markup=keyboard)
    except TelegramAPIError:
        return None  # Delivery not confirmed; no claim of a successful owner notification.


async def _action_reply(message, result):
    keyboard = (
        confirmation(result["confirmation_token"]) if result["status"] == "confirmation_required" else None
    )
    await _reply(message, action_text(result), keyboard)


def build_router(services):
    router = Router(name="private_owner")

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message):
        await _reply(message, HELP, owner_menu(services.settings))

    @router.message(Command(*READS))
    async def read_command(message: Message, command: CommandObject, owner_identity):
        try:
            result = await services.read(owner_identity, READS[command.command.lower()], limit=10)
            await _reply(
                message, render(READS[command.command.lower()], result), owner_menu(services.settings)
            )
        except OwnerInterfaceError as error:
            await _reply(message, "Owner request denied: " + error.code)

    @router.message(Command("alerts"))
    async def alerts_command(message: Message, command: CommandObject, owner_identity):
        try:
            argument = (command.args or "").strip().lower()
            if argument and argument not in ALERT_LEVELS:
                raise OwnerInterfaceError("invalid_command_arguments", 422)
            result = await services.read(owner_identity, "alerts", limit=10, level=ALERT_LEVELS.get(argument))
            await _reply(message, render("alerts", result), owner_menu(services.settings))
        except OwnerInterfaceError as error:
            await _reply(message, "Owner request denied: " + error.code)

    @router.message(
        Command(
            "pause",
            "resume",
            "kill",
            "close",
            "close_all",
            "approve",
            "reject",
            "ai_reset",
            "ai_fallback_block",
            "ai_fallback_technical",
            "ack_all",
            "ack",
        )
    )
    async def action_command(message: Message, command: CommandObject, owner_identity):
        try:
            name = command.command.lower()
            action = {
                "close": "close_position",
                "approve": "approve_suggestion",
                "reject": "reject_suggestion",
                "ack_all": "ack_alerts",
                "ack": "ack_alert",
            }.get(name, name)
            parameters = {}
            if name == "close":
                ticket, identifier = _ids(command.args, 2)
                parameters = {"ticket": ticket, "position_identifier": identifier}
            elif name in {"approve", "reject"}:
                parameters = {"suggestion_id": _ids(command.args, 1)[0]}
            elif name == "ack":
                parameters = {"alert_id": _ids(command.args, 1)[0]}
            elif command.args:
                raise OwnerInterfaceError("invalid_command_arguments", 422)
            result = await services.action(owner_identity, action, parameters, _request_id(message))
            await _action_reply(message, result)
        except OwnerInterfaceError as error:
            await _reply(
                message, "Owner action denied: " + error.code + ". Do not retry uncertain execution."
            )

    @router.callback_query(F.data.startswith("view:"))
    async def read_callback(callback: CallbackQuery, owner_identity):
        section = callback.data[5:]
        if section not in set(READS.values()):
            return
        try:
            result = await services.read(owner_identity, section, limit=10)
            await callback.answer("Stored projection")
            await _reply(callback.message, render(section, result), owner_menu(services.settings))
        except (OwnerInterfaceError, TelegramAPIError):
            return

    @router.callback_query(F.data.startswith("ctl:"))
    async def action_callback(callback: CallbackQuery, owner_identity):
        action = callback.data[4:]
        if action not in {"pause", "resume", "kill", "close_all"}:
            return
        try:
            await callback.answer("Owner request received")
            result = await services.action(owner_identity, action, {}, _request_id(callback))
            await _action_reply(callback.message, result)
        except OwnerInterfaceError as error:
            await _reply(callback.message, "Owner action denied: " + error.code)
        except TelegramAPIError:
            return

    @router.callback_query(F.data.startswith("confirm:"))
    @router.callback_query(F.data.startswith("cancel:"))
    async def decide_callback(callback: CallbackQuery, owner_identity):
        try:
            await callback.answer("Checking single-use owner confirmation")
            prefix, token = callback.data.split(":", 1)
            result = await services.confirm_button(owner_identity, token, cancel=prefix == "cancel")
            await _action_reply(callback.message, result)
        except OwnerInterfaceError as error:
            await _reply(
                callback.message, "Confirmation denied: " + error.code + ". Do not retry uncertain execution."
            )
        except TelegramAPIError:
            return

    @router.errors()
    async def safe_error(event: ErrorEvent):
        # aiogram's default exception printer could expose request/update values.
        # Existing saga already journals uncertain effects; do not echo exception.
        return True

    return router
