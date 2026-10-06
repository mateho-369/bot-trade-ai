"""Real finalization/execution inference over fixtures, never actual AI/trading approval."""

import copy
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from ai.artifacts import artifact_path
from ai.feature_engineering import FeatureEngineering
from ai.model_registry import ModelRegistry
from core.models import BotState, DeploymentEvidence, ModelVersion, OrderIntent, Signal
from core.security import sha256_json
from tests.backtest_helpers import finalized
from tests.replay_model_helpers import model_runtime
from tests.risk_helpers import OWNER
from trading.risk_types import DecisionContext
from trading.types import BrokerError, Tick, TradingDisabled


async def test_enabled_actual_model_gate_does_not_approve_without_ai_or_resume(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, model):
        proposal = await signals.analyze("EURUSD")
        window = news.window("EURUSD", signals.clock.now())
        actual = ModelRegistry(engine.database, engine.settings, engine.clock, engine.profile)
        vector = FeatureEngineering.from_snapshot(
            proposal.payload()["feature_snapshot"], proposal.payload()["technical"]
        )
        probability, binding = actual.replay_inference(vector)
        assert probability >= engine.settings.model_min_probability and binding == model.binding_digest
        denied = await signals.finalize(proposal.signal_id, review=None, news=window)
        assert not denied.approved and "ai_unavailable_or_invalid" in denied.reasons
        assert engine.database.status()["state"] == "paused" and not await engine.broker.get_positions()
        with engine.database.session() as session:
            assert session.scalar(select(DeploymentEvidence)) is None
            assert session.scalar(select(OrderIntent)) is None


async def test_approved_signal_persists_full_actual_gate_and_idempotent_order(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, model):
        ready = await finalized(signals, news, reviewer)
        assert ready.approved and ready.payload()["ai_review"]["provider"] == "replay"
        gate = ready.payload()["model_gate"]
        assert gate["model_sha256"] == engine.profile.model_sha256 == model.digest
        assert gate["replay_binding_sha256"] == model.binding_digest
        assert gate["proposal_hash"] == ready.proposal_hash
        assert gate["code_hash"] == engine.profile.code_hash
        assert gate["policy_hash"] == engine.settings.strategy_fingerprint()
        assert gate["source"] == "historical" and gate["threshold"] == 0.7
        assert ready.context.features["model_gate"] == gate
        assert ready.payload()["model_gate_digest"] == sha256_json(gate)
        with pytest.raises(TradingDisabled):
            await engine.execute_signal(ready.signal_id)  # ML quality is not owner/runtime resume permission.
        assert not await engine.broker.get_positions()


@pytest.mark.parametrize(
    "field,value",
    [
        ("probability", 0.91),
        ("threshold", 0.5),
        ("model_sha256", "a" * 64),
        ("proposal_hash", "b" * 64),
        ("feature_schema_hash", "c" * 64),
        ("feature_vector_sha256", "d" * 64),
        ("replay_binding_sha256", "e" * 64),
        ("source", "mt5"),
        ("code_hash", "f" * 64),
        ("policy_hash", "1" * 64),
        ("config_hash", "2" * 64),
        ("format", "owner-approved-model-v1"),
    ],
)
async def test_rehashing_gate_and_context_does_not_fabricate_inference(model_input, tmp_path, field, value):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, _):
        ready = await finalized(signals, news, reviewer)
        assert ready.approved
        with engine.database.session() as session:
            row = session.get(Signal, ready.signal_id)
            payload = copy.deepcopy(row.features_json)
            payload["model_gate"][field] = value
            payload["model_gate_digest"] = sha256_json(payload["model_gate"])
            payload["decision_context"]["features"]["model_gate"] = copy.deepcopy(payload["model_gate"])
            payload["decision_digest"] = DecisionContext.from_dict(payload["decision_context"]).digest
            row.features_json = payload
        with pytest.raises(TradingDisabled):
            await signals.get(ready.signal_id)
        engine.control.resume(OWNER, account_key=engine.account_key)
        with pytest.raises(TradingDisabled):
            await engine.execute_signal(ready.signal_id)
        assert not await engine.broker.get_positions()
        # Direct engine.open with a rehashed caller context STILL goes through shared risk revalidation.
        context = DecisionContext.from_dict(payload["decision_context"])
        with pytest.raises(BrokerError):
            await engine.open(
                "EURUSD",
                ready.side,
                ready.stop_price,
                context,
                strategy="weighted_router_v1",
                idempotency_key="c" * 64,
            )
        assert not await engine.broker.get_positions()


@pytest.mark.parametrize("phase", ["finalize", "execute"])
@pytest.mark.parametrize(
    "fault",
    [
        "file",
        "proof_missing",
        "late_selection",
        "scope",
        "inactive",
        "duplicate_active",
        "marker",
        "captured_manifest",
    ],
)
async def test_model_selection_artifact_and_context_rechecked_not_trusted_flags(
    model_input, tmp_path, phase, fault
):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, model):
        proposal = await signals.analyze("EURUSD")
        window = news.window("EURUSD", engine.clock.now())
        review = await reviewer.review(proposal, window)
        if phase == "execute":
            ready = await signals.finalize(proposal.signal_id, review=review, news=window)
            assert ready.approved
            engine.control.resume(OWNER, account_key=engine.account_key)
        if fault == "file":
            artifact_path(engine.settings, model.digest).write_text("{}")
        elif fault == "marker":
            marker = engine.settings.project_root / "run.json"
            data = json.loads(marker.read_bytes())
            data["replay_model"]["binding_sha256"] = "d" * 64
            marker.write_text(json.dumps(data))
        elif fault == "captured_manifest":
            (engine.settings.project_root / "inputs" / next(iter(model_input.files))).write_text("{}")
        else:
            with engine.database.session() as session:
                row = session.scalar(select(ModelVersion))
                if fault == "proof_missing":
                    metrics = copy.deepcopy(row.metrics_json)
                    metrics.pop("replay_binding")
                    row.metrics_json = metrics
                elif fault == "late_selection":
                    metrics = copy.deepcopy(row.metrics_json)
                    metrics["replay_binding"]["selection"]["selected_at"] = (
                        engine.clock.now() + timedelta(seconds=1)
                    ).isoformat()
                    row.metrics_json = metrics
                elif fault == "scope":
                    row.deployment_scope = "paper"
                elif fault == "inactive":
                    row.active = False
                elif fault == "duplicate_active":
                    session.add(
                        ModelVersion(
                            model_type=row.model_type,
                            metrics_json=row.metrics_json,
                            file_path=row.file_path,
                            artifact_sha256="9" * 64,
                            active=True,
                            deployment_scope="backtest",
                            config_hash=row.config_hash,
                        )
                    )
        if phase == "finalize":
            denied = await signals.finalize(proposal.signal_id, review=review, news=window)
            assert not denied.approved and "learning_filter_veto_or_unavailable" in denied.reasons
        else:
            with pytest.raises(TradingDisabled):
                await engine.execute_signal(proposal.signal_id)
        assert not await engine.broker.get_positions()


async def test_ordinary_historical_registration_has_no_replay_selection_permission(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (engine, _, _, _, _, _, model):
        with engine.database.session() as session:
            row = session.scalar(select(ModelVersion))
            metrics = copy.deepcopy(row.metrics_json)
            metrics.pop("replay_binding")
            row.metrics_json = metrics
            row.active = False
        registry = ModelRegistry(engine.database, engine.settings, engine.clock, engine.profile)
        # Stop the private session. The local operator audit tag is internal, not a remote identity.
        engine.control.pause(OWNER)
        engine.control.release()
        with pytest.raises(TradingDisabled):
            registry.activate(row.id, scope="backtest", operator=OWNER)
        with pytest.raises(TradingDisabled):
            registry.activate(row.id, scope="paper", operator=OWNER)
        assert registry.get(row.id).digest == model.digest


@pytest.mark.parametrize("provider", ["test", "replay"])
async def test_replay_provider_cannot_bypass_missing_gate_and_test_cannot_claim_historical(
    model_input, tmp_path, provider
):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, _):
        proposal = await signals.analyze("EURUSD")
        window = news.window("EURUSD", engine.clock.now())
        reply = await reviewer.review(proposal, window)
        if provider == "replay":
            with engine.database.session() as session:
                session.scalar(select(ModelVersion)).active = False
        denied = await signals.finalize(
            proposal.signal_id, review=replace(reply, provider=provider), news=window
        )
        assert not denied.approved and "unbound_or_stale_ai_review" in denied.reasons


async def test_actual_probability_recomputed_at_pre_send_risk_not_only_signal_get(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, _):
        ready = await finalized(signals, news, reviewer)
        assert ready.approved
        # Remove the persisted gate but recompute EVERY mutable context digest, then avoid get().
        with engine.database.session() as session:
            row = session.get(Signal, ready.signal_id)
            payload = copy.deepcopy(row.features_json)
            payload.pop("model_gate")
            payload.pop("model_gate_digest")
            payload["decision_context"]["features"].pop("model_gate")
            context = DecisionContext.from_dict(payload["decision_context"])
            payload["decision_digest"] = context.digest
            row.features_json = payload
        engine.control.resume(OWNER, account_key=engine.account_key)
        with pytest.raises(BrokerError):
            await engine.open(
                "EURUSD",
                ready.side,
                ready.stop_price,
                context,
                strategy="weighted_router_v1",
                idempotency_key="d" * 64,
            )
        assert not await engine.broker.get_positions()


async def test_good_ml_probability_does_not_bypass_kill_risk_stop(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, _):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        engine.control.kill(OWNER)
        with pytest.raises(BrokerError):
            await engine.execute_signal(ready.signal_id)
        assert not await engine.broker.get_positions()
        with engine.database.session() as session:
            assert session.get(BotState, 1).kill_switch_active


async def test_model_failure_does_not_stop_protective_work_for_owned_position(model_input, tmp_path):
    async with model_runtime(tmp_path, model_input) as (
        engine,
        signals,
        market,
        news,
        reviewer,
        manager,
        model,
    ):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await engine.execute_signal(ready.signal_id)
        position = (await engine.broker.get_positions())[0]
        artifact_path(engine.settings, model.digest).write_text("{}")
        engine.control.kill(OWNER)
        market.clock.advance(timedelta(seconds=1))
        bid = position.tp - Decimal("0.0003")
        market._quotes["EURUSD"] = Tick("EURUSD", bid, bid + Decimal("0.00012"), market.clock.now())
        summary = await manager.cycle()
        protected = (await engine.broker.get_positions())[0]
        assert protected.sl >= position.sl
        assert any(item["operation"] == "sl" for item in summary["outcomes"])


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -0.01, 1.01, True, 0.69])
async def test_invalid_or_subthreshold_inference_never_becomes_an_approval(
    model_input, tmp_path, monkeypatch, probability
):
    # Narrow hostile estimator test. The positive test above always uses actual inference, not this stub.
    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, model):
        monkeypatch.setattr(
            ModelRegistry, "replay_inference", lambda *args: (probability, model.binding_digest)
        )
        denied = await finalized(signals, news, reviewer)
        assert not denied.approved and "learning_filter_veto_or_unavailable" in denied.reasons
        assert not await engine.broker.get_positions()


@pytest.mark.parametrize("error", [RuntimeError, OSError, ValueError])
async def test_library_read_inference_failure_is_sanitized_entry_veto(
    model_input, tmp_path, monkeypatch, error
):
    from ai.model_registry import RegisteredModel

    async with model_runtime(tmp_path, model_input) as (engine, signals, _, news, reviewer, _, _):

        def fail(*args):
            raise error("SENTINEL_UNTRUSTED_PRIVATE_MODEL_ERROR")

        monkeypatch.setattr(RegisteredModel, "predict", fail)
        denied = await finalized(signals, news, reviewer)
        assert not denied.approved and "learning_filter_veto_or_unavailable" in denied.reasons
        assert "SENTINEL" not in str(denied.reasons)
        assert not await engine.broker.get_positions()
