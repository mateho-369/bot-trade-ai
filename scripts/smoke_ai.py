"""Isolated offline Part 7 regression: scripted HTTP → signal/risk → proof labels → toy ML.

No host .env/credentials, real provider/network/MT5 SDK/orders. Positive synthetic
scores/returns are NEVER strategy evaluation or stage evidence. No unattended bot.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from ai.ai_router import AIRouter
from ai.ai_supervisor import AISupervisor
from ai.learning_engine import LearningEngine
from ai.model_registry import ModelRegistry
from ai.model_trainer import ModelTrainer
from ai.ollama_client import OllamaClient
from ai.openai_client import OpenAIClient
from core.database import Database
from core.local_operator import LocalOperator
from core.models import AISuggestion, Trade
from core.security import sha256_json
from scripts.smoke_signals import SmokeSettings
from scripts.synthetic_ai_fixtures import ScriptedHTTP, fixture_dataset
from scripts.synthetic_signal_market import ANCHOR, EngineeredSignalMarket
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.risk_types import NewsWindow
from trading.simulation import SimulatedBroker
from trading.types import ManualClock, SourceKind, TradingDisabled


async def run():
    with tempfile.TemporaryDirectory(prefix="reflex-ai-offline-") as directory:
        cfg = SmokeSettings(
            _env_file=None,
            project_root=Path(directory),
            symbols=("EURUSD", "GBPUSD"),
            openai_api_key="FIXTURE_ONLY_NO_HTTP_NETWORK",
            atr_trailing_enabled=False,
        )
        clock = ManualClock(ANCHOR)
        database = Database(cfg)
        database.initialize()
        market = EngineeredSignalMarket(cfg, clock=clock)
        broker = SimulatedBroker(market, cfg, source_kind=SourceKind.SYNTHETIC, ledger_id="ai-smoke")
        execution = ExecutionEngine(broker, database, cfg)
        primary = ScriptedHTTP(status=503)
        fallback = ScriptedHTTP("openai")
        supervisor = None
        try:
            await execution.initialize()
            signals = SignalEngine(broker, database, cfg, profile=execution.profile)
            await signals.initialize()
            router = AIRouter(
                cfg,
                database,
                clock,
                providers=(
                    OllamaClient(cfg, transport=primary.transport),
                    OpenAIClient(cfg, transport=fallback.transport),
                ),
            )
            supervisor = AISupervisor(database, cfg, clock, execution.profile, router=router)
            await supervisor.initialize()
            now = clock.now()
            window = NewsWindow(
                True, True, now, now, now + timedelta(hours=1), sha256_json({"scripted_news": True})
            )
            ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=window)
            assert ready.approved and ready.payload()["ai_review"]["provider"] == "test"
            assert database.status()["state"] == "paused" and not await broker.get_positions()
            assert len(primary.requests) == len(fallback.requests) == 1
            # Provider asked for smaller risk, but owner has NOT enabled auto reduction.
            primary.status = 200
            primary.reply_changes = {"risk_percent": "0.2"}
            veto = await signals.evaluate("GBPUSD", reviewer=supervisor, news=window)
            assert not veto.approved
            with database.session() as session:
                proposal = session.scalar(select(AISuggestion))
                proposal_id = proposal.id
                assert proposal.status == "pending"
            execution.control.resume(LocalOperator.current(), account_key=execution.account_key)
            filled = await execution.execute_signal(ready.signal_id)
            assert await execution.execute_signal(ready.signal_id) == filled
            position = (await broker.get_positions())[0]
            clock.advance(timedelta(seconds=5))
            await market.set_tick("EURUSD", position.tp, position.tp + Decimal(".00012"))
            await execution.reconcile()
            assert (
                not await broker.get_positions() and await execution.execute_signal(ready.signal_id) == filled
            )
            learning = LearningEngine(database, cfg, clock, execution.profile)
            exported = await asyncio.to_thread(learning.export, execution.account_key)
            assert exported.dataset and len(exported.dataset.samples) == 1
            with database.session() as session:
                trade = session.scalar(select(Trade))
                assert exported.dataset.samples[0].net_usd == trade.profit
            # Explicit synthetic toy training, not enough real trade labels or any profitability research.
            toy = fixture_dataset(cfg, profile=execution.profile)
            trainer = ModelTrainer(cfg, execution.profile)
            trained = await asyncio.to_thread(trainer.train, toy, as_of=clock.now())
            registry = ModelRegistry(database, cfg, clock, execution.profile)
            candidate = await asyncio.to_thread(registry.register, trained)
            assert not candidate.active and candidate.scope == "candidate"
            await execution.shutdown()  # Release lease before research selection/projection.
            research = await asyncio.to_thread(
                registry.activate, candidate.model_id, scope="backtest", operator=LocalOperator.current()
            )
            assert research.active and research.scope == "backtest"
            try:
                await asyncio.to_thread(
                    registry.activate, candidate.model_id, scope="paper", operator=LocalOperator.current()
                )
            except TradingDisabled:
                pass
            else:
                raise AssertionError("synthetic model must never qualify for native-data paper promotion")
            await asyncio.to_thread(
                supervisor.suggestions.decide, proposal_id, operator=LocalOperator.current(), approve=True
            )
            projected = await asyncio.to_thread(
                supervisor.suggestions.apply, proposal_id, operator=LocalOperator.current()
            )
            assert projected.effective_risk_percent == Decimal(
                ".2"
            ) and cfg.effective_risk_percent == Decimal(".5")
            assert database.status()["state"] == "paused"
            return {
                "source": "synthetic",
                "scripted_provider_http": True,
                "actual_http_network_calls": 0,
                "real_orders_sent": 0,
                "native_sdk_imported": "MetaTrader5" in sys.modules,
                "confidence_is_scripted_not_an_ai_assessment": True,
                "fallback_for_availability_verified": True,
                "approved_signal_did_not_resume": True,
                "unapproved_risk_change_vetoed_entry": True,
                "duplicate_and_closed_signal_did_not_reopen": True,
                "cost_inclusive_label_verified": True,
                "reconciled_trade_labels": len(exported.dataset.samples),
                "synthetic_toy_training_rows": len(toy.samples),
                "actual_training_algorithm": trained.payload()["model"]["algorithm"],
                "purged_oos_folds": len(trained.payload()["evaluation"]["folds"]),
                "candidate_was_inactive": True,
                "research_only_selection_scope": research.scope,
                "synthetic_promotion_vetoed": True,
                "local_operator_projection_risk_percent": str(projected.effective_risk_percent),
                "running_settings_were_not_mutated": True,
                "eligible_stage_evidence": False,
                "warning": (
                    "SCRIPTED HTTP/quotes/confidence/toy labels; NOT provider evaluation, "
                    "strategy profitability, promotion or live permission."
                ),
            }
        finally:
            if supervisor is not None:
                await supervisor.close()
            await execution.shutdown()
            database.close()


def main():
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
