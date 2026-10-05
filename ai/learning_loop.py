"""AI learning loop: analyse every closed trade, log lessons, propose bounded improvements.

After each closed trade (win or loss):

1. **Analyse** – a deterministic review ALWAYS runs (entry timing, risk vs plan, news effect,
   trailing effectiveness). When the AI is available its strict-JSON ``trade_review`` replaces the
   deterministic lessons; an outage never blocks the loop.
2. **Suggest** – rule lessons such as "Reduce risk during high volatility" or "Avoid trading 30 min
   before high-impact news" are written to the AI Decision Journal (kind=lesson) and are fed back
   into future prompts via ``MarketSnapshot.lessons``.
3. **Strategy weights** – per-strategy realized performance nudges weights by at most the owner's
   MAX_STRATEGY_WEIGHT_STEP; this is ALWAYS a major change routed to owner approval.
4. **Outcome** – realized P&L is attached to every journal row for that position so AI decision
   quality can be measured (``DecisionJournal.stats``).

Nightly ``deep_review`` uses AI_DEEP_MODEL (default openai/gpt-oss-120b) for a config review whose
suggestions go through ``AIConfigAdjuster`` bounds/approval like any other.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from ai.ai_first_schemas import STRATEGY_KEYS, TradeReview
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from core.database import Database
from core.models import Trade
from core.settings import Settings
from trading.types import Clock

MIN_TRADES_FOR_WEIGHTS = 10


@dataclass(frozen=True, slots=True)
class LearningReport:
    trade_id: int
    position_id: int | None
    outcome_usd: str
    review: dict
    lessons: tuple[str, ...]
    source: str
    weight_proposal: dict | None = None
    journal_ids: tuple[int, ...] = field(default_factory=tuple)


def _strategy_key(name: str) -> str | None:
    lowered = (name or "").lower()
    return next((key for key in STRATEGY_KEYS if key in lowered or key.replace("_", "") in lowered), None)


class LearningLoop:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        journal: DecisionJournal,
        *,
        brain=None,
        adjuster=None,
    ):
        self.database, self.settings, self.clock = database, settings, clock
        self.journal, self.brain, self.adjuster = journal, brain, adjuster

    # -- facts ----------------------------------------------------------------------------------
    def _facts(self, trade_id: int) -> dict:
        with self.database.session() as session:
            trade = session.get(Trade, trade_id)
            if trade is None or trade.close_time is None:
                raise ValueError("closed trade required")
            entry = None
            if trade.position_identifier is not None:
                entry = session.scalars(
                    select(AIDecisionJournal)
                    .where(
                        AIDecisionJournal.position_id == trade.position_identifier,
                        AIDecisionJournal.kind == "entry",
                    )
                    .order_by(AIDecisionJournal.id.desc())
                ).first()
            context = entry.input_summary if entry is not None else {}
            return {
                "trade_id": trade.id,
                "position_id": trade.position_identifier,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "strategy": trade.strategy,
                "profit_usd": Decimal(trade.profit_usd),
                "profit_account": Decimal(trade.profit),
                "initial_risk_usd": Decimal(trade.initial_risk_usd),
                "target_usd": Decimal(trade.target_profit_usd),
                "lock_level": float(trade.profit_lock_level),
                "minutes_open": round((trade.close_time - trade.open_time).total_seconds() / 60, 1),
                "close_reason": trade.close_reason or "unknown",
                "signal_score": trade.signal_score,
                "ai_score": trade.ai_score,
                "news_score": trade.news_score,
                "regime": context.get("regime", "unknown"),
                "news_risk_at_entry": context.get("news_risk", "unknown"),
                "ai_source_at_entry": entry.source if entry is not None else None,
                "ai_confidence_at_entry": entry.confidence if entry is not None else None,
            }

    @staticmethod
    def deterministic_review(facts: dict) -> TradeReview:
        profit, risk, lock = facts["profit_usd"], facts["initial_risk_usd"], facts["lock_level"]
        win = profit > 0
        if win:
            timing = "good"
        elif lock == 0 and facts["minutes_open"] <= 15:
            timing = "early"
        elif lock > 0:
            timing = "late"
        else:
            timing = "unknown"
        risk_ok = risk <= 0 or -profit <= risk * Decimal("1.1")
        news_risk = facts["news_risk_at_entry"]
        news_effect = (
            "hurt"
            if not win and news_risk in {"medium", "high"}
            else ("none" if news_risk == "low" else "unknown")
        )
        trailing_ok = lock > 0 and win
        lessons = []
        if not win and facts["regime"] == "volatile":
            lessons.append("Reduce risk during high volatility")
        if news_effect == "hurt":
            lessons.append("Avoid trading 30 min before high-impact news")
        if win and lock >= 90 and facts["regime"] == "trending":
            lessons.append("Increase target profit for trending markets")
        if not win and facts["regime"] == "ranging":
            lessons.append("Reduce max trades during ranging markets")
        if not risk_ok:
            lessons.append("Realized loss exceeded planned risk: review slippage/spread at entry")
        if win and lock == 0:
            lessons.append("Profit was taken without a lock tier: check trailing thresholds")
        if not lessons:
            lessons.append("No rule-based lesson: outcome consistent with plan")
        return TradeReview(
            entry_timing=timing,
            risk_appropriate=bool(risk_ok),
            news_effect=news_effect,
            trailing_effective=bool(trailing_ok),
            lessons=lessons[:5],
            confidence=60.0,
            reason=f"deterministic review: pnl {profit} USD, lock {lock:g}, regime {facts['regime']}",
        )

    # -- loop -----------------------------------------------------------------------------------
    async def on_trade_closed(self, trade_id: int, *, deep: bool = False) -> LearningReport:
        facts = self._facts(trade_id)
        review, source, model = self.deterministic_review(facts), "deterministic", None
        if self.brain is not None:
            payload = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in facts.items()}
            ai_review, ai_model = await self.brain.review_trade(payload, deep=deep)
            if ai_review is not None:
                review, source, model = ai_review, "ai", ai_model
        ids = []
        for lesson in review.lessons:
            entry = self.journal.record(
                kind="lesson",
                source=source,
                action="lesson",
                reason=lesson,
                confidence=review.confidence,
                symbol=facts["symbol"],
                position_id=facts["position_id"],
                model=model,
                input_summary={
                    "trade_id": facts["trade_id"],
                    "profit_usd": str(facts["profit_usd"]),
                    "lock_level": facts["lock_level"],
                    "regime": facts["regime"],
                    "entry_timing": review.entry_timing,
                    "risk_appropriate": review.risk_appropriate,
                    "news_effect": review.news_effect,
                    "trailing_effective": review.trailing_effective,
                },
                executed=True,
                final_action="lesson_logged",
            )
            ids.append(entry.id)
        if facts["position_id"] is not None:
            self.journal.record_outcome(
                facts["position_id"], outcome_account=facts["profit_account"], outcome_usd=facts["profit_usd"]
            )
        proposal = self.propose_weights()
        return LearningReport(
            facts["trade_id"],
            facts["position_id"],
            str(facts["profit_usd"]),
            review.model_dump(),
            tuple(review.lessons),
            source,
            proposal,
            tuple(ids),
        )

    def strategy_performance(self, *, days: int = 7) -> dict[str, dict]:
        since = self.clock.now() - timedelta(days=days)
        totals: dict[str, list[Decimal]] = defaultdict(list)
        with self.database.session() as session:
            for strategy, pnl in session.execute(
                select(Trade.strategy, Trade.profit_usd).where(
                    Trade.close_time.is_not(None), Trade.close_time >= since
                )
            ):
                key = _strategy_key(strategy)
                if key is not None:
                    totals[key].append(Decimal(pnl))
        return {
            key: {"trades": len(values), "net_usd": str(sum(values, Decimal("0")))}
            for key, values in totals.items()
        }

    def propose_weights(self) -> dict | None:
        """Bounded nudge toward the best strategy; ALWAYS a major (owner-approved) change."""
        if self.adjuster is None:
            return None
        performance = self.strategy_performance()
        if (
            sum(item["trades"] for item in performance.values()) < MIN_TRADES_FOR_WEIGHTS
            or len(performance) < 2
        ):
            return None
        ranked = sorted(performance, key=lambda k: Decimal(performance[k]["net_usd"]))
        worst, best = ranked[0], ranked[-1]
        if Decimal(performance[best]["net_usd"]) <= Decimal(performance[worst]["net_usd"]):
            return None
        weights = {k: Decimal(v) for k, v in self.settings.strategy_weights.items()}
        step = min(self.settings.max_strategy_weight_step, weights[worst])
        if step <= 0 or weights[best] == 0:
            return None
        weights[worst] -= step
        weights[best] += step
        result = self.adjuster.propose(
            "strategy_weights",
            {k: str(v) for k, v in weights.items()},
            reason=f"learning loop: {best} outperformed {worst} over 7 days",
            source="ai",
        )
        return {"status": result.status, "classification": result.classification, "value": result.value}

    async def deep_review(self, snapshot) -> dict:
        """Nightly deep config review (AI_DEEP_MODEL). Suggestions obey adjuster bounds/approval."""
        if self.brain is None:
            return {"status": "skipped", "reason": "no_brain"}
        payload = {"decision_quality": self.journal.stats()}
        reply, model = await self.brain.review_config(snapshot, deep=True, extra=payload)
        if reply is None:
            self.journal.record(
                kind="deep_review",
                source="deterministic",
                action="skipped",
                reason="deep review unavailable; no configuration change",
                input_summary={"symbol": snapshot.symbol},
            )
            return {"status": "unavailable"}
        context = {"regime": snapshot.regime, "news_risk": snapshot.news.get("risk", "high")}
        results = self.adjuster.apply_suggestion(reply, context=context) if self.adjuster is not None else []
        self.journal.record(
            kind="deep_review",
            source="ai",
            action="config_review",
            reason=reply.reason,
            confidence=reply.confidence,
            model=model,
            input_summary={"symbol": snapshot.symbol, "pause_recommended": reply.pause_recommended},
            adjustments={r.parameter: {"status": r.status, "class": r.classification} for r in results},
            executed=any(r.status == "applied" for r in results),
            final_action="adjustments_processed",
        )
        return {"status": "ok", "results": [asdict(r) for r in results]}
