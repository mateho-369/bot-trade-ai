"""Real private BACKTEST integration, all data/model labels are explicitly engineered fixtures."""

import asyncio
import hashlib
import json
import socket
from datetime import timedelta

import pytest
from sqlalchemy import select

from ai.model_registry import ModelRegistry
from ai.model_trainer import ModelTrainer
from backtesting.backtester import Backtester, ReplaySettings
from backtesting.contracts import BacktestOptions
from backtesting.dataset import DatasetError, HistoricalDataset
from core.models import AuditLog, BotState, DeploymentEvidence, ModelVersion
from scripts.make_backtest_model_fixture import make_model_fixture
from tests.backtest_helpers import clone_dataset, document
from tests.replay_model_helpers import changed_input, prepare_private, verified
from trading.types import ManualClock, TradingDisabled


def options(**changes):
    return BacktestOptions(
        review_mode="synthetic_research", simulate_orders=True, close_at_end=True, **changes
    )


def journal(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_default_enabled_model_is_analysis_only_no_resume(model_input, tmp_path):
    report = await Backtester(model_input, ReplaySettings(model_filter_enabled=True)).run(tmp_path / "run")
    assert not report["simulated_execution_enabled"] and report["metrics"]["closed_trades"] == 0
    assert report["model_policy_enabled"] and report["replay_model"]["fixture_labels"]
    assert report["replay_model"]["reconstructed_from_frozen_past_corpus"]
    assert "ai_unavailable_or_invalid" in report["veto_reasons"]
    assert not report["promotion_eligible"] and not report["replay_model"]["production_model_activated"]
    assert "model_evaluation_not_stage_evidence" in report["promotion_blockers"]


async def test_real_inference_fixed_model_exact_inputs_private_snapshot_not_owner_activate(
    model_input, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("replay must make no network request")
    )
    monkeypatch.setattr(ModelRegistry, "activate", lambda *args, **kwargs: pytest.fail("no owner activation"))
    production = tmp_path / "production"
    production.mkdir()
    (production / "data").mkdir()
    (production / "data/reflexbot.db").write_bytes(b"PRESERVE NOT SQLITE NEVER OPEN")
    (production / "data/models").mkdir()
    (production / "data/models/owner-model.json").write_bytes(b"KEEP ORIGINAL OWNER MODEL")
    before = {
        str(path.relative_to(production)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in production.rglob("*")
        if path.is_file()
    }
    calls, real_train = [], ModelTrainer.train

    def train(self, corpus, *, as_of):
        assert not (tmp_path / "run").exists(), "reconstruction must precede replay/ledger creation"
        calls.append(as_of)
        return real_train(self, corpus, as_of=as_of)

    monkeypatch.setattr(ModelTrainer, "train", train)
    report = await Backtester(
        model_input, ReplaySettings(project_root=production, model_filter_enabled=True), options=options()
    ).run(tmp_path / "run")
    assert len(calls) == 1 and report["metrics"]["closed_trades"] == 1
    assert report["model_sha256"] == model_input.manifest.model.artifact.sha256
    assert not report["replay_model"]["midrun_selection"] and not report["promotion_eligible"]
    assert report["native_broker_calls"] == report["provider_calls"] == report["outbound_report_calls"] == 0
    assert before == {
        str(path.relative_to(production)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in production.rglob("*")
        if path.is_file()
    }
    restored = HistoricalDataset.load(tmp_path / "run/inputs/manifest.json")
    assert restored.dataset_sha256 == model_input.dataset_sha256
    finalization = next(row for row in journal(tmp_path / "run/signals.jsonl") if row["state"] == "approved")
    assert finalization["payload"]["model_gate"]["probability"] >= 0.7
    assert finalization["payload"]["ai_review"]["provider"] == "replay"


async def test_real_inverse_model_vetoes_artificial_high_confidence_ai_without_disable(tmp_path):
    manifest = make_model_fixture(tmp_path / "inverse", relationship="inverse_quality")
    report = await Backtester(
        HistoricalDataset.load(manifest), ReplaySettings(model_filter_enabled=True), options=options()
    ).run(tmp_path / "run")
    assert report["model_policy_enabled"] and report["artificial_reviews"] == 1
    assert report["metrics"]["closed_trades"] == report["metrics"]["open_trades"] == 0
    assert "learning_filter_veto_or_unavailable" in report["veto_reasons"]
    observation = next(
        row for row in journal(tmp_path / "run/signals.jsonl") if row["phase"] == "finalization"
    )["payload"]["model_gate"]
    assert not observation["accepted"] and observation["probability"] < observation["threshold"]
    assert not report["promotion_eligible"]


async def test_model_never_substitutes_unknown_news_or_native_stage_evidence(model_input, tmp_path):
    unknown = clone_dataset(model_input, tmp_path / "unknown", news=None)
    report = await Backtester(unknown, ReplaySettings(model_filter_enabled=True), options=options()).run(
        tmp_path / "run"
    )
    assert report["model_policy_enabled"] and report["metrics"]["closed_trades"] == 0
    assert report["artificial_reviews"] == 0 and "unknown_stale_or_unsafe_news" in report["veto_reasons"]
    assert not report["promotion_eligible"]


@pytest.mark.parametrize("fault", ["weight", "corpus", "disabled", "model_missing"])
async def test_bad_model_rejected_before_directory_or_financial_initialization(model_input, tmp_path, fault):
    history = model_input
    settings = ReplaySettings(model_filter_enabled=fault != "disabled")
    if fault == "weight":
        history = changed_input(history, artifact=lambda body: body["model"].update(intercept=0))
    elif fault == "corpus":
        history = changed_input(history, corpus=lambda body: body.update(policy_hash="d" * 64))
    elif fault == "model_missing":
        history = clone_dataset(model_input, tmp_path / "none", model=None)
    with pytest.raises((DatasetError, TradingDisabled)):
        await Backtester(history, settings, options=options()).run(tmp_path / "NEVER_CREATED")
    assert not (tmp_path / "NEVER_CREATED").exists()


async def test_exact_archive_model_sha_not_rebound_and_available_reply_is_causal(model_input, tmp_path):
    cfg = ReplaySettings(model_filter_enabled=True)
    await Backtester(model_input, cfg, options=options()).run(tmp_path / "binding")
    approved = next(row for row in journal(tmp_path / "binding/signals.jsonl") if row["state"] == "approved")
    payload, start = approved["payload"], model_input.manifest.replay_from
    review = payload["ai_review"]
    values = {key: value for key, value in review.items() if key not in {"source", "provider"}}
    values.update(available_at=(start + timedelta(seconds=3)).isoformat(), original_provider="ollama")
    archive = {
        "format": "reflex-replay-reviews-v1",
        "description": "TEST ONLY rehashed archived fixture; not an actual Ollama call",
        "entries": [values],
    }
    history = clone_dataset(model_input, tmp_path / "archive", reviews=archive)
    report = await Backtester(
        history, cfg, options=BacktestOptions(review_mode="archive", simulate_orders=True, close_at_end=True)
    ).run(tmp_path / "delayed")
    assert report["archived_review_matches"] == 1 and report["metrics"]["closed_trades"] == 1
    entry = next(row for row in journal(tmp_path / "delayed/operations.jsonl") if row["operation"] == "entry")
    assert entry["time"] == (start + timedelta(seconds=3)).isoformat()
    values["model_sha256"] = "a" * 64
    wrong = clone_dataset(model_input, tmp_path / "wrong_archive", reviews=archive)
    denied = await Backtester(
        wrong, cfg, options=BacktestOptions(review_mode="archive", simulate_orders=True, close_at_end=True)
    ).run(tmp_path / "wrong")
    # A proposal lookup is NOT a bound/approved model match. No review is rebound.
    assert denied["archived_review_matches"] == 1 and denied["metrics"]["closed_trades"] == 0
    assert "unbound_or_stale_ai_review" in denied["veto_reasons"]


@pytest.mark.parametrize(
    "fault",
    [
        "existing_model",
        "running",
        "stale_clock",
        "completed",
        "run_identity",
        "corpus_capture",
        "model_capture",
        "killed",
    ],
)
def test_snapshot_import_new_private_only_preserves_state(small_model_input, tmp_path, fault):
    root = tmp_path / "new"
    model, cfg, profile = verified(small_model_input, root)
    db = prepare_private(root, small_model_input, model, cfg, profile)
    clock = ManualClock(small_model_input.manifest.replay_from)
    try:
        if fault == "existing_model":
            with db.session() as session:
                session.add(
                    ModelVersion(
                        model_type="sentinel",
                        metrics_json={},
                        file_path="sentinel",
                        artifact_sha256="d" * 64,
                        config_hash="e" * 64,
                    )
                )
        elif fault in {"running", "killed"}:
            with db.session() as session:
                state = session.get(BotState, 1)
                state.desired_state = "running" if fault == "running" else "killed"
                state.kill_switch_active = fault == "killed"
        elif fault == "stale_clock":
            clock.advance(timedelta(seconds=1))
        elif fault == "completed":
            (root / "completion.json").write_text("{}")
        elif fault == "run_identity":
            marker = document(root / "run.json")
            marker["model_sha256"] = "a" * 64
            (root / "run.json").write_text(json.dumps(marker))
        elif fault.endswith("capture"):
            file = model.selection.learning_dataset if fault == "corpus_capture" else model.selection.artifact
            (root / "inputs" / file.path).write_text("{}")
        before = db.status()
        with pytest.raises((DatasetError, TradingDisabled)):
            model.install_private(db, cfg, clock, profile)
        assert db.status() == before
        with db.session() as session:
            assert session.scalar(select(DeploymentEvidence)) is None
            assert (
                session.scalar(select(AuditLog).where(AuditLog.action == "replay.model_snapshot_imported"))
                is None
            )
    finally:
        db.close()


async def test_reconstruction_runs_off_async_event_loop(model_input, tmp_path, monkeypatch):
    import threading

    main_thread = threading.get_ident()
    worker, real = [], ModelTrainer.train

    def train(self, corpus, *, as_of):
        worker.append(threading.get_ident())
        return real(self, corpus, as_of=as_of)

    monkeypatch.setattr(ModelTrainer, "train", train)
    await Backtester(model_input, ReplaySettings(model_filter_enabled=True)).run(tmp_path / "run")
    assert worker and main_thread not in worker
    await asyncio.sleep(0)  # Async caller remains usable; no blocking native MT5/provider call.
