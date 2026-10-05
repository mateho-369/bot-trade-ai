"""Owner-controlled AI_FALLBACK_MODE and the two-layer AI-dynamic limits (offline, mock/paper only).

* BLOCK_ON_AI_FAILURE (default): AI unavailable => NO new entries; trailing/protection continue.
* TECHNICAL_ONLY: AI unavailable => the technical score must reach AI_RULE_FALLBACK_MIN_SCORE.
* Toggle: owner only (Telegram + Mini App), audited, never touches the kill switch or risk gates.
* Dynamic limits: AI values only while the AI is available; owner defaults otherwise; hard caps always.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from ai.ai_brain import AIBrain
from ai.ai_router import AIRouter
from ai.ai_supervisor import AISupervisor
from ai.openai_client import OpenAIClient
from core.models import AuditLog, BotState
from tests.ai_helpers import ScriptedHTTP
from tests.owner_helpers import api_client, auth_header, fake_transport, message_update, owner_services
from tests.risk_helpers import OWNER, make_engine, open_one, safe_context
from tests.signal_helpers import make_signal_runtime, news
from tests.test_ai_first import Provider, decision_json, market, snapshot_for  # noqa: F401 (fixture)
from trading.ai_controls import (
    DYNAMIC_BOUNDS,
    HARD_MAX_DAILY_TRADES,
    HARD_MAX_OPEN_POSITIONS,
    HARD_MAX_RISK_PERCENT,
    STATUS_FRESH_SECONDS,
    ai_status,
    broker_ceilings,
    effective_limits,
    ensure_control_tables,
    fallback_mode,
    publish_ai_status,
    set_fallback_mode,
    write_dynamic,
)
from trading.types import RiskViolation, Side, TradingDisabled

GROQ = {
    "ai_provider": "openai",
    "ai_fallback_provider": "disabled",
    "openai_base_url": "https://api.groq.com/openai/v1",
    "openai_model": "qwen/qwen3.8-27b",
    "openai_response_format": "json_schema_strict",
    "openai_api_key": "TEST_GROQ_KEY",
}


async def groq_down(tmp_path, **settings):
    """Signal runtime whose Groq provider answers HTTP 503 (outage)."""
    http = ScriptedHTTP("openai", status=503)
    signals, execution = await make_signal_runtime(tmp_path, **GROQ, **settings)
    router = AIRouter(
        execution.settings,
        execution.database,
        execution.clock,
        providers=(OpenAIClient(execution.settings, transport=http.transport),),
    )
    supervisor = AISupervisor(
        execution.database, execution.settings, execution.clock, execution.profile, router=router
    )
    await supervisor.initialize()
    return signals, execution, supervisor, http


async def shutdown(execution, supervisor):
    await supervisor.close()
    await execution.shutdown()
    execution.database.close()


def audits(database, action):
    with database.session() as session:
        return [row.details for row in session.scalars(select(AuditLog).where(AuditLog.action == action))]


# -- end-to-end entry behaviour ------------------------------------------------------------------
async def test_default_block_mode_opens_no_entry_when_the_ai_is_down(tmp_path):
    signals, execution, supervisor, http = await groq_down(tmp_path)
    try:
        assert execution.settings.ai_fallback_mode == "BLOCK_ON_AI_FAILURE"  # Safest default.
        execution.control.resume(OWNER, account_key=execution.account_key)
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert http.requests  # The AI was consulted first (AI-first stays primary).
        assert not ready.approved and ready.state == "rejected"
        assert "ai_unavailable_or_invalid" in ready.reasons
        [detail] = audits(execution.database, "ai.rule_fallback_not_permitted")
        assert detail["fallback_mode"] == "BLOCK_ON_AI_FAILURE"
        assert await execution.broker.get_positions() == ()
    finally:
        await shutdown(execution, supervisor)


async def test_technical_mode_good_signal_opens_a_paper_trade(tmp_path):
    signals, execution, supervisor, _ = await groq_down(tmp_path)
    try:
        set_fallback_mode(execution.database, execution.clock, "TECHNICAL_ONLY", owner_id=OWNER)
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert ready.approved, ready.reasons
        assert ready.score >= execution.settings.ai_rule_fallback_min_score
        [detail] = audits(execution.database, "ai.rule_fallback_review")
        assert detail["fallback_mode"] == "TECHNICAL_ONLY"
        execution.control.resume(OWNER, account_key=execution.account_key)
        outcome = await execution.execute_signal(ready.signal_id)
        assert outcome.status.value == "filled" and outcome.reason == "SIMULATED_ONLY"
        assert len(await execution.broker.get_positions()) == 1
    finally:
        await shutdown(execution, supervisor)


async def test_technical_mode_bad_signal_opens_no_trade(tmp_path):
    signals, execution, supervisor, _ = await groq_down(tmp_path, ai_rule_fallback_min_score=100)
    try:
        set_fallback_mode(execution.database, execution.clock, "TECHNICAL_ONLY", owner_id=OWNER)
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert ready.score < 100 and not ready.approved
        assert await execution.broker.get_positions() == ()
    finally:
        await shutdown(execution, supervisor)


async def test_technical_mode_still_obeys_the_news_block(tmp_path):
    signals, execution, supervisor, _ = await groq_down(tmp_path)
    try:
        set_fallback_mode(execution.database, execution.clock, "TECHNICAL_ONLY", owner_id=OWNER)
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock, safe=False))
        assert not ready.approved
    finally:
        await shutdown(execution, supervisor)


# -- AI brain (AI-first entry path) ---------------------------------------------------------------
async def test_brain_block_mode_returns_a_non_executable_blocked_decision(market):  # noqa: F811
    settings, database, clock, _ = market
    brain = AIBrain(settings, clock, provider=Provider("broken"))
    result = await brain.decide(await snapshot_for(market))
    assert result.source == "ai_blocked" and not result.executable
    assert "ai_unavailable_block_mode" in result.reasons


async def test_brain_retries_transient_failures_before_falling_back(market, monkeypatch):  # noqa: F811
    settings, database, clock, _ = market
    settings = settings.model_copy(update={"ai_max_retries": 3})
    provider = Provider("broken", "broken", decision_json())
    brain = AIBrain(settings, clock, provider=provider)
    sleeps = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("ai.ai_brain.asyncio.sleep", no_sleep)
    result = await brain.decide(await snapshot_for(market))
    assert result.source == "ai" and result.executable and provider.calls == 3
    assert sleeps and all(0 < s <= 1.0 for s in sleeps)  # Bounded backoff, then a real AI answer.


async def test_brain_follows_the_owner_mode_at_decision_time(market):  # noqa: F811
    settings, database, clock, _ = market
    modes = iter(["BLOCK_ON_AI_FAILURE", "TECHNICAL_ONLY"])
    brain = AIBrain(settings, clock, provider=Provider("broken"), fallback_mode=lambda: next(modes))
    snapshot = await snapshot_for(market)
    assert (await brain.decide(snapshot)).source == "ai_blocked"
    technical = await brain.decide(snapshot)
    assert technical.source == "rule_fallback" and technical.executable


# -- owner toggle: Telegram + Mini App, audited, kill switch untouched ------------------------------
async def test_telegram_toggle_is_owner_only_and_audited(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    try:
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/ai_fallback_technical")
        )
        assert fallback_mode(services.database, services.settings) == "TECHNICAL_ONLY"
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/ai_fallback_status", message_id=2)
        )
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/ai_fallback_block", message_id=3)
        )
        assert fallback_mode(services.database, services.settings) == "BLOCK_ON_AI_FAILURE"
        replies = [m.text for name, m in session.calls if name == "SendMessage"]
        assert "TECHNICAL_ONLY" in replies[0] and "kill switch" in replies[0].lower()
        assert "AI fallback mode: TECHNICAL_ONLY" in replies[1]
        assert "BLOCK_ON_AI_FAILURE" in replies[2]
        changes = audits(services.database, "owner.ai_fallback_mode_changed")
        assert [(c["from"], c["to"], c["owner_id"]) for c in changes] == [
            (None, "TECHNICAL_ONLY", services.settings.telegram_owner_id),
            ("TECHNICAL_ONLY", "BLOCK_ON_AI_FAILURE", services.settings.telegram_owner_id),
        ]
        calls = len(session.calls)
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/ai_fallback_technical", sender=43, message_id=4)
        )
        assert len(session.calls) == calls  # Non-owner: silently ignored.
        assert fallback_mode(services.database, services.settings) == "BLOCK_ON_AI_FAILURE"
    finally:
        await transport.close()


async def test_toggle_never_touches_the_kill_switch(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        engine.control.kill(OWNER)
        for mode in ("TECHNICAL_ONLY", "BLOCK_ON_AI_FAILURE", "TECHNICAL_ONLY"):
            set_fallback_mode(engine.database, engine.clock, mode, owner_id=OWNER)
        with engine.database.session() as session:
            state = session.get(BotState, 1)
            assert state.kill_switch_active and state.desired_state == "killed"
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_miniapp_settings_toggle_requires_owner_and_validates_the_mode(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        denied = await client.post(
            "/api/ai_fallback",
            json={"request_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12, "mode": "TECHNICAL_ONLY"},
        )
        bad = await client.post(
            "/api/ai_fallback",
            json={"request_id": "1" * 8 + "-1111-4111-8111-" + "1" * 12, "mode": "YOLO"},
            headers=auth_header(services),
        )
        ok = await client.post(
            "/api/ai_fallback",
            json={"request_id": "2" * 8 + "-2222-4222-8222-" + "2" * 12, "mode": "TECHNICAL_ONLY"},
            headers=auth_header(services),
        )
        status = await client.get("/api/ai_fallback", headers=auth_header(services))
        settings_view = await client.get("/api/settings", headers=auth_header(services))
        limits = await client.get("/api/limits", headers=auth_header(services))
    assert denied.status_code == 401 and bad.status_code == 422
    assert ok.status_code == 200 and ok.json()["ai_fallback_mode"] == "TECHNICAL_ONLY"
    assert status.json()["mode"] == "TECHNICAL_ONLY" and status.json()["source"] == "owner"
    assert status.json()["kill_switch_unaffected"] is True
    assert settings_view.json()["ai_fallback"]["mode"] == "TECHNICAL_ONLY"
    body = limits.json()
    assert body["hard_caps"]["max_daily_trades"] == 25 and body["hard_caps"]["max_open_positions"] == 5
    assert body["effective"]["source"] == "defaults"  # No AI heartbeat => owner defaults.
    assert len(audits(services.database, "owner.ai_fallback_mode_changed")) == 1


# -- two-layer dynamic limits --------------------------------------------------------------------
def test_hard_caps_and_ai_bounds_match_the_owner_specification():
    assert (HARD_MAX_DAILY_TRADES, HARD_MAX_OPEN_POSITIONS, HARD_MAX_RISK_PERCENT) == (25, 5, Decimal("1.0"))
    assert DYNAMIC_BOUNDS["max_daily_trades"] == (6, 20)
    assert DYNAMIC_BOUNDS["max_open_positions"] == (1, 5)
    assert DYNAMIC_BOUNDS["risk_percent_per_trade"] == (Decimal("0.1"), Decimal("1.0"))
    assert DYNAMIC_BOUNDS["target_profit_per_trade"] == (Decimal("1"), Decimal("20"))


async def test_ai_values_apply_only_while_the_ai_is_available(market):  # noqa: F811
    settings, database, clock, _ = market
    ensure_control_tables(database)
    with database.session() as session:
        write_dynamic(session, settings, clock, "max_daily_trades", 18, history_id=None, reason="trend")
        write_dynamic(session, settings, clock, "max_open_positions", 4, history_id=None, reason="trend")
    before = effective_limits(database, settings, clock)
    assert before.source == "defaults" and before.max_daily_trades == settings.max_daily_trades
    publish_ai_status(database, clock, mode="ai", detail="test")
    live = effective_limits(database, settings, clock)
    assert live.source == "ai" and (live.max_daily_trades, live.max_open_positions) == (18, 4)
    clock.advance(timedelta(seconds=STATUS_FRESH_SECONDS + 1))  # Heartbeat stale => AI unavailable.
    assert ai_status(database, clock)["available"] is False
    stale = effective_limits(database, settings, clock)
    assert stale.source == "defaults" and stale.max_open_positions == settings.max_open_positions
    publish_ai_status(database, clock, mode="rule", detail="circuit open")
    assert effective_limits(database, settings, clock).source == "defaults"


async def test_broker_ceilings_follow_the_hard_caps_not_the_owner_defaults(market):  # noqa: F811
    settings, *_ = market
    ceilings = broker_ceilings(settings)
    assert ceilings.max_open_positions <= HARD_MAX_OPEN_POSITIONS
    assert ceilings.risk_percent <= HARD_MAX_RISK_PERCENT


@pytest.mark.parametrize("fresh_ai", [True, False])
async def test_risk_engine_enforces_the_ai_position_limit_only_while_ai_is_up(tmp_path, fresh_ai):
    engine = await make_engine(tmp_path, symbols=("EURUSD", "GBPUSD"))
    try:
        ensure_control_tables(engine.database)
        with engine.database.session() as session:
            write_dynamic(
                session,
                engine.settings,
                engine.clock,
                "max_open_positions",
                1,
                history_id=None,
                reason="chop",
            )
        if fresh_ai:
            publish_ai_status(engine.database, engine.clock, mode="ai", detail="test")
        await open_one(engine)
        assert len(await engine.broker.get_positions()) == 1
        plan = await engine.calculator.plan_market_order(
            "GBPUSD", Side.BUY, Decimal("1.24770"), strategy="TEST_SYNTHETIC", idempotency_key="2" * 64
        )
        assert plan is not None
        try:
            await engine.execute(plan, safe_context(engine.clock))
        except (RiskViolation, TradingDisabled):
            pass  # A vetoed grant may surface as an exception; the durable decision is checked below.
        positions = len(await engine.broker.get_positions())
        vetoes = [d for d in audits(engine.database, "risk.entry_decision") if d.get("approved") is False]
        if fresh_ai:  # AI limit 1 (inside hard cap 5) => second symbol vetoed by the risk engine.
            assert positions == 1 and vetoes and "position_cap" in vetoes[-1]["reasons"]
        else:  # AI unavailable => owner default 3 => second symbol allowed.
            assert positions == 2 and not any("position_cap" in v["reasons"] for v in vetoes)
    finally:
        await engine.shutdown()
        engine.database.close()
