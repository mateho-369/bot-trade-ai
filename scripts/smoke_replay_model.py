"""ARTIFICIAL model replay smoke; NO genuine skill, historical availability, owner or stage evidence."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from backtesting.backtester import Backtester, ReplaySettings
from backtesting.contracts import BacktestOptions
from backtesting.dataset import HistoricalDataset
from scripts.make_backtest_model_fixture import make_model_fixture
from scripts.verify_replay_model import verify


async def run():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="reflex-ARTIFICIAL-ml-replay-") as name:
        root = Path(name)
        production = root / "production_never_opened"
        production.mkdir()
        (production / "data").mkdir()
        (production / "data/reflexbot.db").write_bytes(b"PRESERVE OWNER HISTORY NEVER OPEN")
        original = (production / "data/reflexbot.db").read_bytes()
        settings = ReplaySettings(project_root=production, model_filter_enabled=True)
        manifest = make_model_fixture(root / "ARTIFICIAL_input", settings=settings)
        observed = await verify(manifest, settings)
        assert observed["reconstructed_from_frozen_past_corpus"] and not observed["ledger_created"]
        checks += 1
        assert observed["fixture_labels"] and not observed["genuine_model_provenance_verified"]
        checks += 1
        dataset = HistoricalDataset.load(manifest)
        default = await Backtester(dataset, settings).run(root / "analysis_only")
        assert default["metrics"]["closed_trades"] == 0 and not default["simulated_execution_enabled"]
        checks += 1
        options = BacktestOptions(review_mode="synthetic_research", simulate_orders=True, close_at_end=True)
        positive = await Backtester(dataset, settings, options=options).run(root / "model_trade")
        assert positive["model_policy_enabled"] and positive["metrics"]["closed_trades"] == 1
        checks += 1
        assert positive["replay_model"]["model_sha256"] == observed["model_sha256"]
        checks += 1
        assert not positive["replay_model"]["midrun_selection"]
        checks += 1
        entries = [json.loads(line) for line in (root / "model_trade/signals.jsonl").read_text().splitlines()]
        approved = next(item for item in entries if item["state"] == "approved")["payload"]
        assert approved["model_gate"]["probability"] >= settings.model_min_probability
        checks += 1
        assert approved["model_gate"] == approved["decision_context"]["features"]["model_gate"]
        checks += 1
        assert approved["ai_review"]["provider"] == "replay"
        checks += 1
        assert "synthetic_model_training_labels" in positive["promotion_blockers"]
        checks += 1
        inverse = make_model_fixture(
            root / "ARTIFICIAL_inverse", relationship="inverse_quality", settings=settings
        )
        denied = await Backtester(HistoricalDataset.load(inverse), settings, options=options).run(
            root / "veto"
        )
        assert denied["metrics"]["closed_trades"] == 0 and denied["model_policy_enabled"]
        checks += 1
        assert "learning_filter_veto_or_unavailable" in denied["veto_reasons"]
        veto_trace = [json.loads(line) for line in (root / "veto/signals.jsonl").read_text().splitlines()]
        veto_gate = next(row for row in veto_trace if row["phase"] == "finalization")["payload"]["model_gate"]
        assert not veto_gate["accepted"] and veto_gate["probability"] < veto_gate["threshold"]
        checks += 1
        captured = HistoricalDataset.load(root / "model_trade/inputs/manifest.json")
        assert captured.dataset_sha256 == dataset.dataset_sha256
        checks += 1
        assert (production / "data/reflexbot.db").read_bytes() == original
        checks += 1
        assert "MetaTrader5" not in sys.modules and not positive["live_enabled"]
        checks += 1
        assert not positive["promotion_eligible"] and not denied["promotion_eligible"]
        checks += 1
        return {
            "fixture_only": True,
            "checks_passed": checks,
            "actual_portable_model_inference": True,
            "engineered_probability_approval_and_veto_exercised": True,
            "historical_model_availability_verified": False,
            "genuine_model_provenance_verified": False,
            "predictive_skill_demonstrated": False,
            "eligible_stage_evidence": False,
            "production_model_activated": False,
            "genuine_owner_authenticated": False,
            "native_sdk_imported": False,
            "actual_native_broker_calls": 0,
            "actual_provider_network_calls": 0,
            "actual_telegram_network_calls": 0,
            "actual_child_processes_spawned": 0,
            "real_orders": 0,
            "automatic_resume_production": False,
            "financial_history_reset": False,
        }


def main():
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
