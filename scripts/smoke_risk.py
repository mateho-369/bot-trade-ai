"""Isolated SYNTHETIC risk/trailing/restart smoke. NEVER promotion evidence.

python -m scripts.smoke_risk
No .env/host credentials, native SDK import, Telegram/API connection or real order.
Temporary SQLite/checkpoints are destroyed after the test. Engineered quotes are
accounting/permission regression evidence, NOT a profitable trading strategy.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from core.database import Database
from core.local_operator import LocalOperator
from core.models import OrderIntent, RiskState, Trade
from core.security import sha256_json
from core.settings import Settings
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import ManualClock, Side, SourceKind, TradingDisabled


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # No host environment/.env/credential loading.


async def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="reflex-risk-synthetic-") as directory:
        cfg = SmokeSettings(
            _env_file=None,
            project_root=Path(directory),
            symbols=("EURUSD",),
            max_slippage_points=2,
            atr_trailing_enabled=False,
        )
        database = Database(cfg)
        database.initialize()
        clock = ManualClock(datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
        broker = MockMT5Client(cfg, clock=clock)
        engine = ExecutionEngine(broker, database, cfg)
        restarted = None
        try:
            await engine.initialize()
            context = DecisionContext(
                clock.now(),
                clock.now() - timedelta(seconds=60),
                SourceKind.SYNTHETIC,
                90,
                90,
                NewsWindow(
                    True,
                    True,
                    clock.now(),
                    clock.now(),
                    clock.now() + timedelta(hours=1),
                    sha256_json({"TEST_NEWS": 1}),
                ),
            )
            plan = await engine.calculator.plan_market_order(
                "EURUSD",
                Side.BUY,
                Decimal("1.09780"),
                strategy="synthetic_risk_smoke",
                idempotency_key=sha256_json({"synthetic_entry": 1}),
            )
            if plan is None:
                raise RuntimeError("synthetic test minimum lot cannot fit")
            blocked = False
            try:
                await engine.execute(plan, context)
            except TradingDisabled:
                blocked = True
            assert blocked and await broker.get_positions() == ()
            # Use a NEW intent after the definitive startup-pause veto, never retry it.
            plan = await engine.calculator.plan_market_order(
                "EURUSD",
                Side.BUY,
                Decimal("1.09780"),
                strategy="synthetic_risk_smoke",
                idempotency_key=sha256_json({"synthetic_entry": 2}),
            )
            engine.control.resume(
                LocalOperator.current(), account_key=engine.account_key
            )  # Synthetic test principal only.
            filled = await engine.execute(plan, context)
            assert await engine.execute(plan, context) == filled
            engine.control.pause(LocalOperator.current())
            await broker.set_tick("EURUSD", Decimal("1.10165"), Decimal("1.10177"))
            manager = PositionManager(engine)
            await manager.cycle()
            with database.session() as session:
                trade = session.scalar(select(Trade))
                assert trade.profit_lock_level == 30
                locked_sl = trade.sl
            before = await broker.get_account_info()
            prior_session = engine.control.session_id
            await engine.shutdown()
            restarted = ExecutionEngine(
                MockMT5Client(cfg, clock=clock, quotes={"EURUSD": (Decimal("1.10165"), Decimal("1.10177"))}),
                database,
                cfg,
            )
            await restarted.initialize()
            assert (
                restarted.database.status()["state"] == "paused"
                and restarted.control.session_id != prior_session
            )
            # Synthetic fixture quotes must be restored explicitly; checkpoint is
            # shadow execution state, never a replacement for fresh market data.
            await restarted.broker.set_tick("EURUSD", Decimal("1.10165"), Decimal("1.10177"))
            assert await restarted.execute(plan, context) == filled
            owned = restarted.logger.owned(restarted.account_key)[0]
            assert owned.ticket != filled.order_ticket
            restarted.control.kill(LocalOperator.current())
            await PositionManager(restarted).close(owned.identifier, operator=LocalOperator.current())
            assert await restarted.broker.get_positions() == ()
            final = await restarted.broker.get_account_info()
            with database.session() as session:
                trade = session.scalar(select(Trade))
                risk = session.scalar(select(RiskState))
                intents = session.scalars(select(OrderIntent)).all()
                assert trade.status == "closed" and final.balance == cfg.paper_initial_balance + trade.profit
                assert risk.accepted_entries_today == 1 and risk.reserved_risk_usd == 0
                report = {
                    "source": "synthetic",
                    "simulated_only": True,
                    "eligible_stage_evidence": False,
                    "native_sdk_imported": "MetaTrader5" in sys.modules,
                    "real_orders_sent": 0,
                    "startup_pause_veto_verified": blocked,
                    "duplicate_entry_sent_once": True,
                    "restart_started_paused": True,
                    "protective_management_while_paused_and_killed": True,
                    "verified_historical_lock_level": trade.profit_lock_level,
                    "protected_sl": str(locked_sl),
                    "volume": str(plan.order.volume),
                    "risk_account": str(plan.worst_loss_account),
                    "risk_budget_account": str(plan.risk_budget_account),
                    "original_target_usd": str(plan.target_profit_usd),
                    "net_simulated_account": str(trade.profit),
                    "ending_simulated_balance": str(final.balance),
                    "pre_restart_balance": str(before.balance),
                    "entries_today": risk.accepted_entries_today,
                    "reserved_risk_usd": str(risk.reserved_risk_usd),
                    "intent_states": sorted(row.state for row in intents),
                    "positions": 0,
                    "warning": "Engineered quotes; NOT profitability/backtest/promotion/live permission.",
                }
            return report
        finally:
            if restarted is not None:
                await restarted.shutdown()
            else:
                await engine.shutdown()
            database.close()


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
