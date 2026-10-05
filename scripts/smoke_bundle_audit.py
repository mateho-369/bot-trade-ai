"""ARTIFICIAL bundle integrity/metric/memory-SQL smoke; never authentic history/owner/stage evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from backtesting.audit.runner import audit_bundle
from backtesting.backtester import Backtester, ReplaySettings
from backtesting.contracts import BacktestOptions
from backtesting.dataset import HistoricalDataset
from scripts.make_backtest_fixture import make_fixture
from scripts.make_backtest_model_fixture import make_model_fixture


def file_inventory(root):
    return {
        p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


async def run():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="reflex-ARTIFICIAL-bundle-audit-") as name:
        root = Path(name)
        manifest = make_fixture(root / "input", replay_minutes=1)
        opts = BacktestOptions(review_mode="synthetic_research", simulate_orders=True, close_at_end=True)
        await Backtester(HistoricalDataset.load(manifest), ReplaySettings(), options=opts).run(root / "run")
        before = file_inventory(root / "run")
        closure = (root / "run/bundle.json").read_bytes()
        result = audit_bundle(root / "run", trusted_sha256=hashlib.sha256(closure).hexdigest())
        assert result["overall"] == "consistent_research_bundle" and result["integrity_verified"]
        checks += 1
        assert result["observations"]["metric_fields_recomputed"] == 32
        checks += 1
        assert result["observations"]["trade_rows"] == 1
        checks += 1
        assert result["observations"]["closed_private_memory_ledger_checked"]
        checks += 1
        assert result["observations"]["proposal_review_context_bindings_checked"]
        checks += 1
        assert not result["original_db_opened_by_sqlite"] and result["application_state_writes"] == 0
        checks += 1
        assert file_inventory(root / "run") == before
        checks += 1
        assert (
            not result["stage_evidence"]
            and not result["trading_authorized"]
            and not result["owner_authenticated"]
        )
        checks += 1
        assert audit_bundle(root / "run", trusted_sha256="a" * 64)["overall"] == "blocked"
        checks += 1
        path = root / "run/report.json"
        report = json.loads(path.read_bytes())
        report["metrics"]["net_profit_account"] = "50000"
        path.write_text(json.dumps(report))
        assert audit_bundle(root / "run")["overall"] == "blocked"
        checks += 1
        # Deliberately forge all LOCAL file hashes. Semantic metrics still must fail.
        forged = json.loads(closure)
        forged["files"]["report.json"] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        (root / "run/bundle.json").write_text(json.dumps(forged))
        assert not audit_bundle(root / "run")["internal_consistency_verified"]
        checks += 1
        ml = make_model_fixture(root / "ARTIFICIAL_model_input", replay_minutes=1)
        await Backtester(
            HistoricalDataset.load(ml), ReplaySettings(model_filter_enabled=True), options=opts
        ).run(root / "model_run")
        audited = audit_bundle(root / "model_run")
        assert (
            audited["internal_consistency_verified"]
            and audited["observations"]["logistic_probability_observations_recomputed"] == 1
        )
        checks += 1
        assert audited["observations"]["training_reconstruction_reexecuted"] is False
        checks += 1
        assert "MetaTrader5" not in sys.modules and not audited["production_model_activated"]
        checks += 1
    return {
        "fixture_only": True,
        "checks_passed": checks,
        "actual_portable_logistic_audit": True,
        "private_memory_sql_only": True,
        "auditor_application_state_writes": 0,
        "production_state_writes": 0,
        "historical_provenance_verified": False,
        "genuine_owner_authenticated": False,
        "eligible_stage_evidence": False,
        "trading_authorized": False,
        "production_model_activated": False,
        "native_sdk_imported": False,
        "actual_native_broker_calls": 0,
        "actual_provider_network_calls": 0,
        "actual_telegram_network_calls": 0,
        "actual_child_processes_spawned": 0,
        "real_orders": 0,
        "automatic_resume": False,
        "financial_history_reset": False,
        "smoke_creates_disposable_fixtures": True,
    }


def main():
    print(json.dumps(asyncio.run(run()), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
