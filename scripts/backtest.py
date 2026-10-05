"""Explicit local historical replay CLI; no production daemon, native broker or provider connection."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backtesting.backtester import Backtester
from backtesting.contracts import BacktestOptions
from backtesting.dataset import HistoricalDataset
from core.settings import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline research replay; no automatic broker promotion")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--review-mode", choices=("veto", "archive", "synthetic_research"), default="veto")
    parser.add_argument(
        "--simulate-orders", action="store_true", help="Resume ONLY the new private BACKTEST ledger"
    )
    parser.add_argument(
        "--close-at-end", action="store_true", help="Explicit simulated liquidation; may fail stale/FX gates"
    )
    parser.add_argument("--max-events", type=int, default=500000)
    args = parser.parse_args(argv)
    try:
        if args.env_file is not None and not args.env_file.is_file():
            raise ValueError("missing explicit research environment")
        # Without --env-file, do not read the repository's credential file.
        cfg = Settings(_env_file=args.env_file if args.env_file is not None else None)
        dataset = HistoricalDataset.load(args.manifest)
        options = BacktestOptions(
            review_mode=args.review_mode,
            simulate_orders=args.simulate_orders,
            close_at_end=args.close_at_end,
            max_events=args.max_events,
        )
        report = asyncio.run(Backtester(dataset, cfg, options=options).run(args.output))
        print(
            json.dumps(
                {
                    "status": "completed",
                    "report": str(args.output / "report.json"),
                    "closed_trades": report["metrics"]["closed_trades"],
                    "promotion_eligible": False,
                    "live_enabled": False,
                    "native_broker_calls": 0,
                    "provider_calls": 0,
                }
            )
        )
        return 0
    except KeyboardInterrupt:
        print('{"status":"interrupted","promotion_eligible":false}')
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "refused_or_incomplete",
                    "error_kind": type(exc).__name__,
                    "promotion_eligible": False,
                    "live_enabled": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
