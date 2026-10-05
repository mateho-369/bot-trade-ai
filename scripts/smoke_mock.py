"""One deterministic SYNTHETIC trade, not a strategy evaluation/backtest.

Run: python -m scripts.smoke_mock
Ignores host environment and .env credentials. Imports no native MT5 module.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.security import sha256_json
from core.settings import Settings
from trading.mock_mt5 import MockMT5Client
from trading.order_calculator import OrderCalculator
from trading.symbol_manager import SymbolManager
from trading.types import ManualClock, Side


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # NEVER consume credentials/mode/risk from the host.


async def run() -> dict:
    settings = SmokeSettings(_env_file=None, symbols=("XAUUSD",))
    clock = ManualClock(datetime(2026, 10, 2, 8, tzinfo=timezone.utc))
    async with MockMT5Client(settings, clock=clock) as broker:
        symbols = SymbolManager(broker, settings)
        await symbols.initialize()
        managed = symbols.resolve("XAUUSD")
        candles = await broker.get_candles(managed.native, "M5", 300)
        plan = await OrderCalculator(broker, settings).plan_market_order(
            managed.native,
            Side.BUY,
            Decimal("2608"),
            strategy="synthetic_smoke",
            idempotency_key=sha256_json({"smoke": 1}),
        )
        if plan is None:
            raise RuntimeError("default smoke risk budget unexpectedly cannot fit minimum lot")
        opened = await broker.open_market_buy(plan.order)
        assert await broker.open_market_buy(plan.order) == opened
        positions = await broker.get_positions()
        assert len(positions) == 1
        clock.advance(timedelta(seconds=1))
        await broker.set_tick(managed.native, plan.order.tp, plan.order.tp + Decimal("0.20"))
        deals = await broker.get_deals(datetime(2026, 10, 2, 8, tzinfo=timezone.utc))
        account = await broker.get_account_info()
        assert len(deals) == 2 and await broker.get_positions() == ()
        net = sum(deal.net for deal in deals)
        assert account.balance == settings.paper_initial_balance + net
        return {
            "source": broker.source_kind.value,
            "simulated_only": True,
            "eligible_stage_evidence": False,
            "native_sdk_imported": "MetaTrader5" in sys.modules,
            "real_orders_sent": 0,
            "mode": settings.mode.value,
            "finalized_candles": len(candles),
            "duplicate_open_sent_once": len(deals) == 2,
            "volume": str(plan.order.volume),
            "nominal_risk_account": str(plan.worst_loss_account),
            "risk_budget_account": str(plan.risk_budget_account),
            "target_usd": str(plan.target_profit_usd),
            "simulated_net_usd": str(net),
            "ending_simulated_balance": str(account.balance),
            "deals": len(deals),
            "positions": 0,
            "warning": "Artificial quotes; this is NOT profitability evidence or permission to trade.",
        }


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
