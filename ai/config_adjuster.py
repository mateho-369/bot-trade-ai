"""AI dynamic configuration: model suggestions stay inside strict, reviewed bounds.

Two layers (see ``trading.ai_controls``):

Layer 1 – AI-adjustable inside fixed module bounds:

* max_daily_trades        6..20       configured default MAX_DAILY_TRADES (12)
* max_open_positions      1..5        configured default MAX_OPEN_POSITIONS (3)
* risk_percent_per_trade  0.1..1.0 %  configured default MAX_RISK_PERCENT_PER_TRADE (0.5)
* target_profit_per_trade $1..$20     configured default TARGET_PROFIT_USD_PER_TRADE (5)
* max_spread_points       10..50      never above the global cap or explicit symbol cap
* skip_trailing_levels    {30,60,90}  skips AI consultation only; mechanical locks still fire
* strategy_weights        major only  kept as a local review proposal
* symbols_to_trade        major only  subset of configured SYMBOLS

Layer 2 – hard caps the AI can NEVER exceed: 25 trades/day, 5 open positions, 1.0% risk;
daily-loss and drawdown latches, news/safety gates, kill, mode, startup pause and broker-write
authority are not adjustable by AI. Increases need a strong trend and non-high news risk.

Changes within 50% may auto-apply when AI_CONFIG_AUTO_APPLY_MINOR=true. Major/non-numeric
changes stay pending until a current local operator uses a reviewed typed transition. Every
proposal, decision and rejection is audited; stale AI availability returns to configured defaults.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select

from ai.ai_first_schemas import STRATEGY_KEYS, ConfigSuggestion
from ai.decision_journal import AIConfigOverlay, ensure_journal_tables
from core.database import Database
from core.models import AISuggestion
from core.security import sha256_json
from core.settings import Settings
from trading.ai_controls import (
    DYNAMIC_BOUNDS,
    DYNAMIC_PARAMETERS,
    HARD_MAX_DAILY_TRADES,
    HARD_MAX_OPEN_POSITIONS,
    HARD_MAX_RISK_PERCENT,
    clear_dynamic,
    effective_limits,
    write_dynamic,
)
from trading.types import Clock, TradingDisabled

LOG = logging.getLogger("ai.config")

HARD_LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    **DYNAMIC_BOUNDS,
    "max_spread_points": (Decimal("10"), Decimal("50")),
}
ABSOLUTE_MAX_RISK_PERCENT = HARD_MAX_RISK_PERCENT
ABSOLUTE_MAX_DAILY_TRADES = HARD_MAX_DAILY_TRADES
ABSOLUTE_MAX_OPEN_POSITIONS = HARD_MAX_OPEN_POSITIONS
INTEGER_PARAMETERS = frozenset({"max_daily_trades", "max_open_positions", "max_spread_points"})
INCREASE_GUARDED = frozenset({"max_daily_trades", "max_open_positions", "risk_percent_per_trade"})
APPROVAL_RELATIVE_CHANGE = Decimal("0.5")  # > 50 % vs the configured default => local operator review.
ADJUSTABLE = frozenset({*HARD_LIMITS, "skip_trailing_levels", "strategy_weights", "symbols_to_trade"})
FORBIDDEN = frozenset(
    {
        "kill_switch",
        "kill_switch_active",
        "live_trading",
        "paper_trading",
        "start_paused",
        "max_daily_loss_percent",
        "max_drawdown_percent",
        "news_required_for_entry",
        "require_stop_loss",
        "native_write_mode",
        "max_risk_percent_per_trade",
    }
)
TRAILING_LEVELS = (30, 60, 90)
OVERLAY_CACHE_SECONDS = 15  # Local operator overlay changes are honored within 15 s.


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    parameter: str
    value: object
    classification: str  # minor | major | forbidden | invalid | unchanged
    status: str  # applied | pending | rejected | unchanged
    reason: str
    overlay_id: int | None = None
    suggestion_id: int | None = None


def _decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("number required") from None
    if not number.is_finite():
        raise ValueError("finite number required")
    return number


def validate_value(settings: Settings, parameter: str, value):
    """Return the canonical bounded value or raise ValueError. Used by the store validator too."""
    if parameter in FORBIDDEN or parameter not in ADJUSTABLE:
        raise ValueError("parameter is not AI-adjustable")
    if parameter in HARD_LIMITS:
        low, high = HARD_LIMITS[parameter]
        number = _decimal(value)
        if parameter in INTEGER_PARAMETERS:
            if number != number.to_integral_value():
                raise ValueError("integer required")
            number = number.to_integral_value()
        if not low <= number <= high:
            raise ValueError("outside hard bounds")
        if parameter in INTEGER_PARAMETERS:
            return int(number)
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize())
    if parameter == "skip_trailing_levels":
        if not isinstance(value, (list, tuple)) or any(type(v) is not int for v in value):
            raise ValueError("integer level list required")
        levels = sorted(set(value))
        if any(level not in TRAILING_LEVELS for level in levels):
            raise ValueError("only 30/60/90 may be skipped")
        return levels
    if parameter == "symbols_to_trade":
        if not isinstance(value, (list, tuple)) or not value or any(type(v) is not str for v in value):
            raise ValueError("non-empty symbol list required")
        symbols = sorted(set(value))
        if not set(symbols).issubset(settings.symbols):
            raise ValueError("AI may only choose among owner-configured symbols")
        return symbols
    if parameter == "strategy_weights":
        if not isinstance(value, dict) or set(value) != set(STRATEGY_KEYS):
            raise ValueError("exact strategy weight keys required")
        weights = {k: _decimal(v) for k, v in value.items()}
        if any(not 0 <= w <= 1 for w in weights.values()):
            raise ValueError("weights must be 0..1")
        total = sum(weights.values())
        if abs(total - 1) > Decimal("0.001"):
            raise ValueError("weights must sum to 1")
        return {k: str(w.quantize(Decimal("0.01"))) for k, w in weights.items()}
    raise ValueError("unsupported parameter")  # pragma: no cover


class AIConfigAdjuster:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        *,
        suggestions=None,
        notifier=None,
    ):
        self.database, self.settings, self.clock = database, settings, clock
        self.suggestions, self.notifier = suggestions, notifier
        ensure_journal_tables(database)
        self._cache: dict | None = None
        self._cached_at = 0.0

    # -- overlay ------------------------------------------------------------------------------
    def _configured_default(self, parameter: str):
        cfg = self.settings
        return {
            "risk_percent_per_trade": str(cfg.effective_risk_percent),
            "target_profit_per_trade": str(cfg.target_profit_usd_per_trade),
            "max_daily_trades": cfg.max_daily_trades,
            "max_open_positions": cfg.max_open_positions,
            "max_spread_points": cfg.max_spread_points,
            "skip_trailing_levels": [],
            "strategy_weights": {k: str(v) for k, v in cfg.strategy_weights.items()},
            "symbols_to_trade": sorted(cfg.symbols),
        }[parameter]

    def overlay(self) -> dict:
        """Latest APPLIED value per parameter (raw, before owner ceilings)."""
        if self._cache is None or time.monotonic() - self._cached_at > OVERLAY_CACHE_SECONDS:
            self._cached_at = time.monotonic()
            with self.database.session() as session:
                rows = session.scalars(
                    select(AIConfigOverlay)
                    .where(AIConfigOverlay.status == "applied")
                    .order_by(AIConfigOverlay.id.asc())
                ).all()
                self._cache = {row.parameter: row.value for row in rows}
        return dict(self._cache)

    def limits(self):
        """Current layer-1 limits (configured defaults while the AI is unavailable)."""
        return effective_limits(self.database, self.settings, self.clock)

    def effective(self) -> dict:
        """Overlay clamped by hard limits AND owner ceilings. This is what gates consume."""
        raw, cfg = self.overlay(), self.settings
        limits = self.limits()
        result = {
            "risk_percent_per_trade": limits.risk_percent,
            "target_profit_per_trade": limits.target_usd,
            "max_daily_trades": limits.max_daily_trades,
            "max_open_positions": limits.max_open_positions,
            "limits_source": limits.source,
        }
        if "max_spread_points" in raw:
            result["max_spread_points"] = min(int(raw["max_spread_points"]), cfg.max_spread_points)
        if "skip_trailing_levels" in raw:
            result["skip_trailing_levels"] = tuple(raw["skip_trailing_levels"])
        if "symbols_to_trade" in raw:
            result["symbols_to_trade"] = tuple(s for s in raw["symbols_to_trade"] if s in cfg.symbols)
        return result

    def spread_limit(self, logical: str | None, native: str | None = None) -> int:
        base = self.settings.spread_limit_points(logical, native)
        explicit = any(k in self.settings.symbol_spread_limits for k in (logical, native) if k)
        overlay = self.effective().get("max_spread_points")
        return base if explicit or overlay is None else min(base, overlay)

    # -- proposals ----------------------------------------------------------------------------
    def _audit(self, action: str, details: dict) -> None:
        self.database.audit(action, "ai", details)

    def _classify(self, parameter: str, value) -> str:
        """minor = within 50 % of the configured default (auto); major = local operator review required."""
        if parameter not in HARD_LIMITS:
            return "major"
        default = Decimal(str(self._configured_default(parameter)))
        if default <= 0:
            return "major"
        change = abs(Decimal(str(value)) - default) / default
        return "minor" if change <= APPROVAL_RELATIVE_CHANGE else "major"

    @staticmethod
    def _strong_trend(context: dict | None) -> bool:
        context = context or {}
        return context.get("regime") == "trending" and context.get("news_risk") in {"low", "medium"}

    def propose(
        self,
        parameter: str,
        value,
        *,
        reason: str,
        source: str = "ai",
        context: dict | None = None,
    ) -> AdjustmentResult:
        result = self._propose(parameter, value, reason=reason, source=source, context=context)
        LOG.info(  # ai_decisions.log: every adjustment attempt with its reason and outcome.
            "AI config %s %s -> %s: %s (%s) reason=%s",
            source,
            str(parameter)[:64],
            str(value)[:32],
            result.status,
            result.classification,
            (reason or "-")[:200],
        )
        return result

    def _propose(
        self,
        parameter: str,
        value,
        *,
        reason: str,
        source: str = "ai",
        context: dict | None = None,
    ) -> AdjustmentResult:
        reason = (reason or "-")[:600]
        if parameter in FORBIDDEN or parameter not in ADJUSTABLE:
            self._audit(
                "ai.config_forbidden", {"parameter": str(parameter)[:64], "attempted": True, "applied": False}
            )
            return AdjustmentResult(parameter, value, "forbidden", "rejected", "hard_limit_parameter")
        try:
            canonical = validate_value(self.settings, parameter, value)
        except ValueError:
            self._audit(
                "ai.config_out_of_bounds",
                {"parameter": parameter, "reason": "out_of_bounds", "applied": False},
            )
            return AdjustmentResult(parameter, None, "invalid", "rejected", "out_of_bounds")
        current = self.overlay().get(parameter, self._configured_default(parameter))
        if canonical == current:
            return AdjustmentResult(parameter, canonical, "unchanged", "unchanged", "no_change")
        if (
            parameter in INCREASE_GUARDED
            and Decimal(str(canonical)) > Decimal(str(current))
            and not self._strong_trend(context)
        ):
            self._audit(
                "ai.config_increase_rejected",
                {
                    "parameter": parameter,
                    "value": canonical,
                    "previous": current,
                    "reason": "increase_requires_strong_trend",
                    "regime": (context or {}).get("regime"),
                    "news_risk": (context or {}).get("news_risk"),
                },
            )
            return AdjustmentResult(
                parameter, canonical, "invalid", "rejected", "increase_requires_strong_trend"
            )
        classification = self._classify(parameter, canonical)
        auto = classification == "minor" and self.settings.ai_config_auto_apply_minor
        suggestion_id = None
        if not auto and parameter == "strategy_weights" and self.suggestions is not None:
            suggestion_id = self._weights_proposal(canonical, reason)
        elif not auto and self.suggestions is not None:
            suggestion_id = self._store_proposal(parameter, canonical, reason)
        with self.database.session() as session:
            row = AIConfigOverlay(
                time=self.clock.now(),
                parameter=parameter,
                value=canonical,
                previous=current,
                classification=classification,
                status="applied" if auto else "pending",
                suggestion_id=suggestion_id,
                source=source,
                reason=reason,
                decided_at=self.clock.now() if auto else None,
            )
            session.add(row)
            session.flush()
            overlay_id = row.id
            if auto:
                self._materialize(session)
            self.database.add_audit(
                session,
                "ai.config_auto_applied" if auto else "ai.config_pending_owner",
                "ai",
                {
                    "overlay_id": overlay_id,
                    "parameter": parameter,
                    "value": canonical,
                    "previous": current,
                    "classification": classification,
                    "suggestion_id": suggestion_id,
                    "reason": reason[:200],
                },
            )
        self._cache = None
        result = AdjustmentResult(
            parameter,
            canonical,
            classification,
            "applied" if auto else "pending",
            reason,
            overlay_id,
            suggestion_id,
        )
        if self.notifier is not None:
            self.notifier.config_adjustment(result)
        return result

    def _store_proposal(self, parameter, canonical, reason) -> int | None:
        try:
            stored = self.suggestions.create(
                "ai_config_adjustment",
                {"parameter": parameter, "value": canonical},
                reason=reason,
                request_hash=sha256_json({"parameter": parameter, "value": canonical, "t": self.clock.now()}),
            )
            return stored.suggestion_id
        except TradingDisabled:
            return None

    def _weights_proposal(self, weights, reason) -> int | None:
        try:
            stored = self.suggestions.create(
                "rebalance_weights",
                {"weights": weights},
                reason=reason,
                request_hash=sha256_json({"weights": weights, "t": self.clock.now()}),
            )
            return stored.suggestion_id
        except TradingDisabled:
            return None  # Outside the locally reviewed step policy: stays a pending journal record only.

    def _materialize(self, session) -> None:
        """Rebuild ``dynamic_config`` from the latest APPLIED value of each layer-1 parameter."""
        rows = session.scalars(
            select(AIConfigOverlay)
            .where(AIConfigOverlay.status == "applied", AIConfigOverlay.parameter.in_(DYNAMIC_PARAMETERS))
            .order_by(AIConfigOverlay.id.asc())
        ).all()
        latest = {row.parameter: row for row in rows}
        clear_dynamic(session)
        session.flush()
        for parameter, row in latest.items():
            write_dynamic(
                session, self.settings, self.clock, parameter, row.value, history_id=row.id, reason=row.reason
            )

    def apply_suggestion(
        self, config: ConfigSuggestion, *, source: str = "ai", context: dict | None = None
    ) -> list[AdjustmentResult]:
        """Fan out one validated AI config review into individual bounded proposals."""
        results = []
        for parameter in (
            "risk_percent_per_trade",
            "target_profit_per_trade",
            "max_daily_trades",
            "max_open_positions",
            "max_spread_points",
        ):
            value = getattr(config, parameter, None)
            if value is not None:
                results.append(
                    self.propose(parameter, value, reason=config.reason, source=source, context=context)
                )
        if config.skip_trailing_levels:
            results.append(
                self.propose("skip_trailing_levels", list(config.skip_trailing_levels), reason=config.reason)
            )
        if config.strategy_weights is not None:
            results.append(
                self.propose("strategy_weights", config.strategy_weights.model_dump(), reason=config.reason)
            )
        if config.symbols_to_trade is not None:
            results.append(
                self.propose("symbols_to_trade", list(config.symbols_to_trade), reason=config.reason)
            )
        return results

    # -- local operator decisions ---------------------------------------------------------------
    def decide(self, overlay_id: int, *, operator, approve: bool) -> AdjustmentResult:
        from ai.local_operator_guard import require_local_operator

        operator_id = require_local_operator(operator)
        with self.database.session() as session:
            row = session.get(AIConfigOverlay, overlay_id)
            if row is None or row.status != "pending":
                raise TradingDisabled("pending AI config adjustment required")
            validate_value(self.settings, row.parameter, row.value)  # Re-check bounds at decision time.
            if row.parameter == "strategy_weights" and approve:
                raise TradingDisabled("strategy weights apply only through the stopped owner projection")
            row.status, row.decided_at = ("applied" if approve else "rejected"), self.clock.now()
            session.flush()
            self._materialize(session)
            self.database.add_audit(
                session,
                "local_operator.ai_config_decided",
                "local_operator",
                {
                    "overlay_id": row.id,
                    "parameter": row.parameter,
                    "decision": row.status,
                    "operator_id": operator_id,
                },
            )
            result = AdjustmentResult(
                row.parameter, row.value, row.classification, row.status, row.reason, row.id
            )
        self._cache = None
        return result

    def sync_operator_decisions(self) -> int:
        """Mirror local operator decisions on linked proposals into the overlay."""
        changed = 0
        with self.database.session() as session:
            rows = session.scalars(
                select(AIConfigOverlay).where(
                    AIConfigOverlay.status == "pending", AIConfigOverlay.suggestion_id.is_not(None)
                )
            ).all()
            for row in rows:
                proposal = session.get(AISuggestion, row.suggestion_id)
                if proposal is None:
                    continue
                if proposal.status == "approved" and row.parameter != "strategy_weights":
                    try:
                        validate_value(self.settings, row.parameter, row.value)
                    except ValueError:
                        row.status = "rejected"
                    else:
                        row.status = "applied"
                elif proposal.status in {"rejected", "expired"}:
                    row.status = "rejected"
                elif proposal.status == "applied" and row.parameter == "strategy_weights":
                    row.status = "applied"
                else:
                    continue
                row.decided_at = self.clock.now()
                changed += 1
                self.database.add_audit(
                    session,
                    "ai.config_local_decision_synced",
                    "ai",
                    {"overlay_id": row.id, "suggestion_id": row.suggestion_id, "status": row.status},
                )
            if changed:
                session.flush()
                self._materialize(session)
        if changed:
            self._cache = None
        return changed

    def history(self, *, limit: int = 50) -> list[dict]:
        with self.database.session() as session:
            rows = session.scalars(
                select(AIConfigOverlay).order_by(AIConfigOverlay.id.desc()).limit(max(1, min(limit, 200)))
            ).all()
            return [
                {
                    "id": r.id,
                    "time": r.time.isoformat(),
                    "parameter": r.parameter,
                    "value": r.value,
                    "previous": r.previous,
                    "classification": r.classification,
                    "status": r.status,
                    "suggestion_id": r.suggestion_id,
                    "reason": r.reason[:200],
                }
                for r in rows
            ]

    def daily_summary(self, *, hours: int = 24) -> dict:
        """Counts + latest values of the last ``hours`` of AI adjustments (nightly owner digest)."""
        since = self.clock.now() - timedelta(hours=hours)
        with self.database.session() as session:
            rows = session.scalars(
                select(AIConfigOverlay)
                .where(AIConfigOverlay.time >= since)
                .order_by(AIConfigOverlay.id.asc())
            ).all()
            counts: dict[str, int] = {}
            changes = []
            for row in rows:
                counts[row.status] = counts.get(row.status, 0) + 1
                changes.append(f"{row.parameter} {row.previous}->{row.value} ({row.status})")
        limits = self.limits()
        return {
            "hours": hours,
            "total": len(rows),
            "counts": counts,
            "changes": changes[-10:],
            "current_limits": limits.as_dict(),
        }

    def daily_summary_text(self, *, hours: int = 24) -> str:
        data = self.daily_summary(hours=hours)
        limits = data["current_limits"]
        lines = [
            f"Daily AI adjustments ({data['hours']}h): {data['total']} "
            + (", ".join(f"{k} {v}" for k, v in sorted(data["counts"].items())) or "none"),
            f"Limits now ({limits['source']}): trades/day {limits['max_daily_trades']}, "
            f"positions {limits['max_open_positions']}, risk {limits['risk_percent']}%, "
            f"target ${limits['target_usd']}",
            *data["changes"],
            "Reset AI overlays only with the explicit reviewed local operator command.",
        ]
        return "\n".join(lines)


def revert_all(database: Database, clock: Clock, *, operator) -> int:
    """Local operator reset: revert pending/applied overlays without clearing risk latches."""
    from ai.local_operator_guard import require_local_operator

    operator_id = require_local_operator(operator)
    ensure_journal_tables(database)
    with database.session() as session:
        rows = session.scalars(
            select(AIConfigOverlay).where(AIConfigOverlay.status.in_(("applied", "pending")))
        ).all()
        for row in rows:
            row.status, row.decided_at = "reverted", clock.now()
        cleared = clear_dynamic(session)  # Reviewed configuration defaults apply immediately.
        database.add_audit(
            session,
            "local_operator.ai_config_reset",
            "local_operator",
            {
                "operator_id": operator_id,
                "reverted": len(rows),
                "dynamic_cleared": cleared,
                "ids": [r.id for r in rows][:100],
            },
        )
        return len(rows)
