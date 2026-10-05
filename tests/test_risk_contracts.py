from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import OrderIntent, Signal
from tests.risk_helpers import OWNER, D, make_engine, safe_context
from trading.authorization import BrokerSnapshot, PositionRisk
from trading.risk_engine import RiskEngine
from trading.risk_types import DecisionContext, NewsWindow, PositionReview, RuntimeProfile, source_code_hash
from trading.types import BrokerCommand, BrokerError, Operation, Side, SourceKind


@pytest.mark.parametrize(
    "change",
    [
        {"signal_score": float("nan")},
        {"signal_score": True},
        {"ai_confidence": 101},
        {"ai_confidence": "90"},
        {"risk_percent": D("0")},
        {"risk_percent": 0.1},
        {"risk_percent": D("NaN")},
        {"source": "mt5"},
    ],
)
def test_decision_dto_rejects_ambiguous_numeric_and_provenance_fields(tmp_path, change):
    from tests.risk_helpers import MOMENT
    from trading.types import ManualClock

    with pytest.raises(BrokerError):
        safe_context(ManualClock(MOMENT), **change)


@pytest.mark.parametrize("confidence", [True, "90", float("nan"), -1, 101])
def test_review_dto_is_not_a_boolean_or_nan_approval(confidence):
    from tests.risk_helpers import MOMENT

    with pytest.raises(BrokerError):
        PositionReview(MOMENT, SourceKind.SYNTHETIC, confidence)


@pytest.mark.parametrize("field", ["known", "safe"])
def test_news_flags_are_actual_booleans(field):
    with pytest.raises(BrokerError):
        NewsWindow(**{field: 1})


def test_code_hash_changes_for_runnable_code_and_pinned_dependencies_not_notes(tmp_path):
    (tmp_path / "main.py").write_text("print('TEST ONLY')")
    (tmp_path / "config.py").write_text("# TEST")
    (tmp_path / "requirements.txt").write_text("pytest==9.1.1")
    first = source_code_hash(tmp_path)
    (tmp_path / "notes.md").write_text("text")
    assert source_code_hash(tmp_path) == first
    (tmp_path / "requirements.txt").write_text("pytest==9.0.0")
    assert source_code_hash(tmp_path) != first


async def test_features_are_copied_and_durable_context_does_not_mutate_with_caller(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        original = {"nested": {"value": 1}}
        context = safe_context(engine.clock, features=original)
        original["nested"]["value"] = 2
        assert context.features["nested"]["value"] == 1
        roundtrip = DecisionContext.from_dict(context.to_dict())
        assert roundtrip.digest == context.digest
        plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        command = BrokerCommand(
            Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order
        )
        engine.authority.stage(
            command,
            await engine.broker.get_account_info(),
            context=context,
            target_usd=plan.target_profit_usd,
        )
        context.features["nested"]["value"] = 3
        with engine.database.session() as session:
            assert session.scalar(select(OrderIntent)).request["context"]["features"]["nested"]["value"] == 1
    finally:
        await engine.shutdown()
        engine.database.close()


@pytest.mark.parametrize("fault", ["strategy", "code", "model", "digest", "source", "news", "bar", "score"])
async def test_native_signal_binding_rejects_changed_persisted_inputs(tmp_path, fault):
    # Evaluation-only handcrafted native tags; no native client/execution is used.
    engine = await make_engine(tmp_path)
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
        risk = RiskEngine(engine.database, engine.settings, engine.clock, profile)
        plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        command = BrokerCommand(
            Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order
        )
        with engine.database.session() as session:
            signal = Signal(
                time=engine.clock.now(),
                bar_time=engine.clock.now() - timedelta(minutes=6),
                mode="paper",
                symbol="EURUSD",
                timeframe="M5",
                strategy="TEST",
                direction="buy",
                score=90,
                ai_score=90,
                final_decision="approved",
                config_hash=engine.settings.safety_fingerprint(),
                features_json={},
            )
            session.add(signal)
            session.flush()
            sid = signal.id
            context = safe_context(engine.clock, source=SourceKind.MT5, signal_id=sid)
            features = {
                "source": "mt5",
                "bar_closed_at": context.bar_closed_at.isoformat(),
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "news_hash": context.news.evidence_hash,
                "decision_digest": context.digest,
            }
            if fault in {"code", "model", "digest", "source", "news"}:
                key = {
                    "code": "code_hash",
                    "model": "model_sha256",
                    "digest": "decision_digest",
                    "source": "source",
                    "news": "news_hash",
                }[fault]
                features[key] = "wrong"
            if fault == "strategy":
                signal.strategy = "OTHER"
            if fault == "bar":
                signal.bar_time = engine.clock.now()
            if fault == "score":
                signal.score = 89
            signal.features_json = features
        snapshot = BrokerSnapshot(
            await engine.broker.get_account_info(),
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            plan.worst_loss_account,
            plan.margin_account,
            D("5.36"),
            engine.clock.now(),
            (),
            D("1"),
            D("1"),
            SourceKind.MT5,
            True,
        )
        from core.models import RiskState

        with engine.database.session() as session:
            row = session.scalar(select(RiskState))
            result = risk.evaluate(session, command, snapshot, context, row)
            assert "unbound_persisted_signal" in result.reasons
    finally:
        await engine.shutdown()
        engine.database.close()


def test_position_risk_is_finite_and_positive_identity():
    with pytest.raises(BrokerError):
        PositionRisk(1, D("NaN"))
    with pytest.raises(BrokerError):
        PositionRisk(1, 1.0)
    with pytest.raises(BrokerError):
        PositionRisk(0, D("1"))


async def test_snapshot_duplicate_risk_ids_are_not_silently_dropped(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        snapshot = BrokerSnapshot(
            await engine.broker.get_account_info(),
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            D("1"),
            D("1"),
            D("2"),
            engine.clock.now(),
        )
        with pytest.raises(BrokerError):
            replace(snapshot, position_risks=(PositionRisk(1, D("1")), PositionRisk(1, D("2"))))
        with pytest.raises(BrokerError):
            replace(snapshot, durable_simulation=1)
    finally:
        await engine.shutdown()
        engine.database.close()
