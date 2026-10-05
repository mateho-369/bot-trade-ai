"""Silent deny for non-owner/group/forwarded/stale/business/inline updates.

Identity comes from aiogram's Telegram transport context, never message text.
Webhook integrity is separately enforced by a random secret header and TLS.
"""

from __future__ import annotations

import hmac
from datetime import timedelta

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.owner_identity import OwnerIdentity, OwnerInterfaceError
from core.security import sha256_json


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, services):
        self.services = services

    async def __call__(self, handler, event, data):
        cfg, clock = self.services.settings, self.services.clock
        sender = getattr(event, "from_user", None)
        bot = data.get("bot")
        if (
            cfg.telegram_owner_id is None
            or bot is None
            or sender is None
            or not hmac.compare_digest(bot.token, cfg.telegram_bot_token.get_secret_value())
            or type(sender.id) is not int
            or sender.id != cfg.telegram_owner_id
            or sender.is_bot
        ):
            return None
        callback = isinstance(event, CallbackQuery)
        message = event.message if callback else event
        if not isinstance(message, Message) or message.chat.type != "private" or message.chat.id != sender.id:
            return None
        if callback:
            if (
                event.inline_message_id is not None
                or not isinstance(event.data, str)
                or len(event.data) > 64
                or message.from_user is None
                or not message.from_user.is_bot
                or message.from_user.id != bot.id
            ):
                return None
        elif not isinstance(event, Message) or (
            message.from_user is None
            or message.from_user.id != sender.id
            or message.via_bot is not None
            or message.forward_origin is not None
            or message.sender_chat is not None
            or message.business_connection_id is not None
            or not isinstance(message.text, str)
            or len(message.text) > 512
        ):
            return None
        issued = message.date
        try:
            age = (clock.now() - issued).total_seconds()
        except (TypeError, ValueError):
            return None
        if not -cfg.telegram_auth_future_skew_seconds <= age < cfg.telegram_command_max_age_seconds:
            return None
        identity = OwnerIdentity(
            owner_id=sender.id,
            transport="telegram",
            credential_hash=sha256_json(
                {
                    "format": "telegram-private-owner-v1",
                    "chat_id": message.chat.id,
                    "bot_id": bot.id,
                    "config_hash": cfg.safety_fingerprint(),
                }
            ),
            config_hash=cfg.safety_fingerprint(),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=cfg.telegram_command_max_age_seconds),
        )
        command = (message.text or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
        safe_stop = event.data in {"ctl:pause", "ctl:kill"} if callback else command in {"/pause", "/kill"}
        try:
            self.services.limit_actor(identity, safe_stop=safe_stop)
        except OwnerInterfaceError:
            return None
        data["owner_identity"] = identity
        data["owner_services"] = self.services
        return await handler(event, data)
