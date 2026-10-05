"""Actual offline generation/audit/lifecycle/CLI branches; fixture-only identity and metrics."""

import asyncio
import json
from pathlib import Path

import pytest

from backtesting.audit.contracts import sha
from backtesting.audit.runner import audit_bundle
from backtesting.backtester import Backtester, ReplaySettings
from backtesting.bundle import seal_completed_run
from backtesting.contracts import BacktestOptions
from backtesting.dataset import DatasetError, HistoricalDataset
from scripts.audit_backtest import main as audit_cli
from scripts.make_backtest_fixture import make_fixture
from scripts.make_backtest_model_fixture import make_model_fixture
from tests.bundle_audit_helpers import clone_bundle, document, inventory, reseal_for_attack
from tests.test_bundle_audit_integrity import blocked


@pytest.mark.parametrize("kind", ["veto", "open", "ohlc", "lightgbm", "inverse", "short"])
async def test_real_supported_completed_runs_audited_without_relabeling(tmp_path, kind):
    ml = kind in {"inverse", "lightgbm"}
    if ml:
        manifest = make_model_fixture(
            tmp_path / "input",
            replay_minutes=1,
            algorithm="lightgbm" if kind == "lightgbm" else "logistic",
            relationship="inverse_quality" if kind == "inverse" else "quality",
        )
    else:
        manifest = make_fixture(
            tmp_path / "input",
            replay_minutes=1,
            warmup_minutes=60 if kind == "short" else 12240,
            quote_mode="ohlc_conservative" if kind == "ohlc" else "ticks",
        )
    options = (
        BacktestOptions()
        if kind == "veto"
        else BacktestOptions(
            review_mode="synthetic_research", simulate_orders=True, close_at_end=kind != "open"
        )
    )
    cfg = ReplaySettings(
        model_filter_enabled=ml, model_algorithm="lightgbm" if kind == "lightgbm" else "logistic"
    )
    report = await Backtester(HistoricalDataset.load(manifest), cfg, options=options).run(tmp_path / "run")
    before = inventory(tmp_path / "run")
    result = audit_bundle(tmp_path / "run")
    assert result["overall"] == "consistent_research_bundle"
    assert (
        result["observations"]["trade_rows"]
        == report["metrics"]["closed_trades"] + report["metrics"]["open_trades"]
    )
    assert before == inventory(tmp_path / "run")
    if kind == "lightgbm":
        assert result["observations"]["lightgbm_probability_observations_not_reexecuted"] > 0
        assert "lightgbm_inference_not_reexecuted" in {item["code"] for item in result["findings"]}
    elif kind == "open":
        assert result["observations"]["private_snapshot_open_positions"] == 1
    elif kind == "inverse":
        assert result["observations"]["logistic_probability_observations_recomputed"] == 1
        assert report["metrics"]["closed_trades"] == 0


def test_copied_bundle_does_not_open_original_recorded_model_paths(
    completed_ml_replay, tmp_path, monkeypatch
):
    root = clone_bundle(completed_ml_replay, tmp_path / "relocated")
    real = Path.open

    def protected(path, *args, **kwargs):
        assert not path.is_relative_to(completed_ml_replay)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", protected)
    assert audit_bundle(root)["internal_consistency_verified"]


def test_closure_is_never_replaced_or_old_outputs_resealed(completed_replay):
    before = inventory(completed_replay)
    with pytest.raises(DatasetError):
        seal_completed_run(completed_replay)
    assert before == inventory(completed_replay)


@pytest.mark.parametrize("file", ["data/replay.db-wal", "data/replay.db-journal", "data/replay.db-shm"])
def test_no_pending_sqlite_sidecar_deleted_ignored_or_replayed(completed_replay, tmp_path, file):
    root = clone_bundle(completed_replay, tmp_path / "run")
    (root / file).write_bytes(b"TEST_PENDING_PRESERVE")
    reseal_for_attack(root)
    before = inventory(root)
    blocked(root, "bundle_sqlite_sidecar_refused")
    assert before == inventory(root)


def test_old_part13_completion_without_closure_refused_not_auto_upgraded(completed_replay, tmp_path):
    root = clone_bundle(completed_replay, tmp_path / "old")
    (root / "bundle.json").unlink()
    before = inventory(root)
    blocked(root, "completed_bundle_manifest_required")
    assert before == inventory(root)


def test_cli_actual_output_exit_code_and_trusted_hash(completed_replay, capsys):
    digest = sha((completed_replay / "bundle.json").read_bytes())
    assert audit_cli(["--run", str(completed_replay), "--trusted-bundle-sha256", digest]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["internal_consistency_verified"] and result["bundle_sha256"] == digest
    assert not result["trading_authorized"] and result["trusted_anchor_supplied"]
    assert audit_cli(["--run", str(completed_replay), "--trusted-bundle-sha256", "a" * 64]) == 2
    assert json.loads(capsys.readouterr().out)["overall"] == "blocked"


def test_cli_missing_directory_fixed_errors_no_original_path_echo(tmp_path, capsys):
    assert audit_cli(["--run", str(tmp_path / "PASSWORD_SENTINEL_NOT_A_REAL_FILE")]) == 2
    output = capsys.readouterr().out
    assert "SENTINEL" not in output and json.loads(output)["application_state_writes"] == 0


async def test_seal_failure_preserves_evidence_and_private_runtime_is_released(tmp_path, monkeypatch):
    import backtesting.backtester as module

    manifest = make_fixture(tmp_path / "input", warmup_minutes=60, replay_minutes=1)

    def fail(*args):
        raise DatasetError("TEST_SEAL_FAILURE")

    monkeypatch.setattr(module, "seal_completed_run", fail)
    with pytest.raises(DatasetError):
        await Backtester(HistoricalDataset.load(manifest), ReplaySettings()).run(tmp_path / "run")
    assert document(tmp_path / "run/failure.json")["status"] == "incomplete"
    assert (tmp_path / "run/data/replay.db").exists() and not (tmp_path / "run/bundle.json").exists()
    assert not any((tmp_path / "run/data").glob("replay.db-*"))
    blocked(tmp_path / "run")


async def test_publication_hash_worker_off_event_loop_after_database_release(tmp_path, monkeypatch):
    import threading

    import backtesting.backtester as module
    from backtesting.audit.ledger import inspect_image

    main, workers, actual = threading.get_ident(), [], module.seal_completed_run
    manifest = make_fixture(tmp_path / "input", warmup_minutes=60, replay_minutes=1)

    def seal(root):
        workers.append(threading.get_ident())
        assert inspect_image((root / "data/replay.db").read_bytes())["control"]["session_id"] is None
        return actual(root)

    monkeypatch.setattr(module, "seal_completed_run", seal)
    await Backtester(HistoricalDataset.load(manifest), ReplaySettings()).run(tmp_path / "run")
    assert workers and main not in workers
    await asyncio.sleep(0)


async def test_market_shutdown_failure_cannot_publish_completed_bundle(tmp_path, monkeypatch):
    from backtesting.market import HistoricalMarket

    manifest = make_fixture(tmp_path / "input", warmup_minutes=60, replay_minutes=1)
    real, calls = HistoricalMarket.shutdown, []

    async def fail_first(self):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("TEST_MARKET_SHUTDOWN_FAILURE")
        return await real(self)

    monkeypatch.setattr(HistoricalMarket, "shutdown", fail_first)
    with pytest.raises(RuntimeError):
        await Backtester(HistoricalDataset.load(manifest), ReplaySettings()).run(tmp_path / "run")
    assert document(tmp_path / "run/failure.json")["status"] == "incomplete"
    assert not (tmp_path / "run/bundle.json").exists() and not (tmp_path / "run/completion.json").exists()
    blocked(tmp_path / "run")


async def test_actual_delayed_archive_available_time_audited_not_rebound(tmp_path):
    from datetime import timedelta

    from tests.backtest_helpers import clone_dataset
    from tests.bundle_audit_helpers import lines

    path = make_fixture(tmp_path / "input", replay_minutes=1)
    history = HistoricalDataset.load(path)
    opts = BacktestOptions(review_mode="synthetic_research", simulate_orders=True, close_at_end=True)
    cfg = ReplaySettings()
    await Backtester(history, cfg, options=opts).run(tmp_path / "binding")
    approved = next(row for row in lines(tmp_path / "binding/signals.jsonl") if row["state"] == "approved")
    values = {
        key: value
        for key, value in approved["payload"]["ai_review"].items()
        if key not in {"source", "provider"}
    }
    values.update(
        available_at=(history.manifest.replay_from + timedelta(seconds=3)).isoformat(),
        original_provider="ollama",
    )
    archive = {
        "format": "reflex-replay-reviews-v1",
        "description": "TEST ONLY archived fixture, not Ollama capture",
        "entries": [values],
    }
    copied = clone_dataset(history, tmp_path / "archive", reviews=archive)
    await Backtester(
        copied, cfg, options=BacktestOptions(review_mode="archive", simulate_orders=True, close_at_end=True)
    ).run(tmp_path / "run")
    assert audit_bundle(tmp_path / "run")["internal_consistency_verified"]
