"""Read-only symbol discovery: broker suffixes, contract specs, spread caps, cent accounts.

Run:  python -m scripts.resolve_symbols --env-file .env            (report only)
      python -m scripts.resolve_symbols --env-file .env --write    (persist to .env)
      python -m scripts.resolve_symbols --env-file .env --suffix m (force a suffix)

MT5_BACKEND=mock reads the offline synthetic catalogue. A real backend attaches
to the local terminal with PAPER flags forced and the default DENY-ALL write
authority (like scripts.check_mt5_readonly): it reads account currency, the
Market Watch list, symbol metadata, one quote and recent candles. It NEVER sends,
modifies or closes an order. ``--write`` only updates these keys in the given
file: SYMBOL_ALIASES_JSON, SYMBOL_SPREAD_LIMITS_JSON, SYMBOL_NEWS_CURRENCIES_JSON,
ACCOUNT_TO_USD_SYMBOLS_JSON and (if the terminal differs) ACCOUNT_CURRENCY.
Owner-configured entries always win. Review the result, then restart PAUSED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.dont_write_bytecode = True

from pydantic import ValidationError  # noqa: E402

from core.settings import Settings  # noqa: E402
from trading.symbol_resolver import DEFAULT_SPREAD_MULTIPLIER, discover, update_env_file  # noqa: E402
from trading.types import BrokerError, IdentityChanged  # noqa: E402


def _suffix(value: str) -> str:
    if value != "" and not re.fullmatch(r"[A-Za-z0-9._\-#+!]{1,9}", value):
        raise argparse.ArgumentTypeError("invalid broker suffix")
    return value


def _multiplier(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("multiplier must be a number") from None
    if not number.is_finite() or not Decimal("1") <= number <= Decimal("10"):
        raise argparse.ArgumentTypeError("multiplier must be between 1 and 10")
    return number


def load_settings(env_file: Path) -> Settings:
    settings = Settings(_env_file=env_file, project_root=env_file.resolve().parent)
    if settings.mt5_backend == "mock":
        return settings
    # Discovery never needs execution: force the paper/demo flags like check_mt5_readonly.
    return Settings(
        _env_file=env_file,
        project_root=env_file.resolve().parent,
        demo_mode=True,
        paper_trading=True,
        live_trading=False,
        backtest_mode=False,
    )


async def _discover_mock(settings: Settings, **options):
    from trading.mock_mt5 import MockMarketData

    market = MockMarketData(settings)
    await market.initialize()
    try:
        return await discover(market, settings, **options)
    finally:
        await market.shutdown()


async def _discover_native(settings: Settings, **options):
    from trading.mt5_client import MT5Client

    client = MT5Client(settings)  # Default DENY-ALL write authority.
    try:
        async with client:
            return await discover(client, settings, **options)
    except IdentityChanged:
        observed = client.observed_account_currency
        if not observed or observed == settings.account_currency:
            raise
    # The terminal is e.g. a USC cent account while .env declares USD: read again
    # under the observed currency (still read-only) and propose ACCOUNT_CURRENCY.
    corrected = settings.model_copy(update={"account_currency": observed})
    async with MT5Client(corrected) as client:
        report = await discover(client, corrected, account_currency=observed, **options)
    return replace(report, declared_currency=settings.account_currency)


async def run(settings: Settings, *, suffix: str | None, multiplier: Decimal):
    options = {"preferred_suffix": suffix, "spread_multiplier": multiplier}
    if settings.mt5_backend == "mock":
        return await _discover_mock(settings, **options)
    return await _discover_native(settings, **options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only broker symbol discovery (no orders)")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--suffix", type=_suffix, default=None, help="prefer this broker suffix, e.g. m or c")
    parser.add_argument(
        "--spread-multiplier",
        type=_multiplier,
        default=DEFAULT_SPREAD_MULTIPLIER,
        help="spread cap = typical broker spread x this (default 2)",
    )
    parser.add_argument("--refresh-spreads", action="store_true", help="replace existing spread caps")
    parser.add_argument("--write", action="store_true", help="persist the results into --env-file")
    args = parser.parse_args(argv)
    if not args.env_file.is_file():
        print(json.dumps({"error": "env_file_missing", "real_orders_sent": 0}))
        return 2
    try:
        settings = load_settings(args.env_file)
    except ValidationError:
        print(json.dumps({"error": "invalid_configuration", "detail": "Run main.py check-config."}))
        return 2
    try:
        report = asyncio.run(run(settings, suffix=args.suffix, multiplier=args.spread_multiplier))
    except BrokerError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail_printed": False, "real_orders_sent": 0}))
        return 2
    updates = report.env_updates(settings, refresh_spreads=args.refresh_spreads)
    output = report.to_dict()
    output["env_updates"] = updates
    output["written_keys"] = []
    if args.write:
        original = args.env_file.read_text(encoding="utf-8")
        written = update_env_file(args.env_file, updates)
        try:
            Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        except ValidationError:
            args.env_file.write_text(original, encoding="utf-8")
            print(json.dumps({"error": "resolved_values_rejected_by_settings", "restored": True}))
            return 2
        output["written_keys"] = written
        output["next_step"] = "Review .env, run main.py check-config, then restart the runtime PAUSED."
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if not output["disabled_symbols"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
