"""Isolated SYNTHETIC signal→review→risk→fill→duplicate→TP→ledger regression.

No .env/host credentials, network/provider/native SDK import or real broker order.
Engineered histories and scripted confidence are NEVER evaluation/stage evidence.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from core.database import Database
from core.local_operator import LocalOperator
from core.models import OrderIntent, RiskState, Signal, Trade
from core.security import sha256_json
from core.settings import Settings
from scripts.synthetic_signal_market import ANCHOR, EngineeredSignalMarket
from strategy.base_strategy import AIEntryReview
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.risk_types import NewsWindow
from trading.simulation import SimulatedBroker
from trading.types import ManualClock, ResultStatus, SourceKind, TradingDisabled


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # No host secrets/mode or .env can override this diagnostic.


class SyntheticReviewer:
    def __init__(self, signals: SignalEngine):
        self.signals = signals

    async def review(self, proposal, news):
        profile = self.signals.profile
        return AIEntryReview(
            self.signals.clock.now(),
            SourceKind.SYNTHETIC,
            proposal.proposal_hash,
            profile.code_hash,
            profile.model_sha256,
            news.evidence_hash,
            "approve",
            90,
            "test",
        )


async def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="reflex-signals-synthetic-") as directory:
        cfg = SmokeSettings(
            _env_file=None,
            project_root=Path(directory),
            symbols=("EURUSD", "GBPUSD"),
            atr_trailing_enabled=False,
        )
        database = Database(cfg)
        database.initialize()
        market = EngineeredSignalMarket(cfg, clock=ManualClock(ANCHOR))
        broker = SimulatedBroker(market, cfg, source_kind=SourceKind.SYNTHETIC, ledger_id="signal-smoke")
        execution = ExecutionEngine(broker, database, cfg)
        try:
            await execution.initialize()
            signals = SignalEngine(broker, database, cfg, profile=execution.profile)
            await signals.initialize()
            missing = await signals.evaluate("EURUSD")
            assert missing.state == "rejected" and "ai_unavailable_or_invalid" in missing.reasons
            try:
                await execution.execute_signal(missing.signal_id)
            except TradingDisabled:
                pass
            else:
                raise AssertionError("missing reviews must not authorize even a simulated entry")
            assert await broker.get_positions() == ()
            now = signals.clock.now()
            news = NewsWindow(
                True,
                True,
                now,
                now,
                now + timedelta(hours=1),
                sha256_json({"synthetic_news_only": True, "at": now.isoformat()}),
            )
            ready = await signals.evaluate("GBPUSD", reviewer=SyntheticReviewer(signals), news=news)
            assert ready.approved and execution.database.status()["state"] == "paused"
            assert await broker.get_positions() == ()
            execution.control.resume(LocalOperator.current(), account_key=execution.account_key)
            filled = await execution.execute_signal(ready.signal_id)
            assert filled.status == ResultStatus.FILLED
            assert await execution.execute_signal(ready.signal_id) == filled
            positions = await broker.get_positions()
            assert len(positions) == 1 and positions[0].sl == ready.stop_price
            position = positions[0]
            # Deliberately engineered quote, not a strategy performance demonstration.
            await market.set_tick(
                "GBPUSD",
                position.tp,
                position.tp + (await market.get_tick("GBPUSD")).ask - (await market.get_tick("GBPUSD")).bid,
            )
            await execution.reconcile()
            assert await broker.get_positions() == ()
            assert await execution.execute_signal(ready.signal_id) == filled
            with database.session() as session:
                trade = session.scalar(select(Trade))
                risk = session.scalar(select(RiskState))
                assert (
                    trade.status == "closed"
                    and risk.accepted_entries_today == 1
                    and risk.reserved_risk_usd == 0
                )
                signals_count = len(session.scalars(select(Signal)).all())
                intents_count = len(session.scalars(select(OrderIntent)).all())
                assert signals_count == 2 and intents_count == 1
                net, target = trade.profit, trade.target_profit_usd
            account = await broker.get_account_info()
            assert account.balance == cfg.paper_initial_balance + net
            return {
                "source": "synthetic",
                "simulated_only": True,
                "eligible_stage_evidence": False,
                "native_sdk_imported": "MetaTrader5" in sys.modules,
                "real_orders_sent": 0,
                "aggregation_consistent_historical_data": False,
                "ai_confidence_is_scripted": True,
                "missing_reviews_vetoed": True,
                "approved_signal_did_not_auto_resume": True,
                "duplicate_entry_sent_once": True,
                "closed_signal_did_not_reopen": True,
                "weights": cfg.public_config()["strategy_weights"],
                "coverage": ready.payload()["technical"]["coverage"],
                "agreement": ready.payload()["technical"]["agreement"],
                "technical_score": ready.score,
                "ai_score_scripted": ready.context.ai_confidence,
                "finalized_bars_each_timeframe": 300,
                "volume": str(position.volume),
                "sl": str(position.sl),
                "original_target_usd": str(target),
                "net_simulated_account": str(net),
                "ending_simulated_balance": str(account.balance),
                "signal_rows": signals_count,
                "entry_intents": intents_count,
                "positions": 0,
                "warning": (
                    "Engineered OHLC/quotes/reviews; NOT strategy profitability, "
                    "promotion or live permission."
                ),
            }
        finally:
            await execution.shutdown()
            database.close()


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
