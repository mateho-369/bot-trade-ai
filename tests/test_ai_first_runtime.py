"""AI-FIRST runtime wiring, owner notifications, /ai + /ai_reset and the Mini App journal view.

Synthetic broker, scripted Telegram session, no provider network, no real orders.
"""

import pytest
from sqlalchemy import select

from ai.ai_brain import BrainResult
from ai.ai_first import AIFirstLayer
from ai.ai_first_schemas import decode
from ai.config_adjuster import AIConfigAdjuster
from ai.decision_journal import AIConfigOverlay, DecisionJournal
from app.read_model import OwnerReadModel
from core.models import AuditLog
from telegram_bot.ai_notifications import AIOwnerNotifier, format_trailing
from tests.owner_helpers import api_client, auth_header, fake_transport, message_update, owner_services
from tests.risk_helpers import OWNER, config
from tests.runtime_helpers import close_runtime, runtime
from trading.ai_adaptive_trailing import AdaptiveTrailing, TrailingEvent


# -- composition / scheduler -----------------------------------------------------------------------
async def test_runtime_composes_ai_first_layer_and_lock_first_trailing(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        assert isinstance(resources.ai_first, AIFirstLayer)
        assert isinstance(resources.positions.adaptive, AdaptiveTrailing)
        assert resources.ai_first.brain.mode == "rule"  # AI_PROVIDER=disabled: no provider object.
        for name in ("ai_learning", "ai_config_review", "ai_nightly_review"):
            assert name in jobs.jobs
        learning = await jobs.run_job("ai_learning")
        assert learning["state"] == "reviewed" and learning["trades"] == []
        review = await jobs.run_job("ai_config_review")
        assert review["state"] in {"ai_unavailable", "market_unavailable"}
        assert await jobs.run_job("notifications") == {"state": "no_transport"}  # No Telegram in tests.
        signals = await jobs.run_job("signals")  # Paused by default: no entries, no crash.
        assert signals["state"] == "paused"
    finally:
        await close_runtime(resources, jobs)


async def test_ai_first_can_be_disabled_without_changing_the_reviewed_runtime(tmp_path):
    resources, health, jobs = await runtime(tmp_path, ai_first_enabled=False)
    try:
        assert resources.ai_first is None and resources.positions.adaptive is None
        assert (await jobs.run_job("ai_learning")) == {"state": "disabled"}
    finally:
        await close_runtime(resources, jobs)


async def test_adaptive_trailing_switch_keeps_the_brain_but_uses_mechanical_trailing(tmp_path):
    resources, health, jobs = await runtime(tmp_path, ai_adaptive_trailing_enabled=False)
    try:
        assert resources.ai_first is not None and resources.positions.adaptive is None
    finally:
        await close_runtime(resources, jobs)


async def test_trades_today_counts_the_trading_day(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        assert resources.ai_first.trades_today() == 0
    finally:
        await close_runtime(resources, jobs)


# -- notifications ---------------------------------------------------------------------------------
class Bot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("TEST network")
        self.sent.append(kwargs)


def executable_result():
    decision = decode(
        "decision",
        '{"action":"open_buy","confidence":88,"reason":"trend","suggested_risk_percent":0.3,'
        '"suggested_target_profit":5,"suggested_sl_distance":0.002,"news_risk":"low","market_condition":"trending"}',
    )
    return BrainResult("entry", decision, "ai", "qwen/qwen3.8-27b", True)


async def test_owner_notifications_are_bounded_plain_text_and_at_most_once(tmp_path):
    settings = config(tmp_path)
    notifier = AIOwnerNotifier(settings, secrets=("SECRET-VALUE",))
    notifier.decision(executable_result(), "EURUSD")
    notifier.circuit_opened(reason="ReadTimeout SECRET-VALUE", failures=3)
    notifier.circuit_closed()
    bot = Bot()
    result = await notifier.drain(bot)
    assert result["delivered"] == 3 and len(bot.sent) == 3
    assert all(m["chat_id"] == OWNER and m["parse_mode"] is None for m in bot.sent)
    texts = "\n".join(m["text"] for m in bot.sent)
    assert "OPEN_BUY" in texts.upper() and "EURUSD" in texts and "88" in texts
    assert "rule" in texts.lower() and "SECRET-VALUE" not in texts
    notifier.circuit_closed()
    failing = await notifier.drain(Bot(fail=True))
    assert failing["uncertain"] == 1 and failing["queued"] == 0  # Never resent.


async def test_non_executable_or_unowned_notifications_are_not_queued(tmp_path):
    notifier = AIOwnerNotifier(config(tmp_path))
    wait = executable_result()
    notifier.decision(BrainResult("entry", wait.decision, "ai", "m", False), "EURUSD")
    assert not notifier.outbox
    anonymous = AIOwnerNotifier(config(tmp_path, telegram_owner_id=None, telegram_bot_token=""))
    anonymous.circuit_opened(reason="x", failures=3)
    assert not anonymous.outbox and (await anonymous.drain(Bot()))["delivered"] == 0


async def test_config_adjustment_notification_points_to_owner_approval(tmp_path):
    services = owner_services(tmp_path)
    notifier = AIOwnerNotifier(services.settings)
    adjuster = AIConfigAdjuster(services.database, services.settings, services.clock, notifier=notifier)
    adjuster.propose("max_daily_trades", 5, reason="ranging")
    adjuster.propose("risk_percent_per_trade", 0.4, reason="calm")
    texts = list(notifier.outbox)
    assert any("max_daily_trades" in t and "approv" in t.lower() for t in texts)
    assert any("risk_percent_per_trade" in t and "applied" in t.lower() for t in texts)


def test_trailing_notification_mentions_lock_first():
    ai = TrailingEvent(
        7, 60, "1.1025", "close_now", 81.0, "momentum fading", "closed_with_locked_profit", "ai"
    )
    text = format_trailing(ai)
    assert "60% placed FIRST" in text and "close_now" in text and "position 7" in text
    mechanical = TrailingEvent(
        7,
        30,
        "1.10165",
        None,
        None,
        "AI unavailable, using mechanical trailing",
        "mechanical_continue",
        "mechanical",
    )
    assert "mechanical" in format_trailing(mechanical).lower()


# -- owner reads / overrides -----------------------------------------------------------------------
def seed_journal(services):
    journal = DecisionJournal(services.database, services.clock)
    journal.record(
        kind="entry",
        source="ai",
        action="open_buy",
        reason="trend aligned",
        confidence=82,
        symbol="EURUSD",
        executed=True,
        final_action="execution_filled",
    )
    journal.record(
        kind="trailing",
        source="mechanical",
        action="mechanical_continue",
        reason="AI unavailable, using mechanical trailing",
        position_id=9,
        threshold_reached=30,
        final_action="mechanical_continue",
    )
    return journal


def test_read_model_ai_journal_is_empty_without_tables_and_never_creates_them(tmp_path):
    services = owner_services(tmp_path)
    view = OwnerReadModel(services.database, services.settings, services.clock).ai_journal(limit=10, offset=0)
    assert view["items"] == [] and view["adjustments"] == []
    from sqlalchemy import inspect

    assert "ai_decision_journal" not in inspect(services.database.engine).get_table_names()


async def test_telegram_ai_command_renders_the_decision_journal(tmp_path):
    services = owner_services(tmp_path)
    seed_journal(services)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/ai"))
    replies = [m for name, m in session.calls if name == "SendMessage"]
    assert len(replies) == 1 and replies[0].parse_mode is None and len(replies[0].text) <= 3800
    assert "open_buy" in replies[0].text and "mechanical" in replies[0].text
    await transport.close()


async def test_owner_ai_reset_reverts_every_ai_adjustment_and_is_audited(tmp_path):
    services = owner_services(tmp_path)
    adjuster = AIConfigAdjuster(services.database, services.settings, services.clock)
    adjuster.propose("risk_percent_per_trade", 0.4, reason="calm")
    adjuster.propose("max_daily_trades", 5, reason="ranging")
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/ai_reset"))
    replies = [m for name, m in session.calls if name == "SendMessage"]
    assert "reverted" in replies[0].text.lower()
    with services.database.session() as sql:
        statuses = set(sql.scalars(select(AIConfigOverlay.status)).all())
        actions = set(sql.scalars(select(AuditLog.action)).all())
    assert statuses == {"reverted"} and "owner.ai_config_reset" in actions
    fresh = AIConfigAdjuster(services.database, services.settings, services.clock)
    assert "risk_percent_per_trade" not in fresh.effective()
    await transport.close()


async def test_non_owner_cannot_reset_ai_adjustments(tmp_path):
    services = owner_services(tmp_path)
    adjuster = AIConfigAdjuster(services.database, services.settings, services.clock)
    adjuster.propose("risk_percent_per_trade", 0.4, reason="calm")
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/ai_reset", sender=43))
    assert session.calls == []
    with services.database.session() as sql:
        assert set(sql.scalars(select(AIConfigOverlay.status)).all()) == {"applied"}
    await transport.close()


async def test_miniapp_ai_journal_view_requires_owner_and_returns_history(tmp_path):
    services = owner_services(tmp_path)
    seed_journal(services)
    AIConfigAdjuster(services.database, services.settings, services.clock).propose(
        "target_profit_per_trade", 6, reason="trend"
    )
    async with api_client(services) as (client, _):
        denied = await client.get("/api/ai_journal")
        allowed = await client.get("/api/ai_journal?limit=10", headers=auth_header(services))
    assert denied.status_code == 401 and allowed.status_code == 200
    body = allowed.json()
    assert [i["action"] for i in body["items"]] == ["mechanical_continue", "open_buy"]
    assert body["adjustments"][0]["parameter"] == "target_profit_per_trade"
    assert body["summary"]
    assert "TEST_ONLY_NEVER_CONTACT_TELEGRAM" not in allowed.text


@pytest.mark.parametrize("path", ["/", "/static/index.html"])
async def test_miniapp_ships_the_ai_decision_history_view(tmp_path, path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        page = await client.get(path)
    if page.status_code == 200 and "<html" in page.text.lower():
        assert "view-ai_journal" in page.text and "ai-journal-list" in page.text


@pytest.mark.parametrize("provider", ["scripted", "rule"])
async def test_ai_first_smoke_script_passes_offline(provider):
    from scripts.smoke_ai_first import run

    report = await run(provider)
    assert report["ok"] and report["broker"] == "mock" and report["real_orders"] == 0
