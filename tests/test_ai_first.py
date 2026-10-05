"""AI-FIRST brain, awareness, journal, config adjuster, learning loop and reviewer (offline only).

No network, no broker, no orders: providers are in-process doubles or httpx.MockTransport.
"""

import asyncio
import dataclasses
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from ai.ai_brain import AIBrain, CircuitBreaker, RequestQueue, TTLCache, technical_fallback
from ai.ai_first_reviewer import AIFirstReviewer
from ai.ai_first_schemas import STRICT_SCHEMAS, InvalidAIReply, decode
from ai.config_adjuster import ABSOLUTE_MAX_OPEN_POSITIONS, HARD_LIMITS, AIConfigAdjuster
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from ai.learning_loop import LearningLoop
from ai.market_awareness import MarketAwarenessEngine, NewsContext, PerformanceTracker
from ai.ollama_client import ProviderContent
from ai.openai_client import OpenAIClient
from ai.suggestion_store import SuggestionStore
from core.database import Database
from core.models import AuditLog, Trade
from tests.risk_helpers import MOMENT, OWNER, config, make_engine, open_one
from tests.signal_helpers import make_signal_runtime, news
from trading.mock_mt5 import MockMT5Client
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, Side, SourceKind, TradingDisabled


def decision_json(**changes):
    values = dict(
        action="open_buy",
        confidence=85,
        reason="trend aligned on M5/M15/H1",
        suggested_risk_percent=0.3,
        suggested_target_profit=5,
        suggested_sl_distance=0.0025,
        news_risk="low",
        market_condition="trending",
    )
    values.update(changes)
    return json.dumps(values)


class Provider:
    configured = True

    def __init__(self, *replies, delay=0.0, error=None):
        self.replies, self.delay, self.error, self.calls, self.messages = list(replies), delay, error, 0, []

    async def complete(self, messages, schema):
        self.calls += 1
        self.messages.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return ProviderContent("openai", "qwen/qwen3.8-27b", text, simulated=False)


class Notes:
    def __init__(self):
        self.events = []

    def decision(self, result, symbol):
        self.events.append(("decision", result.source))

    def circuit_opened(self, **kwargs):
        self.events.append(("open", kwargs["failures"]))

    def circuit_closed(self, **kwargs):
        self.events.append(("closed",))

    def config_adjustment(self, result):
        self.events.append(("config", result.status))

    def trailing(self, event):
        self.events.append(("trailing", event.final_action))


@pytest.fixture
async def market(tmp_path):
    settings = config(tmp_path, symbols=("EURUSD", "XAUUSD"), ai_queue_min_interval_ms=0)
    database = Database(settings)
    database.initialize()
    clock = ManualClock(MOMENT)
    broker = MockMT5Client(settings, clock=clock)
    await broker.initialize()
    yield settings, database, clock, broker
    database.close()


def clear_news():
    return NewsContext(state="clear")


# -- strict contracts ------------------------------------------------------------------------------
def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


@pytest.mark.parametrize("kind", sorted(STRICT_SCHEMAS))
def test_every_ai_first_schema_is_groq_strict_compatible(kind):
    for node in _objects(STRICT_SCHEMAS[kind]):
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_decision_contract_matches_the_owner_specification():
    reply = decode("decision", decision_json())
    assert reply.action == "open_buy" and reply.opens
    for bad in (
        decision_json(action="martingale"),
        decision_json(suggested_risk_percent=1.5),
        decision_json(confidence=101),
        decision_json(news_risk="none"),
        decision_json()[:-1],
        json.dumps({**json.loads(decision_json()), "extra": 1}),
    ):
        with pytest.raises(InvalidAIReply):
            decode("decision", bad)


async def test_groq_client_sends_reviewed_strict_schema_and_deep_model(tmp_path):
    cfg = config(
        tmp_path,
        openai_api_key="TEST_ONLY_KEY",
        openai_base_url="https://api.groq.com/openai/v1",
        openai_model="qwen/qwen3.8-27b",
        openai_response_format="json_schema_strict",
    )
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        payload = captured[-1]
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": decision_json()},
                    }
                ],
            },
        )

    for model in (None, "openai/gpt-oss-120b"):
        client = OpenAIClient(cfg, transport=httpx.MockTransport(handle), model=model)
        result = await client.complete([{"role": "user", "content": "JSON"}], STRICT_SCHEMAS["decision"])
        assert decode("decision", result.content).action == "open_buy"
        await client.close()
    assert [p["model"] for p in captured] == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    fmt = captured[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == STRICT_SCHEMAS["decision"]


# -- infrastructure --------------------------------------------------------------------------------
def test_circuit_opens_after_three_consecutive_failures_and_half_opens_after_cooldown():
    now = [0.0]
    breaker = CircuitBreaker(3, 60, monotonic=lambda: now[0])
    assert [breaker.failure() for _ in range(3)] == [False, False, True]
    assert breaker.state == "open" and not breaker.allow()
    now[0] = 61
    assert breaker.state == "half_open" and breaker.allow() and not breaker.allow()
    assert breaker.failure() is False and breaker.state == "open"  # Failed trial: no re-notify.
    now[0] = 130
    assert breaker.allow() and breaker.success() is True and breaker.state == "closed"


def test_cache_expires_and_is_bounded():
    now = [0.0]
    cache = TTLCache(90, size=2, monotonic=lambda: now[0])
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") is None and cache.get("c") == 3
    now[0] = 91
    assert cache.get("c") is None


async def test_queue_bounds_concurrency_and_spaces_requests():
    queue, active, peak, starts = RequestQueue(2, 20), 0, 0, []
    loop = asyncio.get_running_loop()

    async def job():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        starts.append(loop.time())
        await asyncio.sleep(0.05)
        active -= 1

    await asyncio.gather(*(queue.run(job) for _ in range(5)))
    assert peak <= 2
    assert all(b - a >= 0.015 for a, b in zip(starts, starts[1:], strict=False))


# -- market awareness ------------------------------------------------------------------------------
async def test_market_snapshot_contains_everything_the_ai_needs(market):
    settings, database, clock, broker = market
    journal = DecisionJournal(database, clock)
    journal.record(
        kind="lesson", source="deterministic", action="lesson", reason="Reduce risk during high volatility"
    )
    engine = MarketAwarenessEngine(
        broker, settings, clock, performance=PerformanceTracker(database, clock), journal=journal
    )
    snapshot = await engine.snapshot("EURUSD", news=clear_news())
    prompt = snapshot.to_prompt()
    assert {"bid", "ask", "spread_points", "spread_limit_points"} <= set(prompt["quote"])
    assert set(prompt["timeframes"]) == {"M5", "M15", "H1"}
    m5 = prompt["timeframes"]["M5"]
    assert {"rsi", "macd_hist_atr", "ema_trend", "atr", "bb_position", "adx"} <= set(m5)
    assert len(prompt["last_candles"]) == 5 and prompt["price_action_20"]["bars"] == 20
    assert {"support_20", "resistance_20"} <= set(prompt["support_resistance"])
    assert prompt["market_condition_hint"] in {"trending", "ranging", "volatile", "news_impact"}
    assert prompt["config"]["max_open_positions"] == 3
    assert prompt["news"]["state"] == "clear" and prompt["news"]["risk"] == "low"
    assert {"last_24h", "last_7d"} <= set(prompt["performance"])
    assert prompt["lessons_learned"] == ["Reduce risk during high volatility"]
    assert len(json.dumps(prompt)) < 8000  # Bounded prompt.


def test_news_context_unknown_is_never_clearance():
    assert NewsContext.from_window(None).risk_level(MOMENT) == "high"
    upcoming = NewsContext("clear", None, ({"impact": "high", "minutes_until": 20},))
    assert upcoming.risk_level(MOMENT) == "high"
    assert (
        NewsContext("clear", None, ({"impact": "medium", "minutes_until": 90},)).risk_level(MOMENT)
        == "medium"
    )


# -- brain -----------------------------------------------------------------------------------------
async def snapshot_for(market, **kwargs):
    settings, database, clock, broker = market
    kwargs.setdefault("technical_side", Side.BUY)
    kwargs.setdefault("technical_score", 85.0)
    return await MarketAwarenessEngine(broker, settings, clock).snapshot(
        "EURUSD", news=clear_news(), **kwargs
    )


def replaced(snapshot, **changes):
    return dataclasses.replace(snapshot, **changes)


async def test_ai_decision_is_executable_only_at_or_above_threshold(market):
    settings, database, clock, _ = market
    snapshot = await snapshot_for(market)
    journal = DecisionJournal(database, clock)
    high = await AIBrain(
        settings, clock, provider=Provider(decision_json(confidence=85)), journal=journal
    ).decide(snapshot)
    low = await AIBrain(
        settings, clock, provider=Provider(decision_json(confidence=60)), journal=journal
    ).decide(snapshot)
    assert high.source == "ai" and high.executable
    assert low.source == "ai" and not low.executable and "confidence_below_threshold" in low.reasons
    rows = journal.recent(kind="entry")
    assert [r["confidence"] for r in rows] == [60.0, 85.0]
    assert rows[1]["adjustments"]["suggested_risk_percent"] == 0.3
    assert rows[1]["input_hash"] and rows[0]["rejection_reason"].startswith("confidence_below_threshold")


@pytest.mark.parametrize(
    "provider",
    [
        Provider("not json"),
        Provider(decision_json(action="all_in")),
        Provider(decision_json(), error=httpx.ConnectError("offline")),
        Provider(decision_json(), delay=2.0),
    ],
    ids=["invalid-json", "invalid-action", "transport-error", "timeout"],
)
async def test_ai_failure_falls_back_to_the_technical_score_without_blocking(market, provider):
    settings, database, clock, _ = market
    settings = settings.model_copy(update={"ai_timeout_seconds": 1})
    snapshot = await snapshot_for(market)
    brain = AIBrain(settings, clock, provider=provider)
    result = await brain.decide(snapshot)
    assert result.source == "rule_fallback" and result.model == "technical-score-v1"
    assert result.failure is not None
    assert result.decision.action == "open_buy" and result.decision.confidence == 85.0
    assert result.executable  # Outage does not block trading: technical score >= fallback minimum.


async def test_rule_fallback_confidence_is_exactly_the_technical_score(market):
    settings, *_ = market
    snapshot = await snapshot_for(market, technical_side=Side.BUY, technical_score=84.0)
    decision = technical_fallback(snapshot, settings)
    assert decision.action == "open_buy" and decision.confidence == 84.0
    weak = technical_fallback(
        await snapshot_for(market, technical_side=Side.BUY, technical_score=72.0), settings
    )
    assert weak.action == "wait"


async def test_three_failures_switch_to_rule_mode_and_notify_owner_once(market):
    settings, database, clock, _ = market
    notes, provider = Notes(), Provider("broken")
    brain = AIBrain(
        settings.model_copy(update={"ai_decision_cache_seconds": 0}), clock, provider=provider, notifier=notes
    )
    snapshot = await snapshot_for(market)
    for _ in range(5):
        result = await brain.decide(snapshot)
        assert result.source == "rule_fallback"
    assert provider.calls == 3  # Circuit open: no more provider calls, no request storm.
    assert brain.mode == "rule"
    assert [e for e in notes.events if e[0] == "open"] == [("open", 3)]


async def test_identical_market_state_is_served_from_cache(market):
    settings, database, clock, _ = market
    provider = Provider(decision_json())
    brain = AIBrain(settings, clock, provider=provider)
    snapshot = await snapshot_for(market)
    first, second = await brain.decide(snapshot), await brain.decide(snapshot)
    assert (first.source, second.source, provider.calls) == ("ai", "cache", 1)


async def test_entry_gate_enforces_news_spread_open_positions_and_daily_trades(market):
    settings, database, clock, _ = market
    brain = AIBrain(settings, clock, provider=Provider(decision_json()))
    snapshot = await snapshot_for(market)
    decision = decode("decision", decision_json())
    assert brain.gate(decision, snapshot) == ()
    assert "max_daily_trades_reached" in brain.gate(
        decision, snapshot, trades_today=settings.max_daily_trades
    )
    blocked = replaced(snapshot, news={"state": "unknown", "risk": "high"})
    assert {"news_unknown_or_blocked", "high_news_risk"} <= set(brain.gate(decision, blocked))
    wide = replaced(snapshot, quote={**snapshot.quote, "spread_points": 10**6})
    assert "spread_above_limit" in brain.gate(decision, wide)
    full = replaced(snapshot, positions=({},) * 3)
    assert "max_open_positions_reached" in brain.gate(decision, full)
    against = replaced(snapshot, technical={"side": "sell", "score": 90.0})
    assert "ai_disagrees_with_technical_side" in brain.gate(decision, against)


async def test_prompt_marks_news_as_untrusted_and_requests_json(market):
    settings, database, clock, _ = market
    provider = Provider(decision_json())
    await AIBrain(settings, clock, provider=provider).decide(await snapshot_for(market))
    system, user = provider.messages[0]
    assert "untrusted" in system["content"] and "JSON" in user["content"]
    assert "TEST_ONLY_NEVER_CONTACT_TELEGRAM" not in json.dumps(provider.messages)


# -- config adjuster -------------------------------------------------------------------------------
def adjuster(market, **kwargs):
    settings, database, clock, _ = market
    return AIConfigAdjuster(database, settings, clock, **kwargs)


def audit_actions(database):
    with database.session() as session:
        return [row.action for row in session.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_minor_risk_change_auto_applies_inside_owner_ceiling(market):
    settings, database, *_ = market
    layer = adjuster(market)
    result = layer.propose("risk_percent_per_trade", 0.4, reason="volatility rising")
    assert (result.classification, result.status) == ("minor", "applied")
    assert layer.effective()["risk_percent_per_trade"] == Decimal("0.4")
    assert "ai.config_auto_applied" in audit_actions(database)


def test_major_changes_wait_for_owner_and_effective_values_never_exceed_owner_caps(market):
    settings, database, clock, _ = market
    profile = RuntimeProfile.current(settings, SourceKind.SYNTHETIC)
    store = SuggestionStore(database, settings, clock, profile)
    layer = adjuster(market, suggestions=store)
    result = layer.propose("max_daily_trades", 5, reason="ranging market")
    assert (result.classification, result.status) == ("major", "pending") and result.suggestion_id
    assert "max_daily_trades" not in layer.effective()
    store.decide(result.suggestion_id, owner_id=OWNER, approve=True)
    assert layer.sync_owner_decisions() == 1
    assert layer.effective()["max_daily_trades"] == 5
    raised = layer.propose("risk_percent_per_trade", 1.0, reason="strong trend")
    layer.decide(raised.overlay_id, owner_id=OWNER, approve=True)
    assert layer.effective()["risk_percent_per_trade"] == settings.effective_risk_percent  # Owner cap wins.
    with pytest.raises(TradingDisabled):
        store.apply(result.suggestion_id, owner_id=OWNER)  # Never a stopped settings projection.


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("risk_percent_per_trade", 1.5),
        ("risk_percent_per_trade", 0.05),
        ("target_profit_per_trade", 25),
        ("max_daily_trades", 16),
        ("max_spread_points", 60),
        ("skip_trailing_levels", [45]),
        ("symbols_to_trade", ["BTCUSD"]),
        ("strategy_weights", {"trend": 0.9, "mean_reversion": 0.9, "breakout": 0, "momentum": 0}),
    ],
)
def test_out_of_bounds_suggestions_are_rejected_and_audited(market, parameter, value):
    settings, database, *_ = market
    result = adjuster(market).propose(parameter, value, reason="x")
    assert result.status == "rejected" and result.classification == "invalid"
    assert "ai.config_out_of_bounds" in audit_actions(database)


@pytest.mark.parametrize(
    "parameter",
    ["max_open_positions", "kill_switch", "live_trading", "max_daily_loss_percent", "max_drawdown_percent"],
)
def test_hard_limit_parameters_can_never_be_changed_by_ai(market, parameter):
    settings, database, *_ = market
    layer = adjuster(market)
    result = layer.propose(parameter, 10, reason="AI wants more")
    assert result.classification == "forbidden" and result.status == "rejected"
    assert "ai.config_forbidden" in audit_actions(database)
    assert layer.effective()["max_open_positions"] <= ABSOLUTE_MAX_OPEN_POSITIONS == 3


def test_hard_limit_table_matches_the_owner_bounds():
    assert {k: (str(a), str(b)) for k, (a, b) in HARD_LIMITS.items()} == {
        "risk_percent_per_trade": ("0.1", "1.0"),
        "target_profit_per_trade": ("1", "20"),
        "max_daily_trades": ("3", "15"),
        "max_spread_points": ("10", "50"),
    }


async def test_ai_overlay_symbols_and_daily_trades_feed_the_entry_gate(market):
    settings, database, clock, _ = market
    profile = RuntimeProfile.current(settings, SourceKind.SYNTHETIC)
    layer = adjuster(market, suggestions=SuggestionStore(database, settings, clock, profile))
    pending = layer.propose("symbols_to_trade", ["XAUUSD"], reason="EURUSD too quiet")
    layer.decide(pending.overlay_id, owner_id=OWNER, approve=True)
    brain = AIBrain(settings, clock, provider=Provider(decision_json()), adjuster=layer)
    reasons = brain.gate(decode("decision", decision_json()), await snapshot_for(market))
    assert "symbol_disabled_by_ai_overlay" in reasons


async def test_pause_recommendation_is_an_entry_veto_window_not_an_owner_pause(market):
    settings, database, clock, _ = market
    reply = json.dumps(
        {
            "risk_percent_per_trade": None,
            "target_profit_per_trade": None,
            "max_daily_trades": None,
            "max_spread_points": None,
            "skip_trailing_levels": [],
            "strategy_weights": None,
            "symbols_to_trade": None,
            "pause_recommended": True,
            "confidence": 90,
            "reason": "FOMC in 20 minutes",
        }
    )
    brain = AIBrain(settings, clock, provider=Provider(reply))
    snapshot = await snapshot_for(market)
    suggestion, _ = await brain.review_config(snapshot)
    assert suggestion.pause_recommended
    assert "ai_pause_advisory_active" in brain.gate(decode("decision", decision_json()), snapshot)
    assert not any(
        a.startswith(("owner.", "control.")) for a in audit_actions(database)
    )  # Owner state untouched.


# -- reviewer on the real signal pipeline ----------------------------------------------------------
async def test_ai_first_reviewer_approves_only_matching_confident_ai_and_journals_it(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, ai_queue_min_interval_ms=0)
    try:
        proposal = await signals.analyze("EURUSD")
        assert proposal.state == "pending" and proposal.side is not None
        side = proposal.side.value.lower()
        journal = DecisionJournal(execution.database, signals.clock)
        brain = AIBrain(
            execution.settings,
            signals.clock,
            provider=Provider(decision_json(action="open_" + side)),
            journal=journal,
        )
        awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
        reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
        window = news(signals.clock)
        review = await reviewer.review(proposal, window)
        assert review is not None and review.decision == "approve" and review.provider == "openai"
        final = await signals.finalize(proposal.signal_id, review=review, news=window)
        assert final.state == "approved"
        row = journal.recent(kind="entry")[0]
        assert row["final_action"] == "approved_to_risk_engine" and row["source"] == "ai"
        journal.link_signal(
            proposal.signal_id, executed=True, position_id=777, final_action="execution_filled"
        )
        assert journal.recent(kind="entry")[0]["position_id"] == 777
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_ai_first_reviewer_valid_wait_is_final_and_never_shopped_to_rules(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, ai_queue_min_interval_ms=0)
    try:
        proposal = await signals.analyze("EURUSD")
        brain = AIBrain(execution.settings, signals.clock, provider=Provider(decision_json(action="wait")))
        awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
        reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
        window = news(signals.clock)
        review = await reviewer.review(proposal, window)
        assert review.decision == "wait"
        assert (await signals.finalize(proposal.signal_id, review=review, news=window)).state == "rejected"
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_ai_first_reviewer_outage_uses_the_canonical_rule_fallback(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, ai_queue_min_interval_ms=0)
    try:
        proposal = await signals.analyze("EURUSD")
        brain = AIBrain(
            execution.settings, signals.clock, provider=Provider("x", error=httpx.ReadTimeout("t"))
        )
        awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
        reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
        review = await reviewer.review(proposal, news(signals.clock))
        assert review.provider == "rule_fallback" and review.confidence == float(proposal.score)
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_disabled_provider_is_a_veto_not_a_rule_approval(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, ai_provider="disabled")
    try:
        proposal = await signals.analyze("EURUSD")
        brain = AIBrain(execution.settings, signals.clock, provider=None)
        awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
        reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
        assert await reviewer.review(proposal, news(signals.clock)) is None
    finally:
        await execution.shutdown()
        execution.database.close()


# -- learning loop ---------------------------------------------------------------------------------
async def closed_trade(tmp_path):
    engine = await make_engine(tmp_path)
    await open_one(engine)
    position = (await engine.broker.get_positions())[0]
    await engine.close_owned(position.ticket, position.identifier)
    await engine.reconcile()
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.close_time is not None
        return engine, trade.id, position.identifier


async def test_learning_loop_logs_lessons_and_attaches_outcomes_without_ai(tmp_path):
    engine, trade_id, position_id = await closed_trade(tmp_path)
    try:
        journal = DecisionJournal(engine.database, engine.clock)
        journal.record(kind="trailing", source="ai", action="hold_to_60", reason="x", position_id=position_id)
        loop = LearningLoop(engine.database, engine.settings, engine.clock, journal)
        report = await loop.on_trade_closed(trade_id)
        assert report.source == "deterministic" and report.lessons
        assert set(report.review) >= {"entry_timing", "risk_appropriate", "news_effect", "trailing_effective"}
        with engine.database.session() as session:
            rows = session.scalars(
                select(AIDecisionJournal).where(AIDecisionJournal.position_id == position_id)
            ).all()
            assert any(r.kind == "lesson" for r in rows)
            assert all(r.outcome_account is not None for r in rows)
        assert journal.lessons()[0] in report.lessons
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_learning_loop_uses_ai_review_when_available(tmp_path):
    engine, trade_id, _ = await closed_trade(tmp_path)
    try:
        reply = json.dumps(
            {
                "entry_timing": "early",
                "risk_appropriate": True,
                "news_effect": "none",
                "trailing_effective": False,
                "lessons": ["Wait for M15 confirmation before entering"],
                "confidence": 77,
                "reason": "entered before the breakout closed",
            }
        )
        brain = AIBrain(
            engine.settings, engine.clock, provider=Provider(reply), deep_provider=Provider(reply)
        )
        loop = LearningLoop(
            engine.database,
            engine.settings,
            engine.clock,
            DecisionJournal(engine.database, engine.clock),
            brain=brain,
        )
        report = await loop.on_trade_closed(trade_id, deep=True)
        assert report.source == "ai" and report.lessons == ("Wait for M15 confirmation before entering",)
    finally:
        await engine.shutdown()
        engine.database.close()


def test_deterministic_lessons_cover_the_owner_examples():
    base = dict(
        profit_usd=Decimal("-3"),
        initial_risk_usd=Decimal("5"),
        lock_level=0.0,
        minutes_open=40,
        regime="volatile",
        news_risk_at_entry="high",
    )
    lessons = LearningLoop.deterministic_review(base).lessons
    assert "Reduce risk during high volatility" in lessons
    assert "Avoid trading 30 min before high-impact news" in lessons
    trending = LearningLoop.deterministic_review(
        {
            **base,
            "profit_usd": Decimal("6"),
            "lock_level": 90.0,
            "regime": "trending",
            "news_risk_at_entry": "low",
        }
    ).lessons
    assert "Increase target profit for trending markets" in trending
    ranging = LearningLoop.deterministic_review(
        {**base, "regime": "ranging", "news_risk_at_entry": "low"}
    ).lessons
    assert "Reduce max trades during ranging markets" in ranging


def test_journal_stats_measure_ai_decision_quality(market):
    settings, database, clock, _ = market
    journal = DecisionJournal(database, clock)
    for position_id, (source, outcome) in enumerate((("ai", "4"), ("ai", "-2"), ("rule_fallback", "1")), 1):
        entry = journal.record(
            kind="entry", source=source, action="open_buy", reason="x", confidence=80, position_id=position_id
        )
        journal.mark(entry.id, executed=True, final_action="execution_filled")
        journal.record_outcome(position_id, outcome_account=Decimal(outcome), outcome_usd=Decimal(outcome))
    journal.record(kind="entry", source="ai", action="wait", reason="x", confidence=40)
    stats = journal.stats()
    ai_group = next(g for g in stats["groups"] if g["source"] == "ai")
    assert (ai_group["decisions"], ai_group["executed"]) == (3, 2)
    assert stats["entry_performance"]["ai"] == {"trades": 2, "win_rate": 50.0, "net_usd": "2"}
    assert stats["entry_performance"]["rule_fallback"]["trades"] == 1
