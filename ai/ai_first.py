"""Composition of the AI-FIRST layer for one runtime (explicit, no module globals).

``AIFirstLayer`` wires: DecisionJournal, AIConfigAdjuster, AIOwnerNotifier, PerformanceTracker,
MarketAwarenessEngine, AIBrain (Groq via the OpenAI-compatible client), AIFirstReviewer (entries),
AdaptiveTrailing (lock-first) and LearningLoop. The scheduler calls its jobs:

* ``reviewer``               – EntryReviewer used by SignalEngine.evaluate (every signal interval)
* ``trailing``               – lock-first layer inside PositionManager (every position interval)
* ``learn_closed_trades``    – learning loop for newly closed trades (every 5 minutes)
* ``config_review``          – AI config suggestions + owner-decision sync (every 30 minutes)
* ``nightly_review``         – deep model review + daily AI adjustment summary (daily)
* ``publish_status``         – AI availability heartbeat for the dynamic limits (every 60 s and on
                               every circuit open/close); stale/rule => owner default limits
* ``notifier.drain``         – owner Telegram notifications

AI_FALLBACK_MODE is resolved per decision from ``trading.ai_controls.fallback_mode`` (owner DB
override, else the setting), so /ai_fallback_block and /ai_fallback_technical apply without restart.

Constructing the layer performs no provider request and no broker write.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ai.ai_brain import AIBrain
from ai.ai_first_reviewer import AIFirstReviewer
from ai.config_adjuster import AIConfigAdjuster
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from ai.learning_loop import LearningLoop
from ai.market_awareness import MarketAwarenessEngine, PerformanceTracker
from core.models import Trade
from telegram_bot.ai_notifications import AIOwnerNotifier
from trading.ai_adaptive_trailing import AdaptiveTrailing
from trading.ai_controls import fallback_mode, publish_ai_status
from trading.types import BrokerError, ResultStatus, RiskViolation

EXECUTED = frozenset({ResultStatus.FILLED.value, ResultStatus.PARTIAL.value, ResultStatus.ACCEPTED.value})


class AIFirstLayer:
    def __init__(self, database, settings, broker, engine, supervisor, *, brain=None, provider=None):
        clock = broker.clock
        self.database, self.settings, self.engine, self.clock = database, settings, engine, clock
        self.alerts = None  # Optional app.alerts.AlertCenter attached by the runtime.
        self.journal = DecisionJournal(database, clock)
        self.notifier = AIOwnerNotifier(settings, secrets=database.secrets, mode_provider=self.fallback_mode)
        self.adjuster = AIConfigAdjuster(
            database,
            settings,
            clock,
            suggestions=getattr(supervisor, "suggestions", None),
            notifier=self.notifier,
        )
        self.performance = PerformanceTracker(database, clock)
        self.awareness = MarketAwarenessEngine(
            broker, settings, clock, performance=self.performance, overlay=self.adjuster, journal=self.journal
        )
        common = {
            "journal": self.journal,
            "adjuster": self.adjuster,
            "notifier": self.notifier,
            "fallback_mode": self.fallback_mode,
            "limits": self.adjuster.limits,
            "status_listener": self._status_changed,
        }
        if brain is not None:
            self.brain = brain
            if brain.limits is None:
                brain.limits = self.adjuster.limits
            if brain.status_listener is None:
                brain.status_listener = self._status_changed
        elif provider is not None:
            self.brain = AIBrain(settings, clock, provider=provider, deep_provider=provider, **common)
        else:
            self.brain = AIBrain.from_settings(
                settings, clock, provider_listener=self._provider_event, **common
            )
        self.reviewer = AIFirstReviewer(
            self.brain,
            self.awareness,
            settings,
            engine.profile,
            clock,
            inner=supervisor,
            trades_today=self.trades_today,
            account_key=engine.account_key,
        )
        self.trailing = AdaptiveTrailing(
            engine,
            brain=self.brain,
            journal=self.journal,
            adjuster=self.adjuster,
            awareness=self.awareness,
            notifier=self.notifier,
        )
        self.learning = LearningLoop(
            database, settings, clock, self.journal, brain=self.brain, adjuster=self.adjuster
        )

    # -- owner controls / status ----------------------------------------------------------------
    def fallback_mode(self) -> str:
        return fallback_mode(self.database, self.settings)

    def publish_status(self) -> dict:
        mode = self.brain.mode
        publish_ai_status(self.database, self.clock, mode=mode, detail="heartbeat")
        return {"state": "published", "mode": mode, "fallback_mode": self.fallback_mode()}

    def _status_changed(self, mode: str, detail: str) -> None:
        try:
            publish_ai_status(self.database, self.clock, mode=mode, detail=detail)
        except Exception:
            pass  # The 60 s heartbeat repairs it; a stale status already means owner defaults.
        if self.alerts is not None:
            if mode == "ai":
                self.alerts.emit("WARNING", "AI Provider", "AI provider answering again; AI mode restored")
            else:
                fallback = self.fallback_mode()
                self.alerts.emit(
                    "ERROR",
                    "AI Provider",
                    "AI circuit open: " + detail[:80],
                    details={"fallback_mode": fallback},
                    action=(
                        "New entries blocked until AI recovers (BLOCK_ON_AI_FAILURE)"
                        if fallback == "BLOCK_ON_AI_FAILURE"
                        else "Technical fallback active (TECHNICAL_ONLY)"
                    ),
                )

    def _provider_event(self, event: str, label: str, detail: str) -> None:
        """Registry per-label telemetry: audit every failure (for /ai_stats), alert circuit changes."""
        try:
            if event == "failure":
                self.database.audit("ai.provider_failure", "ai", {"label": label, "reason": detail})
            else:
                details = {"label": label, "event": event, "reason": detail}
                self.database.audit("ai.provider_circuit", "ai", details)
        except Exception:
            pass  # Telemetry never changes a decision.
        self.notifier.provider_event(event, label, detail)
        if self.alerts is not None and event in {"circuit_open", "circuit_closed"}:
            self.alerts.emit(
                "ERROR" if event == "circuit_open" else "WARNING",
                "AI Provider",
                f"AI provider {label} circuit {'OPEN' if event == 'circuit_open' else 'CLOSED'} ({detail})",
                details={"label": label},
            )

    # -- facts ----------------------------------------------------------------------------------
    def trades_today(self) -> int:
        zone = ZoneInfo(self.settings.trading_day_timezone)
        local = self.clock.now().astimezone(zone)
        start = datetime.combine(local.date(), time(0), tzinfo=zone)
        with self.database.session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Trade)
                    .where(Trade.account_key == self.engine.account_key, Trade.open_time >= start)
                )
                or 0
            )

    # -- jobs -----------------------------------------------------------------------------------
    def link_execution(self, signal_id: int, outcome) -> None:
        status = outcome.status.value if outcome is not None else "no_effect"
        self.journal.link_signal(
            signal_id,
            executed=status in EXECUTED,
            position_id=getattr(outcome, "position_identifier", None),
            final_action=f"execution_{status}",
        )
        self.sync_attribution()

    def sync_attribution(self) -> dict:
        """Who-decided rows + one entry/close Telegram message per trade (idempotent)."""
        from ai.trade_attribution import sync

        return sync(self.database, self.clock, notifier=self.notifier, account_key=self.engine.account_key)

    def trade_audit(self) -> dict:
        """Daily audit: every trade needs a matching valid AI approval; any flag alerts the owner."""
        from ai.trade_audit import audit_trades, format_report

        self.sync_attribution()
        report = audit_trades(self.database, self.settings, self.clock, run_sync=False)
        if not report["clean"]:
            self.notifier.summary(format_report(report, limit=5))
            if self.alerts is not None:
                self.alerts.emit(
                    "ERROR",
                    "Trade Audit",
                    f"Trade audit: {len(report['flags'])} flag(s) in {report['checked']} trades",
                    details=report["counts"],
                    action="Run /audit for details",
                )
        return {"state": "clean" if report["clean"] else "flagged", **report["counts"]}

    async def learn_closed_trades(self, *, limit: int = 5) -> dict:
        synced = self.adjuster.sync_owner_decisions()  # Owner /approve reaches the overlay within 5 min.
        with self.database.session() as session:
            reviewed = set(
                session.scalars(
                    select(AIDecisionJournal.position_id).where(
                        AIDecisionJournal.kind == "lesson", AIDecisionJournal.position_id.is_not(None)
                    )
                ).all()
            )
            candidates = [
                trade_id
                for trade_id, position_id in session.execute(
                    select(Trade.id, Trade.position_identifier)
                    .where(Trade.account_key == self.engine.account_key, Trade.close_time.is_not(None))
                    .order_by(Trade.close_time.desc())
                    .limit(50)
                )
                if position_id is not None and position_id not in reviewed
            ][:limit]
        reports = []
        for trade_id in candidates:
            report = await self.learning.on_trade_closed(trade_id)
            reports.append({"trade_id": trade_id, "source": report.source, "lessons": list(report.lessons)})
        return {"state": "reviewed", "trades": reports, "synced": synced}

    async def config_review(self) -> dict:
        synced = self.adjuster.sync_owner_decisions()
        if not self.settings.symbols:
            return {"state": "no_symbols", "synced": synced}
        try:
            snapshot = await self.awareness.snapshot(
                self.settings.symbols[0], account_key=self.engine.account_key
            )
        except (BrokerError, RiskViolation, ValueError, KeyError, TypeError):
            return {"state": "market_unavailable", "synced": synced}
        reply, model = await self.brain.review_config(snapshot)
        if reply is None:
            return {"state": "ai_unavailable", "synced": synced, "mode": self.brain.mode}
        context = {"regime": snapshot.regime, "news_risk": snapshot.news.get("risk", "high")}
        results = self.adjuster.apply_suggestion(reply, context=context)
        self.journal.record(
            kind="config",
            source="ai",
            action="config_review",
            reason=reply.reason,
            confidence=reply.confidence,
            model=model,
            symbol=snapshot.symbol,
            input_summary=snapshot.summary(),
            adjustments={r.parameter: {"status": r.status, "class": r.classification} for r in results},
            executed=any(r.status == "applied" for r in results),
            final_action="pause_advisory" if reply.pause_recommended else "adjustments_processed",
        )
        return {
            "state": "reviewed",
            "synced": synced,
            "results": [(r.parameter, r.status, r.classification) for r in results],
            "pause_recommended": reply.pause_recommended,
        }

    async def nightly_review(self) -> dict:
        if not self.settings.symbols:
            return {"state": "no_symbols"}
        try:
            snapshot = await self.awareness.snapshot(
                self.settings.symbols[0], account_key=self.engine.account_key
            )
        except (BrokerError, RiskViolation, ValueError, KeyError, TypeError):
            return {"state": "market_unavailable"}
        result = await self.learning.deep_review(snapshot)
        summary = self.adjuster.daily_summary_text()
        self.notifier.summary(summary)
        return {**result, "adjustment_summary": summary}

    async def close(self) -> None:
        await self.brain.close()
