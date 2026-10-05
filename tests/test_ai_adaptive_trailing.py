"""Lock-first AI-adaptive trailing on the PAPER/MOCK execution engine only (no broker, no orders).

Prices (EURUSD BUY from tests.risk_helpers.open_one): bid 1.10165 earns the 30 % lock,
1.10250 the 60 % lock and 1.10280 the 90 % lock (see tests/test_trailing_engine.py).
"""

import asyncio
import json
import logging
from decimal import Decimal

import pytest
from sqlalchemy import select

from ai.ai_brain import AIBrain
from ai.ai_first_schemas import TrailingDecision
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from ai.ollama_client import ProviderContent
from core.models import Trade
from tests.risk_helpers import D, make_engine, open_one
from trading.ai_adaptive_trailing import MECHANICAL_WARNING, AdaptiveTrailing
from trading.position_manager import PositionManager
from trading.trailing_engine import LockRegressionError, assert_never_loosens
from trading.types import ResultStatus, Side


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path, ai_trailing_timeout_seconds=0.5, ai_circuit_failures=5)
    await open_one(result)
    yield result
    await result.shutdown()
    result.database.close()


async def quote(engine, bid):
    await engine.broker.set_tick("EURUSD", D(bid), D(bid) + D("0.00012"))
    await engine.reconcile()


async def position_of(engine):
    positions = await engine.broker.get_positions()
    return positions[0] if positions else None


def lock_level(engine):
    with engine.database.session() as session:
        return session.scalar(select(Trade)).profit_lock_level


class ScriptedBrain:
    """Deterministic AI double. Records WHEN it was asked and what the stop was at that moment."""

    def __init__(self, engine, answers):
        self.engine, self.answers, self.calls = engine, list(answers), []

    async def trailing_advice(self, context, *, threshold, timeout=None):
        position = await position_of(self.engine)
        self.calls.append(
            {"threshold": threshold, "sl_at_call": position.sl, "lock_at_call": lock_level(self.engine)}
        )
        answer = self.answers.pop(0)
        if answer is None:
            return None, None, "AIUnavailable"
        return (
            TrailingDecision(decision=answer, confidence=90, reason=f"scripted {answer}"),
            "test-model",
            None,
        )


class TextProvider:
    """OpenAI-compatible provider double returning fixed text (or sleeping past the timeout)."""

    configured = True

    def __init__(self, text=None, *, delay=0.0):
        self.text, self.delay, self.calls = text, delay, 0

    async def complete(self, messages, schema):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return ProviderContent("openai", "qwen/qwen3.8-27b", self.text)


def adaptive(engine, brain):
    journal = DecisionJournal(engine.database, engine.clock)
    return AdaptiveTrailing(engine, brain=brain, journal=journal), journal


def journal_rows(engine):
    with engine.database.session() as session:
        return [
            (r.threshold_reached, r.source, r.action, r.final_action, r.position_id, r.confidence, r.reason)
            for r in session.scalars(
                select(AIDecisionJournal)
                .where(AIDecisionJournal.kind == "trailing")
                .order_by(AIDecisionJournal.id)
            )
        ]


# 1 ---------------------------------------------------------------------------------------------
async def test_30_percent_lock_is_secured_before_the_ai_is_called(engine):
    brain = ScriptedBrain(engine, ["hold_to_60"])
    layer, _ = adaptive(engine, brain)
    order = []
    protect = engine.protect_sl

    async def spy_protect(*args, **kwargs):
        order.append("mechanical_lock")
        return await protect(*args, **kwargs)

    engine.protect_sl = spy_protect
    original = brain.trailing_advice

    async def spy_ai(*args, **kwargs):
        order.append("ai")
        return await original(*args, **kwargs)

    brain.trailing_advice = spy_ai
    await quote(engine, "1.10165")
    before = await position_of(engine)
    owned = engine.logger.owned(engine.account_key)[0]
    plan = await engine.trailing.plan(before, owned)
    assert plan is not None and plan.lock_level == 30

    outcomes = await layer.manage(before, owned)

    assert order == ["mechanical_lock", "ai"]
    call = brain.calls[0]
    assert call["threshold"] == 30
    assert call["lock_at_call"] == 30  # Durably locked BEFORE the AI saw anything.
    assert call["sl_at_call"] == plan.sl > before.sl
    assert [o["operation"] for o in outcomes] == ["sl", "ai_trailing"]


async def test_rejected_lock_means_the_ai_is_never_consulted(engine):
    brain = ScriptedBrain(engine, ["close_now"])
    layer, _ = adaptive(engine, brain)
    protect = engine.protect_sl

    async def always_rejected(ticket, identifier, sl, **kwargs):
        from dataclasses import replace

        result = await protect(ticket, identifier, sl, **kwargs)
        return replace(result, status=ResultStatus.REJECTED)

    engine.protect_sl = always_rejected
    await quote(engine, "1.10165")
    await layer.manage(await position_of(engine), engine.logger.owned(engine.account_key)[0])
    assert brain.calls == []


# 2 ---------------------------------------------------------------------------------------------
async def test_ai_unavailable_mechanical_trailing_continues_30_60_90(engine, caplog):
    brain = AIBrain(engine.settings, engine.clock, provider=None)  # No provider: rule mode.
    layer, _ = adaptive(engine, brain)
    manager = PositionManager(engine, adaptive=layer)
    caplog.set_level(logging.WARNING, logger="trading.ai_adaptive_trailing")
    for bid, level in (("1.10165", 30), ("1.10250", 60), ("1.10280", 90)):
        await quote(engine, bid)
        await manager.cycle()
        assert lock_level(engine) == level
    assert await position_of(engine) is not None  # Still open; nothing closed by a missing AI.
    rows = journal_rows(engine)
    assert [r[0] for r in rows] == [30, 60, 90]
    assert all(r[1] == "mechanical" and r[3] == "mechanical_continue" for r in rows)
    assert MECHANICAL_WARNING in caplog.text


# 3 ---------------------------------------------------------------------------------------------
async def test_close_now_at_60_closes_and_keeps_the_locked_profit(engine):
    brain = ScriptedBrain(engine, ["hold_to_60", "close_now"])
    layer, journal = adaptive(engine, brain)
    manager = PositionManager(engine, adaptive=layer)
    await quote(engine, "1.10165")
    await manager.cycle()
    await quote(engine, "1.10250")
    owned = engine.logger.owned(engine.account_key)[0]
    target = owned.target_usd
    await manager.cycle()
    assert [c["threshold"] for c in brain.calls] == [30, 60]
    assert brain.calls[1]["lock_at_call"] == 60
    await engine.reconcile()
    assert await position_of(engine) is None
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.status == "closed"
        assert trade.profit_lock_level == 60
        assert Decimal(trade.profit_usd) >= target * Decimal("0.6")  # Locked 60 % kept.
        realized = Decimal(trade.profit)
    assert journal_rows(engine)[-1][3] == "closed_with_locked_profit"
    assert journal.record_outcome(owned.identifier, outcome_account=realized) == 2
    with engine.database.session() as session:
        assert all(
            r.outcome_account == str(realized)
            for r in session.scalars(
                select(AIDecisionJournal).where(AIDecisionJournal.position_id == owned.identifier)
            )
        )


# 4 ---------------------------------------------------------------------------------------------
async def test_hold_to_90_holds_through_60_until_90_or_reversal(engine):
    brain = ScriptedBrain(engine, ["hold_to_90", "close_now"])
    layer, _ = adaptive(engine, brain)
    manager = PositionManager(engine, adaptive=layer)
    await quote(engine, "1.10165")
    await manager.cycle()
    await quote(engine, "1.10250")
    await manager.cycle()
    assert lock_level(engine) == 60  # Mechanical 60 lock still placed...
    assert len(brain.calls) == 1  # ...but the AI is not re-asked: it said hold to 90.
    locked_60 = (await position_of(engine)).sl
    # Reversal: price retreats; the AI path does nothing and the 60 lock never loosens.
    await quote(engine, "1.10215")
    await manager.cycle()
    position = await position_of(engine)
    assert position is not None and position.sl == locked_60 and len(brain.calls) == 1
    # Resumes to 90: mechanical 90 lock first, THEN the AI is consulted again.
    await quote(engine, "1.10280")
    await manager.cycle()
    assert [c["threshold"] for c in brain.calls] == [30, 90]
    assert brain.calls[1]["lock_at_call"] == 90
    assert [r[3] for r in journal_rows(engine)] == ["hold_to_90", "hold", "closed_with_locked_profit"]


# 5 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider",
    [
        TextProvider("this is not json"),
        TextProvider(json.dumps({"decision": "close_now", "confidence": 90})),  # Missing reason.
        TextProvider(json.dumps({"decision": "remove_lock", "confidence": 99, "reason": "x"})),
        TextProvider(
            json.dumps({"decision": "extend_tp_to_120", "confidence": 99, "reason": "x"})
        ),  # Not at 30.
        TextProvider(json.dumps({"decision": "close_now", "confidence": 90, "reason": "slow"}), delay=2.0),
    ],
    ids=["not-json", "missing-field", "unknown-decision", "not-allowed-at-30", "slower-than-timeout"],
)
async def test_invalid_or_slow_ai_falls_back_to_mechanical_rules(engine, provider):
    brain = AIBrain(engine.settings, engine.clock, provider=provider)
    layer, _ = adaptive(engine, brain)
    await quote(engine, "1.10165")
    position = await position_of(engine)
    await layer.manage(position, engine.logger.owned(engine.account_key)[0])
    assert provider.calls == 1
    assert lock_level(engine) == 30
    assert await position_of(engine) is not None  # Not closed by an invalid/slow reply.
    row = journal_rows(engine)[-1]
    assert row[1] == "mechanical" and row[3] == "mechanical_continue"
    assert row[6].startswith(MECHANICAL_WARNING)


async def test_valid_ai_reply_through_the_real_brain_is_executed(engine):
    provider = TextProvider(
        json.dumps({"decision": "close_now", "confidence": 88, "reason": "momentum fading"})
    )
    brain = AIBrain(engine.settings, engine.clock, provider=provider)
    layer, _ = adaptive(engine, brain)
    await quote(engine, "1.10165")
    await layer.manage(await position_of(engine), engine.logger.owned(engine.account_key)[0])
    await engine.reconcile()
    assert await position_of(engine) is None
    assert journal_rows(engine)[-1][1:4] == ("ai", "close_now", "closed_with_locked_profit")


# 6 ---------------------------------------------------------------------------------------------
async def test_ai_can_never_remove_or_loosen_an_existing_lock(engine):
    brain = ScriptedBrain(engine, ["hold_to_60"])
    layer, _ = adaptive(engine, brain)
    await quote(engine, "1.10165")
    await layer.manage(await position_of(engine), engine.logger.owned(engine.account_key)[0])
    locked = await position_of(engine)
    assert lock_level(engine) == 30
    for attempt in (locked.sl - D("0.00010"), D("0"), locked.entry_price):
        with pytest.raises(LockRegressionError):
            await layer.apply_ai_stop(locked, attempt, lock_level=0)
    after = await position_of(engine)
    assert after.sl == locked.sl and lock_level(engine) == 30


async def test_tighten_lock_only_ever_moves_the_stop_closer_to_price(engine):
    brain = ScriptedBrain(engine, ["tighten_lock"])
    layer, _ = adaptive(engine, brain)
    await quote(engine, "1.10280")
    before = await position_of(engine)
    await layer.manage(before, engine.logger.owned(engine.account_key)[0])
    after = await position_of(engine)
    assert lock_level(engine) == 90
    assert journal_rows(engine)[-1][3] in {"lock_tightened", "tighten_not_possible_lock_kept"}
    assert after.sl >= brain.calls[0]["sl_at_call"] > before.sl


def test_lock_invariant_is_side_aware():
    assert_never_loosens(Side.BUY, D("1.1000"), D("1.1001"))
    assert_never_loosens(Side.SELL, D("1.1000"), D("1.0999"))
    assert_never_loosens(Side.BUY, D("0"), D("1.0"))  # No lock yet: anything protective is fine.
    for side, proposed in ((Side.BUY, D("1.0999")), (Side.SELL, D("1.1001")), (Side.BUY, D("0"))):
        with pytest.raises(LockRegressionError):
            assert_never_loosens(side, D("1.1000"), proposed)


async def test_thresholds_are_consulted_once_even_after_a_restart(engine):
    brain = ScriptedBrain(engine, ["hold_to_60"])
    layer, journal = adaptive(engine, brain)
    await quote(engine, "1.10165")
    await layer.manage(await position_of(engine), engine.logger.owned(engine.account_key)[0])
    restarted = AdaptiveTrailing(engine, brain=brain, journal=journal)  # Fresh in-memory state.
    await restarted.manage(await position_of(engine), engine.logger.owned(engine.account_key)[0])
    assert len(brain.calls) == 1
