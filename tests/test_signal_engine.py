"""Synthetic pipeline/review regressions; no AI/news provider or native SDK call."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.models import AuditLog, OrderIntent, Signal
from strategy.signal_engine import SignalEngine
from tests.signal_helpers import TestReviewer, approved, make_signal_runtime, news, review
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, SourceKind, TradingDisabled


@pytest.fixture
async def runtime(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    yield signals, execution
    await execution.shutdown()
    execution.database.close()


async def test_analysis_persists_pending_but_cannot_send_before_reviews(runtime):
    signals, execution = runtime
    proposal = await signals.analyze("EURUSD")
    assert proposal.state == "pending" and not proposal.approved and proposal.context is None
    assert proposal.signal_id and proposal.stop_price > 0 and proposal.score >= 70
    with pytest.raises(TradingDisabled):
        await execution.execute_signal(proposal.signal_id)
    assert await execution.broker.get_positions() == ()
    with execution.database.session() as session:
        row = session.get(Signal, proposal.signal_id)
        assert row.final_decision == "pending" and row.ai_score == 0
        assert session.scalar(select(OrderIntent)) is None
        assert any(row.action == "signal.published" for row in session.scalars(select(AuditLog)))


async def test_provider_absence_and_unknown_news_never_fabricate_confidence(runtime):
    signals, execution = runtime
    result = await signals.evaluate("EURUSD")
    assert result.state == "rejected" and result.context is None
    assert "ai_unavailable_or_invalid" in result.reasons and "unknown_stale_or_unsafe_news" in result.reasons
    assert await execution.broker.get_positions() == ()
    with execution.database.session() as session:
        assert session.get(Signal, result.signal_id).ai_score == 0


async def test_complete_bound_review_produces_context_not_owner_permission(runtime):
    signals, execution = runtime
    result = await approved(signals)
    context = result.context
    p = result.payload()
    assert context.signal_id == result.signal_id and context.digest == p["decision_digest"]
    assert context.ai_confidence == 90 and context.signal_score == result.score
    assert context.features["history_hash"] == p["history_hash"]
    assert p["source"] == "synthetic" and p["news_hash"] == "a" * 64
    assert execution.database.status()["state"] == "paused"
    assert await execution.broker.get_positions() == ()


@pytest.mark.parametrize(
    "fault",
    [
        {"confidence": 69},
        {"decision": "reject"},
        {"decision": "wait"},
        {"proposal_hash": "b" * 64},
        {"code_hash": "b" * 64},
        {"model_sha256": "b" * 64},
        {"news_hash": "b" * 64},
        {"source": SourceKind.MT5},
        {"risk_percent": Decimal("0.6")},
        {"risk_percent": Decimal("0.3")},
        {"observed_at": "old"},
        {"observed_at": "future"},
    ],
)
async def test_low_unbound_stale_or_risk_escalating_review_is_final_veto(runtime, fault):
    signals, _ = runtime
    proposal = await signals.analyze("EURUSD")
    changes = dict(fault)
    if changes.get("observed_at") == "old":
        changes["observed_at"] = signals.clock.now() - timedelta(seconds=31)
    if changes.get("observed_at") == "future":
        changes["observed_at"] = signals.clock.now() + timedelta(seconds=3)
    result = await signals.finalize(
        proposal.signal_id, review=review(signals, proposal, **changes), news=news(signals.clock)
    )
    assert result.state == "rejected" and not result.approved and result.context is None
    retried = await signals.finalize(
        proposal.signal_id, review=review(signals, proposal), news=news(signals.clock)
    )
    assert retried.state == "rejected" and retried.payload_json == result.payload_json


@pytest.mark.parametrize(
    "changes",
    [
        {"known": False},
        {"safe": False},
        {"evidence_hash": None},
        {"headlines_fetched_at": "old"},
        {"calendar_fetched_at": "old"},
        {"calendar_covered_until": "old"},
    ],
)
async def test_headlines_alone_or_incomplete_stale_calendar_do_not_call_ai(runtime, changes):
    signals, _ = runtime
    now = signals.clock.now()
    values = dict(changes)
    if values.get("headlines_fetched_at") == "old":
        values["headlines_fetched_at"] = now - timedelta(seconds=901)
    if values.get("calendar_fetched_at") == "old":
        values["calendar_fetched_at"] = now - timedelta(seconds=21601)
    if values.get("calendar_covered_until") == "old":
        values["calendar_covered_until"] = now - timedelta(seconds=1)
    provider = TestReviewer(signals)
    result = await signals.evaluate("EURUSD", reviewer=provider, news=news(signals.clock, **values))
    assert result.state == "rejected" and provider.calls == 0


async def test_pending_review_deadline_is_not_extended_by_poll_or_reanalysis(runtime):
    signals, execution = runtime
    initial = await signals.analyze("EURUSD")
    signals.clock.advance(timedelta(seconds=31))
    duplicate = await signals.analyze("EURUSD")
    assert duplicate.signal_id == initial.signal_id and duplicate.state == "expired"
    final = await signals.finalize(
        initial.signal_id, review=review(signals, initial), news=news(signals.clock)
    )
    assert final.state == "expired"
    with execution.database.session() as session:
        assert session.get(Signal, initial.signal_id).time.isoformat() == initial.payload()["observed_at"]


async def test_concurrent_identical_bar_creates_one_immutable_signal(runtime):
    signals, execution = runtime
    proposals = await asyncio.gather(*(signals.analyze("EURUSD") for _ in range(8)))
    assert len({proposal.signal_id for proposal in proposals}) == 1
    assert len({proposal.proposal_hash for proposal in proposals}) == 1
    ready = await signals.finalize(
        proposals[0].signal_id, review=review(signals, proposals[0]), news=news(signals.clock)
    )
    replacement = await signals.finalize(
        proposals[0].signal_id,
        review=review(signals, proposals[0], decision="reject"),
        news=news(signals.clock),
    )
    assert ready.payload_json == replacement.payload_json
    with execution.database.session() as session:
        assert len(session.scalars(select(Signal)).all()) == 1


async def test_revision_of_consumed_input_revokes_old_signal_never_refreshes_it(runtime):
    signals, execution = runtime
    ready = await approved(signals)
    market = execution.broker.market
    frame = await market.get_candles("EURUSD", "M5", 300, as_of=signals.clock.now())
    frame.loc[290, "close"] += 0.00001
    market.frames["EURUSD", "M5"] = frame
    changed = await signals.analyze("EURUSD")
    assert changed.signal_id == ready.signal_id and changed.state == "revoked"
    assert changed.payload()["history_hash"] == ready.payload()["history_hash"]
    with pytest.raises(TradingDisabled):
        await execution.execute_signal(ready.signal_id)


async def test_changed_tick_valuation_not_technical_history_does_not_revoke(runtime, monkeypatch):
    signals, execution = runtime
    first = await signals.analyze("EURUSD")
    market = execution.broker.market
    original = market.get_symbol_info

    async def different(symbol):
        return replace(await original(symbol), tick_value_profit=Decimal("2"), tick_value_loss=Decimal("2"))

    monkeypatch.setattr(market, "get_symbol_info", different)
    second = await signals.analyze("EURUSD")
    assert second.signal_id == first.signal_id and second.state == "pending"


@pytest.mark.parametrize(
    "field",
    [
        "stop_price",
        "atr",
        "bar_close_price",
        "feature_snapshot",
        "ai_review",
        "decision_context",
        "decision_digest",
    ],
)
async def test_proposal_or_review_corruption_never_becomes_a_fresh_entry(runtime, field):
    signals, execution = runtime
    ready = await approved(signals)
    with execution.database.session() as session:
        row = session.get(Signal, ready.signal_id)
        values = dict(row.features_json)
        if field in {"stop_price", "atr", "bar_close_price"}:
            values[field] = "0.001"
        elif field == "decision_digest":
            values[field] = "b" * 64
        else:
            values[field] = {}
        row.features_json = values
    with pytest.raises((TradingDisabled, BrokerError, KeyError, TypeError)):
        await signals.get(ready.signal_id)
    assert await execution.broker.get_positions() == ()


async def test_symbol_read_failure_does_not_create_fake_closed_bar_or_log_secret(runtime):
    signals, execution = runtime
    execution.broker.market.bad_symbol = "EURUSD"
    result = await signals.analyze("EURUSD")
    assert result.state == "blocked" and result.signal_id is None
    with execution.database.session() as session:
        assert session.scalar(select(Signal)) is None
        logs = str([row.details for row in session.scalars(select(AuditLog))])
        assert "NOT_A_REAL_SECRET" not in logs and "market_data_or_features_unavailable" in logs


async def test_disabled_symbol_is_a_logged_veto(runtime):
    signals, execution = runtime
    result = await signals.analyze("BTCUSD")
    assert result.state == "blocked" and result.signal_id is None
    assert await execution.broker.get_positions() == ()


async def test_async_provider_error_keeps_event_loop_responsive_and_rejects(runtime):
    signals, _ = runtime
    started = asyncio.Event()
    release = asyncio.Event()

    class Broken:
        async def review(self, proposal, news_window):
            started.set()
            await release.wait()
            raise RuntimeError("TEST provider body token=never_log")

    task = asyncio.create_task(signals.evaluate("EURUSD", reviewer=Broken(), news=news(signals.clock)))
    await asyncio.wait_for(started.wait(), 2)
    # This runs while the provider is in flight; no sync wait on the event loop.
    await asyncio.sleep(0)
    release.set()
    result = await task
    assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons


async def test_canceled_provider_leaves_pending_but_never_approves_or_sends(runtime):
    signals, execution = runtime
    started = asyncio.Event()

    class Never:
        async def review(self, proposal, news_window):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(signals.evaluate("EURUSD", reviewer=Never(), news=news(signals.clock)))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with execution.database.session() as session:
        assert session.scalar(select(Signal)).final_decision == "pending"
    assert await execution.broker.get_positions() == ()


async def test_multi_symbol_pipeline_keeps_fault_isolated(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, symbols=("EURUSD", "GBPUSD"))
    try:
        execution.broker.market.bad_symbol = "GBPUSD"
        provider = TestReviewer(signals)
        results = await signals.evaluate_many(
            reviewer=provider,
            news_by_symbol={symbol: news(signals.clock) for symbol in signals.settings.symbols},
        )
        assert results[0].approved and results[1].state == "blocked" and provider.calls == 1
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_auto_reduction_requires_explicit_owner_setting_and_cannot_escalate(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, auto_reduce_risk=True)
    try:
        proposal = await signals.analyze("EURUSD")
        ready = await signals.finalize(
            proposal.signal_id,
            review=review(signals, proposal, risk_percent=Decimal("0.2")),
            news=news(signals.clock),
        )
        assert ready.context.risk_percent == Decimal("0.2")
        assert execution.database.status()["state"] == "paused"
    finally:
        await execution.shutdown()
        execution.database.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": 101},
        {"decision": "buy"},
        {"source": "synthetic"},
        {"risk_percent": 0.1},
        {"risk_percent": Decimal("NaN")},
        {"proposal_hash": "bad"},
    ],
)
async def test_review_contract_is_not_permissive_json(runtime, changes):
    signals, _ = runtime
    proposal = await signals.analyze("EURUSD")
    with pytest.raises(BrokerError):
        review(signals, proposal, **changes)


async def test_profile_cannot_relabel_actual_source(runtime):
    signals, execution = runtime
    fake = RuntimeProfile(signals.profile.code_hash, signals.profile.model_sha256, SourceKind.MT5)
    with pytest.raises(TradingDisabled):
        SignalEngine(execution.broker, execution.database, signals.settings, profile=fake)


async def test_provider_timeout_is_a_final_veto_when_rule_fallback_is_disabled(tmp_path):
    # With the rule-based fallback switched off, a timeout is a veto, never a placeholder approval.
    # The enabled-fallback behaviour is covered in tests/test_rule_fallback.py.
    signals, execution = await make_signal_runtime(
        tmp_path, ai_timeout_seconds=1, ai_rule_fallback_enabled=False
    )
    try:

        class TooSlow:
            async def review(self, proposal, news_window):
                await asyncio.Event().wait()

        result = await signals.evaluate("EURUSD", reviewer=TooSlow(), news=news(signals.clock))
        assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_read_and_feature_work_never_calls_source_write_methods(runtime, monkeypatch):
    signals, execution = runtime

    async def forbidden(*args, **kwargs):
        raise AssertionError("TEST signal publisher called broker write/exposure mutation")

    for name in (
        "open_market_buy",
        "open_market_sell",
        "close_position",
        "modify_sl",
        "modify_tp",
        "get_positions",
    ):
        monkeypatch.setattr(execution.broker, name, forbidden)
    result = await signals.evaluate("EURUSD", reviewer=TestReviewer(signals), news=news(signals.clock))
    assert result.approved and execution.database.status()["state"] == "paused"
