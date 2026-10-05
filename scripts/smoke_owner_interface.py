"""OFFLINE owner API/aiogram/owned-close smoke, artificial credentials and prices.

No socket, Telegram/provider HTTP, native MT5 SDK or real trading. TEST_ONLY
HMAC signer is not a genuine Telegram launch. Synthetic results are not evidence.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from aiogram.types import Chat, Message, Update, User

from app.owner_services import OwnerServices
from core.database import Database
from core.settings import Settings
from miniapp.server import create_app
from scripts.synthetic_owner_fixtures import SyntheticTelegramSession, signed_fixture
from telegram_bot.bot import TelegramOwnerTransport
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import ManualClock, Side, SourceKind


class FixtureSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


async def smoke():
    with tempfile.TemporaryDirectory(prefix="reflex-owner-smoke-") as temporary:
        settings = FixtureSettings(
            _env_file=None,
            project_root=Path(temporary),
            symbols=("EURUSD",),
            telegram_owner_id=42,
            telegram_bot_token="123456789:TEST_ONLY_NEVER_CONTACT_TELEGRAM",
            telegram_miniapp_url="https://owner.fixture",
            api_trusted_hosts=("owner.fixture",),
            max_slippage_points=2,
            atr_trailing_enabled=False,
        )
        clock = ManualClock(datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
        database = Database(settings)
        database.initialize()
        engine = ExecutionEngine(MockMT5Client(settings, clock=clock), database, settings)
        await engine.initialize()
        services = OwnerServices(database, settings, execution=engine)
        application = create_app(settings, services)
        session = SyntheticTelegramSession(clock)
        transport = TelegramOwnerTransport(services, session=session)
        headers = {"X-Telegram-Init-Data": signed_fixture(settings, clock)}
        checks = []
        try:
            async with application.router.lifespan_context(application):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application), base_url="https://owner.fixture"
                ) as client:
                    assert (await client.get("/api/dashboard")).status_code == 401
                    checks.append("unauthenticated_denied")
                    for endpoint in (
                        "dashboard",
                        "positions",
                        "trades",
                        "signals",
                        "news",
                        "ai_suggestions",
                        "settings",
                        "logs",
                    ):
                        response = await client.get("/api/" + endpoint, headers=headers)
                        assert response.status_code == 200 and not response.json()["meta"]["broker_read"]
                    checks.append("eight_authenticated_SQL_views")
                    key = str(uuid4())
                    request = {"request_id": key}
                    prepared = (await client.post("/api/resume", headers=headers, json=request)).json()
                    assert (
                        prepared["status"] == "confirmation_required"
                        and database.status()["state"] == "paused"
                    )
                    request["confirmation_token"] = prepared["confirmation_token"]
                    assert (await client.post("/api/resume", headers=headers, json=request)).json()[
                        "status"
                    ] == "completed"
                    assert (await client.post("/api/resume", headers=headers, json=request)).json()[
                        "replayed"
                    ]
                    checks.append("single_use_revision_fenced_resume_and_replay")
                    plan = await engine.calculator.plan_market_order(
                        "EURUSD", Side.BUY, Decimal("1.09780"), strategy="TEST_ONLY_OWNER_SMOKE"
                    )
                    context = DecisionContext(
                        observed_at=clock.now(),
                        bar_closed_at=clock.now() - timedelta(minutes=1),
                        source=SourceKind.SYNTHETIC,
                        signal_score=90,
                        ai_confidence=90,
                        news=NewsWindow(
                            True, True, clock.now(), clock.now(), clock.now() + timedelta(hours=1), "a" * 64
                        ),
                    )
                    fill = await engine.execute(plan, context)
                    owned = engine.logger.owned(engine.account_key)[0]
                    body = {
                        "request_id": str(uuid4()),
                        "ticket": owned.ticket,
                        "position_identifier": owned.identifier,
                    }
                    confirmation = (
                        await client.post("/api/close_position", json=body, headers=headers)
                    ).json()
                    assert len(engine.broker._positions) == 1
                    body["confirmation_token"] = confirmation["confirmation_token"]
                    closed = (await client.post("/api/close_position", json=body, headers=headers)).json()
                    assert closed["status"] == "completed" and not closed["broker_account_flat_claimed"]
                    assert (await client.post("/api/close_position", json=body, headers=headers)).json()[
                        "replayed"
                    ]
                    assert not engine.broker._positions
                    checks.append("captured_owned_close_once_no_flat_claim")
                    stored = services.suggestions.create(
                        "reduce_risk",
                        {"risk_percent": "0.4"},
                        reason="TEST_ONLY proposal",
                        request_hash="e" * 64,
                    )
                    body = {"request_id": str(uuid4()), "suggestion_id": stored.suggestion_id}
                    confirmation = (
                        await client.post("/api/approve_suggestion", json=body, headers=headers)
                    ).json()
                    body["confirmation_token"] = confirmation["confirmation_token"]
                    decision = (
                        await client.post("/api/approve_suggestion", json=body, headers=headers)
                    ).json()
                    assert (
                        decision["proposal_status"] == "approved"
                        and not decision["settings_applied"]
                        and not decision["trade_executed"]
                    )
                    checks.append("proposal_approval_not_application_or_trade")
                    for route in ("/api/open", "/api/enable_live", "/api/apply_suggestion"):
                        assert (
                            await client.post(route, json={"request_id": str(uuid4())}, headers=headers)
                        ).status_code == 404
                    checks.append("no_open_live_or_apply_routes")
                    clock.advance(timedelta(seconds=300))
                    assert (await client.get("/api/dashboard", headers=headers)).status_code == 401
                    checks.append("expired_bearer_not_renewed_locally")
            update = Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=clock.now(),
                    chat=Chat(id=42, type="private"),
                    from_user=User(id=42, is_bot=False, first_name="Fixture owner"),
                    text="/status",
                ),
            )
            await transport.dispatcher.feed_update(transport.bot, update)
            assert len(session.calls) == 1 and session.calls[0][1].chat_id == 42
            checks.append("actual_aiogram_owner_private_scripted_reply")
            other = update.model_copy(
                update={
                    "update_id": 2,
                    "message": update.message.model_copy(update={"chat": Chat(id=-55, type="group")}),
                }
            )
            await transport.dispatcher.feed_update(transport.bot, other)
            assert len(session.calls) == 1
            checks.append("group_command_silent_deny")
            return {
                "warning": (
                    "TEST_ONLY HMAC/update/ASGI and artificial prices; NOT Telegram authenticity, "
                    "native execution, profitability or stage permission."
                ),
                "checks": checks,
                "checks_passed": len(checks),
                "actual_http_network_calls": 0,
                "actual_telegram_network_calls": 0,
                "scripted_telegram_api_methods": len(session.calls),
                "simulated_orders": 2,
                "real_orders": 0,
                "simulated_position_identifier": fill.position_identifier,
                "native_sdk_imported": "MetaTrader5" in sys.modules,
                "eligible_stage_evidence": False,
            }
        finally:
            await transport.close()
            await engine.shutdown()
            database.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), indent=2))
