"""AI dynamic config adjustment: AI SUGGESTS, bounds decide, owner APPROVES majors.

Hard limits are module constants (not settings), so neither the AI nor an .env edit can loosen
them here. The overlay can only act INSIDE the owner's configured ceilings:

* risk_percent_per_trade  0.1..1.0 %  effective = min(overlay, owner EFFECTIVE risk ceiling)
* target_profit_per_trade $1..$20     advisory target fed to the AI/trailing context
* max_daily_trades        3..15       effective = min(overlay, owner MAX_DAILY_TRADES)
* max_spread_points       10..50      effective = min(overlay, global cap); never applied to a
                                        symbol with an explicit SYMBOL_SPREAD_LIMITS_JSON entry
* skip_trailing_levels    {30,60,90}  skips the AI CONSULTATION only; mechanical locks still fire
* strategy_weights        major only  routed to the existing owner proposal (rebalance_weights)
* symbols_to_trade        major only  subset of the owner-configured SYMBOLS (remove/re-add)

FORBIDDEN (always rejected + audited): max_open_positions (fixed, <=3), kill switch, live/paper
mode, start paused, daily-loss/drawdown caps, news/safety gates, broker write authority.

Minor = risk change <= 0.1 percentage points or target change <= $1 (auto-applies when
AI_CONFIG_AUTO_APPLY_MINOR=true). Everything else is MAJOR: stored pending and surfaced to the
owner through the existing Telegram /approve /reject and Mini App proposal flow. Every proposal,
application, rejection and forbidden attempt is written to audit_logs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select

from ai.ai_first_schemas import STRATEGY_KEYS, ConfigSuggestion
from ai.decision_journal import AIConfigOverlay, ensure_journal_tables
from core.database import Database
from core.models import AISuggestion
from core.security import sha256_json
from core.settings import Settings
from trading.types import Clock, TradingDisabled

HARD_LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    "risk_percent_per_trade": (Decimal("0.1"), Decimal("1.0")),
    "target_profit_per_trade": (Decimal("1"), Decimal("20")),
    "max_daily_trades": (Decimal("3"), Decimal("15")),
    "max_spread_points": (Decimal("10"), Decimal("50")),
}
ABSOLUTE_MAX_RISK_PERCENT = Decimal("1.0")
ABSOLUTE_MAX_DAILY_TRADES = 15
ABSOLUTE_MAX_OPEN_POSITIONS = 3  # Never changeable by AI.
MINOR_STEPS = {"risk_percent_per_trade": Decimal("0.1"), "target_profit_per_trade": Decimal("1")}
ADJUSTABLE = frozenset({*HARD_LIMITS, "skip_trailing_levels", "strategy_weights", "symbols_to_trade"})
FORBIDDEN = frozenset(
    {
        "max_open_positions",
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
OVERLAY_CACHE_SECONDS = 15  # Owner /ai_reset from the Telegram process is honored within 15 s.


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
        if parameter in {"max_daily_trades", "max_spread_points"}:
            if number != number.to_integral_value():
                raise ValueError("integer required")
            number = number.to_integral_value()
        if not low <= number <= high:
            raise ValueError("outside hard bounds")
        if parameter in {"max_daily_trades", "max_spread_points"}:
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
    def _owner_value(self, parameter: str):
        cfg = self.settings
        return {
            "risk_percent_per_trade": str(cfg.effective_risk_percent),
            "target_profit_per_trade": str(cfg.target_profit_usd_per_trade),
            "max_daily_trades": cfg.max_daily_trades,
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

    def effective(self) -> dict:
        """Overlay clamped by hard limits AND owner ceilings. This is what gates consume."""
        raw, cfg, result = self.overlay(), self.settings, {}
        if "risk_percent_per_trade" in raw:
            result["risk_percent_per_trade"] = min(
                Decimal(raw["risk_percent_per_trade"]), cfg.effective_risk_percent, ABSOLUTE_MAX_RISK_PERCENT
            )
        if "target_profit_per_trade" in raw:
            result["target_profit_per_trade"] = Decimal(raw["target_profit_per_trade"])
        if "max_daily_trades" in raw:
            result["max_daily_trades"] = min(
                int(raw["max_daily_trades"]), cfg.max_daily_trades, ABSOLUTE_MAX_DAILY_TRADES
            )
        if "max_spread_points" in raw:
            result["max_spread_points"] = min(int(raw["max_spread_points"]), cfg.max_spread_points)
        if "skip_trailing_levels" in raw:
            result["skip_trailing_levels"] = tuple(raw["skip_trailing_levels"])
        if "symbols_to_trade" in raw:
            result["symbols_to_trade"] = tuple(s for s in raw["symbols_to_trade"] if s in cfg.symbols)
        result["max_open_positions"] = min(cfg.max_open_positions, ABSOLUTE_MAX_OPEN_POSITIONS)
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
        if parameter not in MINOR_STEPS:
            return "major"
        current = self.overlay().get(parameter, self._owner_value(parameter))
        return (
            "minor" if abs(Decimal(str(value)) - Decimal(str(current))) <= MINOR_STEPS[parameter] else "major"
        )

    def propose(self, parameter: str, value, *, reason: str, source: str = "ai") -> AdjustmentResult:
        reason = (reason or "-")[:600]
        if parameter in FORBIDDEN or parameter not in ADJUSTABLE:
            self._audit(
                "ai.config_forbidden", {"parameter": str(parameter)[:64], "attempted": True, "applied": False}
            )
            return AdjustmentResult(parameter, value, "forbidden", "rejected", "hard_limit_parameter")
        try:
            canonical = validate_value(self.settings, parameter, value)
        except ValueError as exc:
            self._audit(
                "ai.config_out_of_bounds",
                {"parameter": parameter, "value": str(value)[:64], "reason": str(exc)[:120]},
            )
            return AdjustmentResult(parameter, value, "invalid", "rejected", str(exc))
        current = self.overlay().get(parameter, self._owner_value(parameter))
        if canonical == current:
            return AdjustmentResult(parameter, canonical, "unchanged", "unchanged", "no_change")
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
            return None  # Outside the owner-safe step policy: stays a pending journal record only.

    def apply_suggestion(self, config: ConfigSuggestion, *, source: str = "ai") -> list[AdjustmentResult]:
        """Fan out one validated AI config review into individual bounded proposals."""
        results = []
        for parameter in (
            "risk_percent_per_trade",
            "target_profit_per_trade",
            "max_daily_trades",
            "max_spread_points",
        ):
            value = getattr(config, parameter)
            if value is not None:
                results.append(self.propose(parameter, value, reason=config.reason, source=source))
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

    # -- owner decisions ----------------------------------------------------------------------
    def decide(self, overlay_id: int, *, owner_id: int, approve: bool) -> AdjustmentResult:
        if self.settings.telegram_owner_id is None or owner_id != self.settings.telegram_owner_id:
            raise TradingDisabled("only the configured owner may decide AI config adjustments")
        with self.database.session() as session:
            row = session.get(AIConfigOverlay, overlay_id)
            if row is None or row.status != "pending":
                raise TradingDisabled("pending AI config adjustment required")
            validate_value(self.settings, row.parameter, row.value)  # Re-check bounds at decision time.
            if row.parameter == "strategy_weights" and approve:
                raise TradingDisabled("strategy weights apply only through the stopped owner projection")
            row.status, row.decided_at = ("applied" if approve else "rejected"), self.clock.now()
            self.database.add_audit(
                session,
                "owner.ai_config_decided",
                "owner",
                {
                    "overlay_id": row.id,
                    "parameter": row.parameter,
                    "decision": row.status,
                    "owner_id": owner_id,
                },
            )
            result = AdjustmentResult(
                row.parameter, row.value, row.classification, row.status, row.reason, row.id
            )
        self._cache = None
        return result

    def sync_owner_decisions(self) -> int:
        """Mirror Telegram/Mini App decisions on linked proposals into the overlay."""
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
                    "ai.config_owner_decision_synced",
                    "ai",
                    {"overlay_id": row.id, "suggestion_id": row.suggestion_id, "status": row.status},
                )
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


def revert_all(database: Database, clock: Clock, *, owner_id: int) -> int:
    """Owner override (/ai_reset): every applied or pending AI overlay change is reverted."""
    ensure_journal_tables(database)
    with database.session() as session:
        rows = session.scalars(
            select(AIConfigOverlay).where(AIConfigOverlay.status.in_(("applied", "pending")))
        ).all()
        for row in rows:
            row.status, row.decided_at = "reverted", clock.now()
        database.add_audit(
            session,
            "owner.ai_config_reset",
            "owner",
            {"owner_id": owner_id, "reverted": len(rows), "ids": [r.id for r in rows][:100]},
        )
        return len(rows)
