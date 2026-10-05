"""Offline structural/causal reconstruction only; creates no run ledger or production model selection."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backtesting.backtester import isolated_settings
from backtesting.dataset import HistoricalDataset
from backtesting.model_replay import VerifiedReplayModel
from core.settings import Settings
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind


async def verify(manifest, settings):
    history = HistoricalDataset.load(manifest)
    # No mkdir, Database, broker, provider, owner principal authentication or activation.
    private = isolated_settings(settings, history, history.root / "UNCREATED_VERIFICATION_CONTEXT")
    profile = RuntimeProfile.current(private, SourceKind.HISTORICAL)
    model = await asyncio.to_thread(VerifiedReplayModel.verify, history, private, profile)
    return {
        "status": "consistent_research_input",
        **model.summary(),
        "ledger_created": False,
        "native_broker_calls": 0,
        "provider_calls": 0,
        "genuine_owner_authenticated": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconstruct causal research model input; NOT qualification")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--env-file", type=Path, required=True, help="Explicit research config with MODEL_FILTER_ENABLED=true"
    )
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            raise ValueError("explicit research environment required")
        result = asyncio.run(verify(args.manifest, Settings(_env_file=args.env_file)))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "error_kind": type(exc).__name__,
                    "ledger_created": False,
                    "production_model_activated": False,
                    "promotion_eligible": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
