"""OFFLINE TEST ONLY: sign a TEST_ONLY token; scripted aiogram replies, no HTTP.

NOT Telegram-issued identity/authenticity, real update validation or live proof.
Production modules MUST NOT import this file; no fixture auth API exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import Chat, Message, User, WebhookInfo


def signed_fixture(settings, clock, *, user=None, fields=None):
    token = settings.telegram_bot_token.get_secret_value()
    if "TEST_ONLY" not in token:
        raise ValueError("fixture signer only accepts explicitly TEST_ONLY tokens")
    values = {
        "auth_date": str(int(clock.now().timestamp())),
        "user": json.dumps(
            user if user is not None else {"id": settings.telegram_owner_id, "first_name": "Fixture owner"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    values.update(fields or {})
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()) if k != "hash")
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class SyntheticTelegramSession(BaseSession):
    def __init__(self, clock, *, fail_methods=()):
        super().__init__()
        self.clock, self.fail_methods = clock, set(fail_methods)
        self.calls = []
        self.closed = False
        self.webhook_url = ""

    async def close(self):
        self.closed = True

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        if name in self.fail_methods:
            raise TelegramNetworkError(
                method=method, message="TEST_ONLY scripted uncertain Telegram delivery"
            )
        if name == "GetMe":
            return User(id=bot.id, is_bot=True, first_name="Fixture bot", username="reflex_fixture_bot")
        if name == "GetWebhookInfo":
            return WebhookInfo(url=self.webhook_url, has_custom_certificate=False, pending_update_count=0)
        if name == "SendMessage":
            return Message(
                message_id=10000 + len(self.calls),
                date=self.clock.now(),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Fixture bot"),
                text=method.text,
                reply_markup=method.reply_markup,
            )
        if name in {
            "AnswerCallbackQuery",
            "SetMyCommands",
            "SetChatMenuButton",
            "SetWebhook",
            "DeleteWebhook",
        }:
            return True
        raise AssertionError("Unscripted Telegram method: " + name)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        raise AssertionError("No fixture network/download allowed")
        yield b""  # Implements the abstract async-generator protocol, never reached.
