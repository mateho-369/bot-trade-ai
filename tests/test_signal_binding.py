"""TEST ONLY native-tagged read DTOs; synthetic histories, no native SDK/orders.

These evaluation fixtures cannot qualify as backtest/paper/demo promotion evidence.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.models import RiskState, Signal
from scripts.synthetic_news_fixtures import news_fixture_settings
from strategy.signal_engine import SignalEngine
from tests.news_helpers import publish_contract_news
from tests.risk_helpers import OWNER
from tests.signal_helpers import make_signal_runtime, review
from trading.authorization import BrokerSnapshot
from trading.risk_engine import RiskEngine
from trading.risk_types import RuntimeProfile
from trading.types import BrokerCommand, Operation, SourceKind


class TestNativeTaggedReader:
    __test__ = False
    source_kind = SourceKind.MT5

    def __init__(self, synthetic):
        self.synthetic = synthetic

    def __getattr__(self, name):
        return getattr(self.synthetic, name)


@pytest.fixture
async def native_sample(tmp_path):
    synthetic, execution = await make_signal_runtime(tmp_path, **news_fixture_settings(("EURUSD",)))
    profile = RuntimeProfile(execution.profile.code_hash, execution.profile.model_sha256, SourceKind.MT5)
    signals = SignalEngine(
        TestNativeTaggedReader(execution.broker.market),
        execution.database,
        execution.settings,
        profile=profile,
    )
    await signals.initialize()
    headlines = publish_contract_news(execution.database, execution.settings, signals.clock, profile)
    proposal = await signals.analyze("EURUSD")
    # Hand-authored ollama label, not an HTTP call, real model or provider evaluation.
    ready = await signals.finalize(
        proposal.signal_id,
        review=review(
            signals,
            proposal,
            provider="ollama",
            observed_at=signals.clock.now() - timedelta(seconds=2),
            news_hash=headlines["EURUSD"].evidence_hash,
        ),
        news=headlines["EURUSD"],
    )
    assert ready.approved, ready.reasons
    execution.control.resume(OWNER, account_key=execution.account_key)
    plan = await execution.calculator.plan_market_order(
        "EURUSD", ready.side, ready.stop_price, strategy="weighted_router_v1"
    )
    snapshot = BrokerSnapshot(
        await execution.broker.get_account_info(),
        await execution.broker.get_symbol_info("EURUSD"),
        await execution.broker.get_tick("EURUSD"),
        (),
        plan.worst_loss_account,
        plan.margin_account,
        plan.expected_net_profit_usd,
        execution.clock.now(),
        (),
        Decimal("1"),
        Decimal("1"),
        SourceKind.MT5,
        True,
    )
    command = BrokerCommand(
        Operation.OPEN, plan.order.idempotency_key, plan.order.created_at, order=plan.order
    )
    yield signals, execution, ready, command, snapshot
    await execution.shutdown()
    execution.database.close()


def evaluate(sample, *, context=None, command=None, snapshot=None):
    signals, execution, ready, original_command, original_snapshot = sample
    risk = RiskEngine(execution.database, execution.settings, execution.clock, signals.profile)
    with execution.database.session() as session:
        row = session.scalar(select(RiskState))
        return risk.evaluate(
            session, command or original_command, snapshot or original_snapshot, context or ready.context, row
        )


def test_generated_native_tagged_signal_matches_existing_risk_contract(native_sample):
    result = evaluate(native_sample)
    assert result.approved and not result.reasons
    assert native_sample[2].context.source == SourceKind.MT5
    # This is risk evaluation only; stage/owner/broker/native submission is NOT exercised.


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("stop", "strategy_stop_changed"),
        ("drift", "strategy_entry_price_chasing"),
        ("spread", "strategy_spread_to_atr"),
        ("strategy", "unbound_persisted_signal"),
        ("digest", "unbound_persisted_signal"),
        ("format", "unbound_persisted_signal"),
        ("proposal", "unbound_strategy_proposal"),
        ("review", "unbound_strategy_proposal"),
    ],
)
def test_native_generated_proposal_revalidates_each_bound_input(native_sample, fault, reason):
    signals, execution, ready, command, snapshot = native_sample
    context = ready.context
    if fault == "stop":
        command = replace(command, order=replace(command.order, sl=command.order.sl + Decimal("0.00001")))
    elif fault == "strategy":
        command = replace(command, order=replace(command.order, strategy="different"))
    elif fault == "drift":
        snapshot = replace(
            snapshot, tick=replace(snapshot.tick, bid=Decimal("1.1015"), ask=Decimal("1.10162"))
        )
    elif fault == "spread":
        snapshot = replace(snapshot, tick=replace(snapshot.tick, ask=snapshot.tick.bid + Decimal("0.0002")))
    elif fault == "digest":
        context = replace(context, features={"different": True})
    else:
        with execution.database.session() as session:
            row = session.get(Signal, ready.signal_id)
            payload = dict(row.features_json)
            if fault == "format":
                payload.pop("signal_format")
            elif fault == "proposal":
                payload["stop_price"] = "1.09800"
            elif fault == "review":
                payload["ai_review"] = {}
            row.features_json = payload
    result = evaluate(native_sample, context=context, command=command, snapshot=snapshot)
    assert not result.approved and reason in result.reasons


def test_review_time_is_rechecked_pre_send_even_if_context_has_two_second_skew_allowance(native_sample):
    signals, execution, ready, command, snapshot = native_sample
    # Original review is t0-2 (accepted clock precision allowance). At t0+29 the
    # context is still fresh, but the separately bound review is 31 seconds old.
    execution.clock.advance(timedelta(seconds=29))
    fresh = replace(
        snapshot, observed_at=execution.clock.now(), tick=replace(snapshot.tick, time=execution.clock.now())
    )
    result = evaluate(native_sample, snapshot=fresh)
    assert not result.approved and "stale_bound_ai_review" in result.reasons
    assert "stale_signal" not in result.reasons and "stale_broker_snapshot" not in result.reasons


async def test_test_provider_cannot_approve_native_tagged_context(tmp_path):
    synthetic, execution = await make_signal_runtime(tmp_path, **news_fixture_settings(("EURUSD",)))
    try:
        profile = RuntimeProfile(execution.profile.code_hash, execution.profile.model_sha256, SourceKind.MT5)
        signals = SignalEngine(
            TestNativeTaggedReader(execution.broker.market),
            execution.database,
            execution.settings,
            profile=profile,
        )
        await signals.initialize()
        headlines = publish_contract_news(execution.database, execution.settings, signals.clock, profile)
        proposal = await signals.analyze("EURUSD")
        result = await signals.finalize(
            proposal.signal_id,
            review=review(signals, proposal, news_hash=headlines["EURUSD"].evidence_hash),
            news=headlines["EURUSD"],
        )
        assert not result.approved and "unbound_or_stale_ai_review" in result.reasons
    finally:
        await execution.shutdown()
        execution.database.close()
