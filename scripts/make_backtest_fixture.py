"""Write an explicitly artificial, reproducible replay fixture. NOT historical price evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from backtesting.artifacts import new_run_directory, write_json
from backtesting.dataset import BAR_FIELDS, TICK_FIELDS
from trading.mock_mt5 import synthetic_catalogue


def make_fixture(output: Path, *, warmup_minutes=12240, replay_minutes=3, quote_mode="ticks", seed=17):
    if type(warmup_minutes) is not int or not 60 <= warmup_minutes <= 50000:
        raise ValueError("bounded artificial warmup required")
    if type(replay_minutes) is not int or not 1 <= replay_minutes <= 360:
        raise ValueError("bounded artificial replay period required")
    if quote_mode not in {"ticks", "ohlc_conservative"} or type(seed) is not int or not 0 <= seed <= 1000000:
        raise ValueError("valid artificial quote mode and seed required")
    root = new_run_directory(output)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    replay = start + timedelta(minutes=warmup_minutes)
    end = replay + timedelta(minutes=replay_minutes)
    info = synthetic_catalogue()[0]["EURUSD"]
    records = []

    def fixture_price(index):
        offset = index - warmup_minutes
        value = (
            Decimal("1.10000")
            + Decimal(offset) * Decimal("0.000008")
            + Decimal(str(math.sin(offset * 0.13 + 2.6 + (seed - 17) / 100))) * Decimal("0.00025")
        )
        return (value / info.tick_size).to_integral_value(rounding=ROUND_HALF_EVEN) * info.tick_size

    previous = fixture_price(0)
    for index in range(warmup_minutes + replay_minutes + 1):
        time = start + timedelta(minutes=index)
        close = fixture_price(index + 1)
        high = max(previous, close) + Decimal("0.00050")
        low = min(previous, close) - Decimal("0.00050")
        records.append(
            {
                "time": time.isoformat(),
                "open_available_at": time.isoformat(),
                "available_at": (time + timedelta(minutes=1)).isoformat(),
                "open": str(previous),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "tick_volume": "60",
                "spread_open_points": "12",
                "spread_max_points": "15",
                "real_volume": "0",
            }
        )
        previous = close
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=BAR_FIELDS)
    writer.writeheader()
    writer.writerows(records)
    bars = buffer.getvalue().encode()
    (root / "EURUSD_M1.csv").write_bytes(bars)
    ticks = []
    if quote_mode == "ticks":
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=TICK_FIELDS)
        writer.writeheader()
        # A genuine-looking file format does NOT make these engineered ticks real.
        first = replay - timedelta(minutes=1)
        for seconds in range(0, (replay_minutes + 1) * 60 + 1, 5):
            time = first + timedelta(seconds=seconds)
            index = int((time - start).total_seconds() // 60)
            bid = Decimal(records[index]["open"])
            writer.writerow(
                {
                    "time": time.isoformat(),
                    "available_at": time.isoformat(),
                    "bid": str(bid),
                    "ask": str(bid + Decimal("0.00012")),
                }
            )
        raw = buffer.getvalue().encode()
        (root / "EURUSD_ticks.csv").write_bytes(raw)
        ticks = [{"symbol": "EURUSD", "path": "EURUSD_ticks.csv", "sha256": hashlib.sha256(raw).hexdigest()}]
    news = {
        "format": "reflex-replay-news-v1",
        "snapshots": [
            {
                "available_at": replay.isoformat(),
                "headlines_fetched_at": replay.isoformat(),
                "calendar_fetched_at": replay.isoformat(),
                "covered_from": replay.isoformat(),
                "covered_until": (end + timedelta(hours=1)).isoformat(),
                "complete": True,
                "symbols": ["EURUSD"],
                "headlines": [],
                "events": [],
            }
        ],
    }
    news_info = write_json(root / "synthetic_news.json", news)
    spec = {
        name: str(getattr(info, name))
        for name in (
            "point",
            "tick_size",
            "contract_size",
            "volume_min",
            "volume_max",
            "volume_step",
            "tick_value_profit",
            "tick_value_loss",
        )
    }
    spec.update(
        logical_symbol="EURUSD",
        name="EURUSD",
        digits=info.digits,
        currency_base="EUR",
        currency_profit="USD",
        stops_level=info.stops_level,
        freeze_level=info.freeze_level,
        profit_model="linear_contract",
        margin_model="notional",
        effective_from=start.isoformat(),
        description="ARTIFICIAL FIXTURE CONTRACT; not a broker historical specification",
    )
    manifest = {
        "format": "reflex-history-v1",
        "origin": {
            "kind": "synthetic_fixture",
            "description": f"Artificial fixture, seed {seed}. NOT real prices or profitability evidence.",
            "provider": "local fixture generator",
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "license_note": "Generated engineering data, not third-party market data",
        },
        "quote_mode": quote_mode,
        "account_currency": "USD",
        "replay_from": replay.isoformat(),
        "replay_until": end.isoformat(),
        "symbols": [spec],
        "bars": [{"symbol": "EURUSD", "path": "EURUSD_M1.csv", "sha256": hashlib.sha256(bars).hexdigest()}],
        "ticks": ticks,
        "sessions": [
            {"symbol": "EURUSD", "start": start.isoformat(), "end": (end + timedelta(minutes=1)).isoformat()}
        ],
        "news": {"path": "synthetic_news.json", "sha256": news_info["sha256"]},
    }
    write_json(root / "manifest.json", manifest)
    return root / "manifest.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="ARTIFICIAL replay fixture; never promotion evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-minutes", type=int, default=12240)
    parser.add_argument("--replay-minutes", type=int, default=3)
    parser.add_argument("--quote-mode", choices=("ticks", "ohlc_conservative"), default="ticks")
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)
    try:
        path = make_fixture(
            args.output,
            warmup_minutes=args.warmup_minutes,
            replay_minutes=args.replay_minutes,
            quote_mode=args.quote_mode,
            seed=args.seed,
        )
        print(json.dumps({"manifest": str(path), "synthetic_only": True, "promotion_eligible": False}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "refused", "error_kind": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
