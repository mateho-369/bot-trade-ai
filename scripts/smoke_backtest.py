"""Offline engineering smoke: one artificial simulated trade, NEVER genuine research or stage evidence."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from backtesting.backtester import Backtester
from backtesting.contracts import BacktestOptions
from backtesting.dataset import DatasetError, HistoricalDataset
from core.settings import Settings
from scripts.make_backtest_fixture import make_fixture


async def run():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="reflex-backtest-smoke-") as name:
        root = Path(name)
        production = root / "production_never_used"
        production.mkdir()
        sentinel = production / "capital_and_stops.txt"
        sentinel.write_text("PRESERVE")
        manifest = make_fixture(root / "synthetic", warmup_minutes=12240, replay_minutes=2)
        dataset = HistoricalDataset.load(manifest)
        assert dataset.manifest.origin.kind == "synthetic_fixture"
        checks += 1
        settings = Settings(_env_file=None, project_root=production)
        no_review = await Backtester(dataset, settings).run(root / "veto")
        assert no_review["metrics"]["closed_trades"] == no_review["metrics"]["open_trades"] == 0
        checks += 1
        assert not no_review["simulated_execution_enabled"]
        checks += 1
        result = await Backtester(
            dataset,
            settings,
            options=BacktestOptions(
                review_mode="synthetic_research", simulate_orders=True, close_at_end=True
            ),
        ).run(root / "trade")
        assert result["metrics"]["closed_trades"] == 1 and result["artificial_reviews"] == 1
        checks += 1
        assert result["metrics"]["open_trades"] == 0 and result["metrics"]["costs_included"]
        checks += 1
        assert float(result["metrics"]["commission_account"]) < 0
        checks += 1
        assert float(result["metrics"]["final_equity_account"]) < 1000
        checks += 1
        assert not result["promotion_eligible"] and not result["live_enabled"]
        checks += 1
        copied = HistoricalDataset.load(root / "trade/inputs/manifest.json")
        assert copied.dataset_sha256 == dataset.dataset_sha256
        checks += 1
        before = (root / "trade/report.json").read_bytes()
        try:
            await Backtester(dataset, settings).run(root / "trade")
        except DatasetError:
            pass
        else:
            raise AssertionError("existing ledger overwrite must be refused")
        assert (root / "trade/report.json").read_bytes() == before
        checks += 1
        assert sentinel.read_text() == "PRESERVE" and "MetaTrader5" not in sys.modules
        checks += 1
        assert not any(production.glob("*.db")) and not list(production.glob("data/**/*.db"))
        checks += 1
        assert "synthetic_review_not_ai" in result["promotion_blockers"]
        checks += 1
        return {
            "fixture_only": True,
            "historical_data_authenticity_verified": False,
            "genuine_strategy_qualification": False,
            "eligible_stage_evidence": False,
            "checks_passed": checks,
            "simulated_entries": 1,
            "simulated_closes": 1,
            "synthetic_trade_net_account": result["metrics"]["net_profit_account"],
            "credentials_loaded": False,
            "native_sdk_imported": False,
            "actual_native_broker_calls": 0,
            "actual_provider_network_calls": 0,
            "actual_telegram_network_calls": 0,
            "actual_child_processes_spawned": 0,
            "real_orders": 0,
            "automatic_resume_production": False,
            "financial_history_reset": False,
            "demonstrates_profitability": False,
        }


def main():
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
