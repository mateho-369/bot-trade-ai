"""AI-adaptive trailing stop with a LOCK-FIRST design.

    profit reaches 30 % of target
        └─► 1. MECHANICAL lock 30 % (TrailingEngine.plan + protect_sl) – instant, no AI involved
        └─► 2. confirm the stop is in place (fresh broker/paper position)
        └─► 3. ONLY THEN ask the AI (hard timeout AI_TRAILING_TIMEOUT_SECONDS, default 2 s)
                hold_to_60 | hold_to_90 | close_now | tighten_lock          (after 30)
                hold_to_90 | close_now | tighten_lock                       (after 60)
                extend_tp_to_120 | close_now | tighten_lock                 (after 90)
        └─► 4. AI slow / unavailable / invalid JSON / low confidence / not allowed at this
                threshold  =>  "AI unavailable, using mechanical trailing": continue 30→60→90.

Invariants (enforced in code and tests):

* The mechanical lock for a threshold is ALWAYS sent before any AI call. If the lock was not
  confirmed (rejected/deferred), the AI is NOT consulted for that threshold.
* The AI can only hold longer, close earlier, tighten, or (with ALLOW_TP_EXTENSION=true and the
  existing bound PositionReview checks) extend TP to 120 %. It can never remove or loosen a lock:
  every stop the AI path requests passes ``assert_never_loosens`` or raises LockRegressionError.
* Closing and modifying go through the normal ExecutionEngine (paper/mock by default, native
  adapters DenyAllWrites); owner pause/kill never blocks PROTECTIVE work.
* Every threshold decision is written to ``ai_decision_journal`` (position_id, threshold, AI
  decision/confidence/reason, final action; outcome attached when the trade closes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from trading.risk_types import PositionReview, position_review_hash
from trading.trailing_engine import LockRegressionError, assert_never_loosens
from trading.types import BrokerError, Position, ResultStatus, RiskViolation

logger = logging.getLogger(__name__)

THRESHOLDS = (30, 60, 90)
CONFIRMED = frozenset({ResultStatus.FILLED, ResultStatus.ACCEPTED, ResultStatus.NO_CHANGE})
TIGHTEN_FRACTION = Decimal("0.5")
MECHANICAL_WARNING = "AI unavailable, using mechanical trailing"


@dataclass(slots=True)
class _PositionState:
    handled: set[int] = field(default_factory=set)
    hold_target: int | None = None


@dataclass(frozen=True, slots=True)
class TrailingEvent:
    position_id: int
    threshold: int
    mechanical_sl: str | None
    ai_decision: str | None
    ai_confidence: float | None
    ai_reason: str
    final_action: str
    source: str
    journal_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "identifier": self.position_id,
            "operation": "ai_trailing",
            "threshold": self.threshold,
            "ai_decision": self.ai_decision,
            "ai_confidence": self.ai_confidence,
            "final_action": self.final_action,
            "source": self.source,
        }


def session_context(now: datetime) -> dict:
    """Minutes to the 21:00 UTC daily rollover and to the Friday 21:00 UTC weekly close."""
    rollover = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if rollover <= now:
        rollover += timedelta(days=1)
    days_to_friday = (4 - now.weekday()) % 7
    weekly = (now + timedelta(days=days_to_friday)).replace(hour=21, minute=0, second=0, microsecond=0)
    if weekly <= now:
        weekly += timedelta(days=7)
    return {
        "minutes_to_daily_rollover": int((rollover - now).total_seconds() // 60),
        "minutes_to_weekly_close": int((weekly - now).total_seconds() // 60),
    }


class AdaptiveTrailing:
    def __init__(
        self,
        engine,
        *,
        brain=None,
        journal=None,
        adjuster=None,
        awareness=None,
        news_context=None,
        notifier=None,
    ):
        self.engine, self.brain, self.journal = engine, brain, journal
        self.adjuster, self.awareness, self.news_context = adjuster, awareness, news_context
        self.notifier = notifier
        self.settings, self.clock = engine.settings, engine.clock
        self._states: dict[int, _PositionState] = {}

    # -- helpers ------------------------------------------------------------------------------
    def _state(self, position_id: int) -> _PositionState:
        state = self._states.get(position_id)
        if state is None:
            handled = self.journal.trailing_thresholds(position_id) if self.journal is not None else set()
            state = self._states[position_id] = _PositionState(handled=set(handled))
        return state

    async def _fresh(self, identifier: int) -> Position | None:
        return next((p for p in await self.engine.broker.get_positions() if p.identifier == identifier), None)

    async def apply_ai_stop(self, position: Position, proposed_sl: Decimal, *, lock_level: float):
        """The ONLY way the AI path may move a stop. Raises LockRegressionError if it would loosen."""
        assert_never_loosens(position.side, position.sl, proposed_sl)
        return await self.engine.protect_sl(
            position.ticket, position.identifier, proposed_sl, lock_level=lock_level
        )

    async def _context(self, position: Position, owned, threshold: int) -> dict:
        trailing = self.engine.trailing
        tick = await self.engine.broker.get_tick(position.symbol)
        price = tick.exit(position.side)
        net = await trailing.net_at_price(position, owned, price, stop=False)
        progress = net * 100 / owned.target_usd
        following = next((t for t in (*THRESHOLDS, 120) if t > threshold), None)
        distance = None
        if following is not None:
            try:
                goal = owned.target_usd * Decimal(following) / 100
                target_price = await trailing.calculator.price_for_profit_usd(
                    position.symbol,
                    position.side,
                    position.volume,
                    position.entry_price,
                    goal,
                    net_offset_account=await trailing._offset(position, owned),
                    exit_slippage_points=self.settings.max_slippage_points,
                )
                distance = str(abs(target_price - price))
            except (BrokerError, RiskViolation, ArithmeticError):
                distance = None
        context = {
            "format": "reflex-trailing-context-v1",
            "symbol": position.symbol,
            "side": position.side.value.lower(),
            "entry": str(position.entry_price),
            "current_price": str(price),
            "current_sl": str(position.sl),
            "current_tp": str(position.tp),
            "progress_percent_of_target": str(progress.quantize(Decimal("0.1"))),
            "target_profit_usd": str(owned.target_usd),
            "next_threshold_percent": following,
            "distance_to_next_threshold_price": distance,
            "session": session_context(self.clock.now()),
        }
        if self.awareness is not None:
            try:
                news = None
                if self.news_context is not None:
                    news = self.news_context(position.symbol, None)
                snapshot = await self.awareness.snapshot(position.symbol, native=position.symbol, news=news)
                context["trend"] = {tf: v.get("ema_trend") for tf, v in snapshot.timeframes.items()}
                context["adx"] = {tf: v.get("adx") for tf, v in snapshot.timeframes.items()}
                primary = snapshot.timeframes.get(self.settings.primary_timeframe, {})
                context["rsi"], context["atr"] = primary.get("rsi"), primary.get("atr")
                context["volatility"] = {
                    "atr_vs_median": primary.get("atr_vs_median"),
                    "regime": snapshot.regime,
                }
                context["news_risk"] = snapshot.news.get("risk")
            except (BrokerError, RiskViolation, ValueError, KeyError, TypeError):
                context["news_risk"] = "unknown"
        else:
            context["news_risk"] = "unknown"
        return context

    def _record(
        self, event: TrailingEvent, *, model: str | None = None, context: dict | None = None
    ) -> TrailingEvent:
        if self.journal is None:
            return event
        entry = self.journal.record(
            kind="trailing",
            source=event.source,
            action=event.ai_decision or "mechanical",
            reason=event.ai_reason,
            confidence=event.ai_confidence,
            symbol=(context or {}).get("symbol"),
            position_id=event.position_id,
            threshold_reached=event.threshold,
            model=model,
            input_summary={k: v for k, v in (context or {}).items() if k not in {"format"}},
            adjustments={"mechanical_sl": event.mechanical_sl},
            executed=event.final_action not in {"mechanical_continue", "ai_action_failed"},
            final_action=event.final_action,
        )
        recorded = TrailingEvent(
            **{**{s: getattr(event, s) for s in event.__slots__}, "journal_id": entry.id}
        )
        if self.notifier is not None:
            self.notifier.trailing(recorded)
        return recorded

    # -- main entry point ---------------------------------------------------------------------
    async def manage(self, position: Position, owned, *, candles=None) -> list[dict]:
        outcomes: list[dict] = []
        locked = float(owned.lock_level)
        mechanical_sl = None
        # STEP 1 — MECHANICAL LOCK FIRST. No AI call can happen before this completes.
        plan = await self.engine.trailing.plan(position, owned, candles=candles)
        if plan is not None:
            result = await self.engine.protect_sl(
                position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level
            )
            outcomes.append(
                {
                    "identifier": position.identifier,
                    "operation": "sl",
                    "status": result.status.value,
                    "reason": plan.reason,
                    "requested_lock_level": plan.lock_level,
                }
            )
            if result.status not in CONFIRMED:
                return outcomes  # Lock not secured => never consult the AI for this threshold.
            locked, mechanical_sl = max(locked, float(plan.lock_level)), str(plan.sl)
        if not (self.settings.ai_adaptive_trailing_enabled and self.brain is not None):
            return outcomes
        state = self._state(position.identifier)
        threshold = max((t for t in THRESHOLDS if t <= locked and t not in state.handled), default=None)
        if threshold is None:
            return outcomes
        # STEP 2 — confirm the stop really is in place before asking anything.
        current = await self._fresh(position.identifier)
        if current is None or current.sl <= 0:
            return outcomes
        if mechanical_sl is not None and current.side.sign * (current.sl - Decimal(mechanical_sl)) < 0:
            return outcomes
        state.handled.update(t for t in THRESHOLDS if t <= threshold)
        skip = set(self.adjuster.effective().get("skip_trailing_levels", ())) if self.adjuster else set()
        if threshold in skip or (state.hold_target is not None and threshold < state.hold_target):
            reason = (
                f"AI consultation skipped at {threshold} (overlay)"
                if threshold in skip
                else f"holding to {state.hold_target} per AI; mechanical lock {threshold} kept"
            )
            event = TrailingEvent(
                current.identifier,
                threshold,
                mechanical_sl or str(current.sl),
                None,
                None,
                reason,
                "hold",
                "mechanical",
            )
            outcomes.append(self._record(event).as_dict())
            return outcomes
        context = await self._context(current, owned, threshold)
        decision, model, failure = await self.brain.trailing_advice(context, threshold=threshold)
        if decision is None or decision.confidence < self.settings.ai_confidence_threshold:
            why = failure or "low_confidence"
            logger.warning(
                "%s (position %s, threshold %s, %s)", MECHANICAL_WARNING, current.identifier, threshold, why
            )
            event = TrailingEvent(
                current.identifier,
                threshold,
                mechanical_sl or str(current.sl),
                None if decision is None else decision.decision,
                None if decision is None else decision.confidence,
                f"{MECHANICAL_WARNING}: {why}",
                "mechanical_continue",
                "mechanical",
            )
            outcomes.append(self._record(event, model=model, context=context).as_dict())
            return outcomes
        # STEP 3 — execute the AI decision (it can never loosen the lock).
        final = await self._execute(decision.decision, current, owned, locked, decision.confidence)
        if decision.decision in {"hold_to_60", "hold_to_90"}:
            state.hold_target = int(decision.decision.rsplit("_", 1)[1])
        event = TrailingEvent(
            current.identifier,
            threshold,
            mechanical_sl or str(current.sl),
            decision.decision,
            decision.confidence,
            decision.reason,
            final,
            "ai",
        )
        outcomes.append(self._record(event, model=model, context=context).as_dict())
        return outcomes

    async def _execute(
        self, decision: str, position: Position, owned, locked: float, confidence: float
    ) -> str:
        try:
            if decision in {"hold_to_60", "hold_to_90"}:
                return decision
            if decision == "close_now":
                result = await self.engine.close_owned(position.ticket, position.identifier)
                return "closed_with_locked_profit" if result.status in CONFIRMED else "ai_action_failed"
            if decision == "tighten_lock":
                sl = await self.engine.trailing.tighten(position, owned, fraction=TIGHTEN_FRACTION)
                if sl is None:
                    return "tighten_not_possible_lock_kept"
                result = await self.apply_ai_stop(position, sl, lock_level=locked)
                return "lock_tightened" if result.status in CONFIRMED else "ai_action_failed"
            if decision == "extend_tp_to_120":
                if not self.settings.allow_tp_extension:
                    return "extension_not_permitted_lock_kept"
                profile = self.engine.profile
                review = PositionReview(
                    self.clock.now(),
                    self.engine.data_source,
                    float(confidence),
                    momentum_continues=True,
                    volatility_safe=True,
                    position_identifier=position.identifier,
                    position_hash=position_review_hash(position),
                    code_hash=profile.code_hash,
                    model_sha256=profile.model_sha256,
                )
                tp = await self.engine.trailing.extension(position, owned, review)
                if tp is None:
                    return "extension_not_available_lock_kept"
                result = await self.engine.extend_tp(position.ticket, position.identifier, tp, review)
                return "tp_extended_to_120" if result.status in CONFIRMED else "ai_action_failed"
        except LockRegressionError:
            raise
        except (BrokerError, RiskViolation):
            return "ai_action_failed"
        return "mechanical_continue"  # pragma: no cover - unreachable for validated decisions

    def forget(self, position_id: int) -> None:
        self._states.pop(position_id, None)
