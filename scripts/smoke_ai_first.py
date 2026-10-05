"""AI-FIRST + lock-first trailing smoke on the MOCK broker / PAPER engine. NEVER promotion evidence.

    python -m scripts.smoke_ai_first                 # offline scripted AI (default, no network)
    python -m scripts.smoke_ai_first --provider rule # AI outage -> technical rule mode + circuit
    python -m scripts.smoke_ai_first --provider groq # REAL Groq call for the decision step only;
                                                     # key read from OPENAI_API_KEY env var

No broker connection, no native SDK, no Telegram send, no real order. A temporary SQLite database
is created and destroyed. Engineered mock quotes prove ordering/permissions, NOT profitability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from ai.ai_brain import AIBrain
from ai.config_adjuster import AIConfigAdjuster
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from ai.learning_loop import LearningLoop
from ai.market_awareness import MarketAwarenessEngine, NewsContext, PerformanceTracker
from ai.ollama_client import ProviderContent
from ai.trade_attribution import history, sync
from ai.trade_audit import audit_trades, format_report
from core.database import Database
from core.models import Trade
from core.security import sha256_json
from core.settings import Settings
from telegram_bot.ai_notifications import AIOwnerNotifier
from trading.ai_adaptive_trailing import AdaptiveTrailing
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import DecisionContext, NewsWindow
from trading.trailing_engine import LockRegressionError, assert_never_loosens
from trading.types import ManualClock, Side, SourceKind

SMOKE_SIGNAL_ID = 1


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # No host .env / credential loading.


class ScriptedProvider:
    """Offline OpenAI-compatible double: answers by schema kind, marked simulated."""

    configured = True

    def __init__(self, trailing=("hold_to_60", "close_now")):
        self.trailing, self.calls = list(trailing), 0

    async def complete(self, messages, schema):
        self.calls += 1
        props = set(schema.get("properties", {}))
        if "action" in props:
            body = {
                "action": "open_buy",
                "confidence": 84,
                "reason": "SCRIPTED: M5/M15/H1 aligned, spread normal, no event within 2h",
                "suggested_risk_percent": 0.3,
                "suggested_target_profit": 5,
                "suggested_sl_distance": 0.0025,
                "news_risk": "low",
                "market_condition": "trending",
            }
        elif "decision" in props:
            body = {"decision": self.trailing.pop(0), "confidence": 82, "reason": "SCRIPTED trailing advice"}
        else:
            body = {
                "entry_timing": "good",
                "risk_appropriate": True,
                "news_effect": "none",
                "trailing_effective": True,
                "lessons": ["SCRIPTED: lock-first trailing kept the 60% profit"],
                "confidence": 75,
                "reason": "SCRIPTED review",
            }
        return ProviderContent("scripted", "scripted-offline", json.dumps(body), simulated=True)


class DownProvider:
    configured = True

    async def complete(self, messages, schema):
        raise ConnectionError("SMOKE provider offline")


def settings_for(directory: str, provider: str) -> SmokeSettings:
    values = dict(
        _env_file=None,
        project_root=Path(directory),
        symbols=("EURUSD",),
        telegram_owner_id=1,
        telegram_bot_token="123456789:TEST_ONLY_NOT_USED",
        max_slippage_points=2,
        atr_trailing_enabled=False,
        ai_queue_min_interval_ms=0,
        ai_trailing_timeout_seconds=2,
    )
    if provider == "groq":
        values.update(
            ai_provider="openai",
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url="https://api.groq.com/openai/v1",
            openai_model=os.environ.get("OPENAI_MODEL", "qwen/qwen3.8-27b"),
            openai_response_format="json_schema_strict",
        )
    return SmokeSettings(**values)


async def open_paper_position(engine, clock):
    engine.control.resume(1, account_key=engine.account_key)
    context = DecisionContext(
        clock.now(),
        clock.now() - timedelta(seconds=60),
        SourceKind.SYNTHETIC,
        90,
        90,
        NewsWindow(
            True, True, clock.now(), clock.now(), clock.now() + timedelta(hours=1), sha256_json({"n": 1})
        ),
    )
    plan = await engine.calculator.plan_market_order(
        "EURUSD",
        Side.BUY,
        Decimal("1.09780"),
        strategy="ai_first_smoke",
        idempotency_key=sha256_json({"s": 1}),
    )
    return await engine.execute(plan, context)


async def run(provider_name: str) -> dict:
    checks: dict[str, bool] = {}
    report: dict = {"provider": provider_name, "broker": "mock", "mode": "paper", "real_orders": 0}
    with tempfile.TemporaryDirectory(prefix="reflex-ai-first-") as directory:
        cfg = settings_for(directory, provider_name)
        if provider_name == "groq" and not cfg.openai_api_key.get_secret_value():
            return {"error": "OPENAI_API_KEY env var is empty; nothing was called"}
        database = Database(cfg)
        database.initialize()
        clock = ManualClock(datetime(2026, 10, 5, 9, tzinfo=timezone.utc))
        broker = MockMT5Client(cfg, clock=clock)
        engine = ExecutionEngine(broker, database, cfg)
        brain = None
        try:
            await engine.initialize()
            journal = DecisionJournal(database, clock)
            notifier = AIOwnerNotifier(cfg)
            adjuster = AIConfigAdjuster(database, cfg, clock, notifier=notifier)
            awareness = MarketAwarenessEngine(
                broker,
                cfg,
                clock,
                performance=PerformanceTracker(database, clock),
                overlay=adjuster,
                journal=journal,
            )
            if provider_name == "groq":
                brain = AIBrain.from_settings(
                    cfg, clock, journal=journal, adjuster=adjuster, notifier=notifier
                )
            else:
                scripted = ScriptedProvider() if provider_name == "scripted" else DownProvider()
                brain = AIBrain(
                    cfg,
                    clock,
                    provider=scripted,
                    deep_provider=scripted,
                    journal=journal,
                    adjuster=adjuster,
                    notifier=notifier,
                )

            # 1. Market awareness + AI decision (or technical fallback) ------------------------
            snapshot = await awareness.snapshot("EURUSD", news=NewsContext(state="clear"))
            prompt = snapshot.to_prompt()
            checks["snapshot_has_m5_m15_h1"] = set(prompt["timeframes"]) == {"M5", "M15", "H1"}
            decision = await brain.decide(snapshot, signal_id=SMOKE_SIGNAL_ID)
            report["entry_decision"] = {
                "source": decision.source,
                "action": decision.decision.action,
                "confidence": decision.decision.confidence,
                "executable": decision.executable,
                "reasons": list(decision.reasons),
            }
            checks["decision_journaled"] = bool(journal.recent(kind="entry"))
            if provider_name == "rule":
                for _ in range(3):
                    await brain.decide(snapshot)
                checks["circuit_open_rule_mode"] = brain.mode == "rule"
                checks["owner_notified_of_outage"] = any("rule" in t.lower() for t in notifier.outbox)
                # Owner AI_FALLBACK_MODE: BLOCK (default) => no entry; TECHNICAL_ONLY => technical score.
                blocked = await brain.decide(snapshot)
                checks["block_mode_blocks_entries"] = (
                    blocked.source == "ai_blocked" and not blocked.executable
                )
                brain.fallback_mode = lambda: "TECHNICAL_ONLY"
                required = await brain.decide(snapshot)  # AI_REQUIRE_APPROVAL=true (default).
                checks["require_approval_blocks_technical_mode"] = (
                    required.source == "ai_blocked" and "no_ai_approval" in required.reasons
                )
                brain.settings = cfg.model_copy(
                    update={"ai_require_approval": False, "ai_rule_fallback_enabled": True}
                )
                technical = await brain.decide(snapshot)
                checks["technical_mode_uses_score"] = technical.source == "rule_fallback"
                brain.settings = cfg
                report["fallback_modes"] = {
                    "BLOCK_ON_AI_FAILURE": [blocked.source, blocked.executable],
                    "TECHNICAL_ONLY": [technical.source, technical.decision.action],
                }
                brain.fallback_mode = lambda: "BLOCK_ON_AI_FAILURE"

            # 2. Config adjuster: <=50 % auto, >50 % owner approval, increase needs a strong trend,
            #    forbidden rejected --------------------------------------------------------------
            minor = adjuster.propose("risk_percent_per_trade", 0.4, reason="smoke minor")
            major = adjuster.propose("max_open_positions", 1, reason="smoke major (3 -> 1)")
            choppy = adjuster.propose(
                "max_daily_trades", 16, reason="smoke increase", context={"regime": "ranging"}
            )
            forbidden = adjuster.propose("kill_switch", 0, reason="smoke forbidden")
            results = (minor, major, choppy, forbidden)
            report["config"] = [(r.parameter, r.classification, r.status, r.reason[:40]) for r in results]
            checks["config_policy"] = (
                tuple(r.status for r in results)
                == (
                    "applied",
                    "pending",
                    "rejected",
                    "rejected",
                )
                and choppy.reason == "increase_requires_strong_trend"
            )

            # 3. Lock-first trailing on a PAPER position ------------------------------------------
            opened = await open_paper_position(engine, clock)
            if decision.executable:  # Exactly what AIFirstLayer.link_execution records after a fill.
                journal.link_signal(
                    SMOKE_SIGNAL_ID,
                    executed=True,
                    position_id=opened.position_identifier,
                    final_action="execution_filled",
                )
            trailing_brain = (
                brain if provider_name != "groq" else AIBrain(cfg, clock, provider=ScriptedProvider())
            )
            layer = AdaptiveTrailing(engine, brain=trailing_brain, journal=journal, notifier=notifier)
            manager = PositionManager(engine, adaptive=layer)
            for bid in ("1.10165", "1.10250"):
                await broker.set_tick("EURUSD", Decimal(bid), Decimal(bid) + Decimal("0.00012"))
                await engine.reconcile()
                await manager.cycle()
            with database.session() as session:
                trade = session.scalar(select(Trade))
                trailing = [
                    (r.threshold_reached, r.source, r.action, r.final_action)
                    for r in session.scalars(
                        select(AIDecisionJournal)
                        .where(AIDecisionJournal.kind == "trailing")
                        .order_by(AIDecisionJournal.id)
                    )
                ]
                report["trailing"] = trailing
                report["trade"] = {
                    "lock_level": float(trade.profit_lock_level),
                    "status": trade.status,
                    "profit_usd": str(trade.profit_usd),
                }
            checks["lock_before_ai"] = bool(trailing) and trailing[0][0] == 30
            if provider_name == "scripted":
                checks["close_now_at_60_kept_locked_profit"] = (
                    trade.status == "closed" and Decimal(trade.profit_usd) > 0
                )
            else:
                checks["mechanical_fallback"] = all(row[1] == "mechanical" for row in trailing)
            try:
                assert_never_loosens(Side.BUY, Decimal("1.1020"), Decimal("1.1010"))
                checks["lock_regression_rejected"] = False
            except LockRegressionError:
                checks["lock_regression_rejected"] = True

            # 4. Learning loop -------------------------------------------------------------------
            if trade.status == "closed":
                learning = LearningLoop(database, cfg, clock, journal, brain=brain, adjuster=adjuster)
                lesson = await learning.on_trade_closed(trade.id)
                report["lessons"] = list(lesson.lessons)
                checks["lesson_logged"] = bool(lesson.lessons)
            # 5. Who decided + trade audit ----------------------------------------------------------
            sync(database, clock, notifier=notifier)
            audit = audit_trades(database, cfg, clock, run_sync=False)
            report["audit"] = format_report(audit)
            report["who_decided"] = [(h["decided_by"], h["trailing_by"]) for h in history(database)]
            if decision.executable:
                checks["audit_clean"] = audit["clean"] and audit["checked"] == 1
            else:  # The smoke opened a position WITHOUT an AI approval: the audit must catch it.
                checks["audit_flags_unapproved_trade"] = audit["counts"]["NO_AI_APPROVAL"] == 1
            report["notifications_queued"] = len(notifier.outbox)
            report["journal_stats"] = journal.stats()["groups"]
            report["checks"] = checks
            report["ok"] = all(checks.values())
            return report
        finally:
            if brain is not None:
                await brain.close()
            await engine.shutdown()
            database.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=("scripted", "rule", "groq"), default="scripted")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.provider))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
