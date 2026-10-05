"""Explicit aiogram 3 polling/webhook transport; construction does NO network I/O.

Part 10 chooses polling OR webhook and owns lifecycle. No unauthenticated webhook
is ever mounted. Menu commands are scoped to the configured owner's private chat.
"""

from __future__ import annotations

import hmac

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonWebApp, Update, WebAppInfo

from app.owner_identity import OwnerInterfaceError
from telegram_bot.handlers import build_router
from telegram_bot.middleware import OwnerOnlyMiddleware
from telegram_bot.miniapp_auth import strict_json

COMMANDS = [
    ("start", "Owner help and dashboard"),
    ("status", "Stored bot and account status"),
    ("dashboard", "Open the owner dashboard"),
    ("positions", "Stored owned-position candidates"),
    ("trades", "Stored trade ledger"),
    ("signals", "Stored signal decisions"),
    ("news", "Stored headlines and coverage"),
    ("suggestions", "Owner-reviewed AI proposals"),
    ("settings", "Read-only effective settings"),
    ("logs", "Sanitized audit metadata"),
    ("ai", "AI decision journal and adjustments"),
    ("pause", "Pause NEW entries"),
    ("resume", "Confirm entry resume, not live approval"),
    ("kill", "Latch NEW entries off, not flatten"),
    ("close", "Confirm exact captured owned position"),
    ("close_all", "Confirm captured bot-owned positions"),
    ("approve", "Approve proposal ONLY, do not apply"),
    ("reject", "Reject a pending proposal"),
    ("ai_reset", "Revert all AI config adjustments"),
    ("ai_fallback_status", "AI fallback mode and AI health"),
    ("ai_fallback_block", "AI down: block new entries (default)"),
    ("ai_fallback_technical", "AI down: technical score fallback"),
    ("limits", "AI-dynamic limits and hard caps"),
    ("alerts", "Recent alerts (add: critical)"),
    ("ack_all", "Acknowledge all alerts"),
    ("ai_stats", "Per-AI approvals, trades, win rate, profit"),
    ("audit", "Check every trade has a valid AI approval"),
]


def build_dispatcher(services):
    dispatcher = Dispatcher(owner_services=services)
    router = build_router(services)
    middleware = OwnerOnlyMiddleware(services)
    router.message.outer_middleware(middleware)
    router.callback_query.outer_middleware(middleware)
    dispatcher.include_router(router)
    return dispatcher


class TelegramOwnerTransport:
    def __init__(self, services, *, session=None):
        cfg = services.settings
        if (
            services.preview_only
            or cfg.telegram_owner_id is None
            or not cfg.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("explicit owner Telegram credentials required; previews cannot attach a bot")
        self.services = services
        self.bot = Bot(
            cfg.telegram_bot_token.get_secret_value(),
            session=session,
            default=DefaultBotProperties(parse_mode=None),
        )
        self.dispatcher = build_dispatcher(services)
        self._polling = False

    async def set_owner_menu(self):
        cfg = self.services.settings
        await self.bot.set_my_commands(
            [BotCommand(command=k, description=v) for k, v in COMMANDS],
            scope=BotCommandScopeChat(chat_id=cfg.telegram_owner_id),
        )
        if cfg.telegram_miniapp_url:
            await self.bot.set_chat_menu_button(
                chat_id=cfg.telegram_owner_id,
                menu_button=MenuButtonWebApp(
                    text="ReflexBot", web_app=WebAppInfo(url=cfg.telegram_miniapp_url)
                ),
            )

    async def poll(self):
        if self.services.settings.telegram_use_webhook or self._polling:
            raise OwnerInterfaceError("polling_webhook_modes_conflict", 409)
        if self.services.closing:
            return
        # Never silently delete another configured webhook or pending updates.
        info = await self.bot.get_webhook_info()
        if self.services.closing:
            return  # Shutdown during initial API read must not start a new poll.
        if info.url:
            raise OwnerInterfaceError("remove_existing_webhook_before_polling", 409)
        self._polling = True
        try:
            await self.dispatcher.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query"],
                handle_signals=False,
                close_bot_session=False,
                handle_as_tasks=True,
                tasks_concurrency_limit=4,
                polling_timeout=20,
            )
        finally:
            self._polling = False

    async def register_webhook(self):
        cfg = self.services.settings
        if not cfg.telegram_use_webhook or self._polling:
            raise OwnerInterfaceError("polling_webhook_modes_conflict", 409)
        await self.bot.set_webhook(
            url=cfg.telegram_webhook_url,
            secret_token=cfg.telegram_webhook_secret.get_secret_value(),
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
            max_connections=4,
        )

    async def feed_webhook(self, request):
        cfg = self.services.settings
        supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
        expected = cfg.telegram_webhook_secret.get_secret_value()
        if (
            not cfg.telegram_use_webhook
            or self._polling
            or not expected
            or len(supplied) > 256
            or not supplied.isascii()
            or not hmac.compare_digest(supplied, expected)
        ):
            raise OwnerInterfaceError("webhook_authentication_required", 403)
        try:
            raw = await request.body()
            if len(raw) > cfg.api_max_request_bytes:
                raise ValueError
            update = Update.model_validate(strict_json(raw), context={"bot": self.bot})
        except (ValueError, TypeError, RecursionError):
            raise OwnerInterfaceError("invalid_webhook_update", 422) from None
        # Unsupported update kinds are acknowledged without calling any handler.
        if update.message is not None or update.callback_query is not None:
            await self.dispatcher.feed_update(self.bot, update)
        return {"ok": True}

    async def close(self):
        await self.bot.session.close()
