"""Windows-only explicit connection diagnostic. NEVER sends a broker order.

Run: python -m scripts.check_mt5_readonly --env-file .env
Force PAPER flags even if the supplied environment attempts demo/live execution.
May launch terminal64.exe/login with local .env credentials; invoke only locally.
"""

import argparse
import asyncio
import json

from pydantic import ValidationError

from core.settings import Settings
from trading.mt5_client import MT5Client
from trading.symbol_manager import SymbolManager
from trading.types import BrokerError


async def inspect(settings: Settings) -> dict:
    async with MT5Client(settings) as client:  # Default DENY-ALL write authority.
        account = await client.get_account_info()
        symbols = SymbolManager(client, settings)
        await symbols.initialize()
        metadata = []
        for item in symbols.enabled():
            tick = await client.get_tick(item.native)
            tick.fresh(client.clock, settings.max_tick_age_seconds)
            metadata.append(
                {
                    "logical": item.logical,
                    "native": item.native,
                    "tick_size": str(item.info.tick_size),
                    "point": str(item.info.point),
                    "volume_min": str(item.info.volume_min),
                    "volume_step": str(item.info.volume_step),
                    "spread_points": str(tick.spread_points(item.info)),
                    "spread_limit_points": item.spread_limit_points,
                    "news_exposure_configured": symbols.news_exposure_known(item.logical),
                }
            )
        return {
            "diagnostic": "read-only",
            "real_orders_sent": 0,
            "mode_forced": "paper",
            "account_key": account.key,
            "actual_account_kind": account.kind.value,
            "currency": account.currency,
            "balance": str(account.balance),
            "equity": str(account.equity),
            "symbols": metadata,
            "disabled_symbols": symbols.errors(),
            "health": client.health(),
            "warning": "Connection success is NOT stage approval, news coverage, or live readiness.",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    try:
        settings = Settings(
            _env_file=args.env_file,
            mt5_backend="real",
            demo_mode=True,
            paper_trading=True,
            live_trading=False,
            backtest_mode=False,
        )
        report = asyncio.run(inspect(settings))
    except ValidationError:
        print(
            json.dumps(
                {
                    "error": "invalid_configuration",
                    "detail": "Run main.py check-config; raw values suppressed.",
                }
            )
        )
        return 2
    except BrokerError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc), "real_orders_sent": 0}))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
