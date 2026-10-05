"""Owner AI controls + AI-dynamic trade limits (layer 1) inside fixed hard caps (layer 2).

Layer 1 (AI-adjustable, written by ``ai.config_adjuster`` into ``dynamic_config``)::

    max_daily_trades        6..20   (owner default MAX_DAILY_TRADES, 12)
    max_open_positions      1..5    (owner default MAX_OPEN_POSITIONS, 3)
    risk_percent_per_trade  0.1..1.0 % (owner default MAX_RISK_PERCENT_PER_TRADE, 0.5)
    target_profit_per_trade $1..$20 (owner default TARGET_PROFIT_USD_PER_TRADE, 5)

Layer 2 (module constants, never AI- or .env-adjustable here)::

    HARD_MAX_DAILY_TRADES 25, HARD_MAX_OPEN_POSITIONS 5, HARD_MAX_RISK_PERCENT 1.0 %
    daily loss 3 % / drawdown 10 % latches auto-pause (trading.risk_engine, unchanged)

``effective_limits`` returns the owner defaults unless ALL hold: AI_DYNAMIC_LIMITS_ENABLED, mode is
PAPER/DEMO/LIVE (never BACKTEST), and the runtime published a FRESH "AI available" heartbeat. So an
AI outage reverts to the safe defaults automatically. In LIVE a dynamic value can only be LOWER
than the owner setting (never an escalation of real-money exposure).

``ai_owner_settings`` stores owner runtime toggles (currently AI_FALLBACK_MODE). Runtime toggles live
in the database, not in Settings, so the watchdog config hash and stage evidence stay stable.

No function here places, modifies or closes an order.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.database import Database
from core.models import UTCDateTime
from core.settings import OperatingMode, Settings

LOG = logging.getLogger("trading.ai_controls")

HARD_MAX_DAILY_TRADES = 25
HARD_MAX_OPEN_POSITIONS = 5
HARD_MAX_RISK_PERCENT = Decimal("1.0")
DYNAMIC_BOUNDS: dict[str, tuple[Decimal, Decimal]] = {
    "max_daily_trades": (Decimal("6"), Decimal("20")),
    "max_open_positions": (Decimal("1"), Decimal("5")),
    "risk_percent_per_trade": (Decimal("0.1"), Decimal("1.0")),
    "target_profit_per_trade": (Decimal("1"), Decimal("20")),
}
DYNAMIC_PARAMETERS = tuple(DYNAMIC_BOUNDS)
FALLBACK_MODES = ("BLOCK_ON_AI_FAILURE", "TECHNICAL_ONLY")
STATUS_FRESH_SECONDS = 180
CACHE_SECONDS = 5.0
MISSING_TABLE_RECHECK_SECONDS = 30.0


class ControlBase(DeclarativeBase):
    pass


class AIOwnerSetting(ControlBase):
    __tablename__ = "ai_owner_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class DynamicConfig(ControlBase):
    """CURRENT AI-dynamic value per parameter. History lives in ``config_history``."""

    __tablename__ = "dynamic_config"
    parameter: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    default_value: Mapped[Any] = mapped_column(JSON, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    history_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(600), nullable=False, default="")


def ensure_control_tables(database: Database) -> None:
    """Idempotent and additive; never alters the core schema."""
    ControlBase.metadata.create_all(database.engine, checkfirst=True)
    with _LOCK:
        _TABLES.pop(database.engine, None)


# -- table presence (read paths never create tables) ----------------------------------------------
_TABLES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


def _tables_present(database: Database) -> bool:
    key, now = database.engine, time.monotonic()
    with _LOCK:
        cached = _TABLES.get(key)
        if cached is not None and (cached[0] or now - cached[1] < MISSING_TABLE_RECHECK_SECONDS):
            return cached[0]
    try:
        names = set(inspect(database.engine).get_table_names())
    except Exception:  # pragma: no cover - engine unavailable => defaults (fail safe)
        names = set()
    present = {"ai_owner_settings", "dynamic_config"} <= names
    with _LOCK:
        _TABLES[key] = (present, now)
    return present


# -- owner fallback mode --------------------------------------------------------------------------
_MODE_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def fallback_mode(database: Database | None, settings: Settings) -> str:
    """Effective AI_FALLBACK_MODE: owner DB override (Telegram/Mini App) else the setting."""
    default = settings.ai_fallback_mode
    if database is None:
        return default
    key, now = database.engine, time.monotonic()
    cached = _MODE_CACHE.get(key)
    if cached is not None and now - cached[1] < CACHE_SECONDS:
        return cached[0]
    mode = default
    if _tables_present(database):
        try:
            with database.session() as session:
                row = session.get(AIOwnerSetting, "ai_fallback_mode")
                if row is not None and row.value in FALLBACK_MODES:
                    mode = row.value
        except Exception:
            LOG.warning("AI fallback mode unreadable; using configured default")
    _MODE_CACHE[key] = (mode, now)
    return mode


def fallback_status(database: Database | None, settings: Settings) -> dict:
    mode = fallback_mode(database, settings)
    source, updated_at, updated_by = "settings", None, None
    if database is not None and _tables_present(database):
        with database.session() as session:
            row = session.get(AIOwnerSetting, "ai_fallback_mode")
            if row is not None and row.value in FALLBACK_MODES:
                source, updated_at, updated_by = "owner", row.updated_at.isoformat(), row.updated_by
    return {
        "mode": mode,
        "source": source,
        "configured_default": settings.ai_fallback_mode,
        "updated_at": updated_at,
        "updated_by": updated_by,
        "technical_min_score": settings.ai_rule_fallback_min_score,
        "technical_fallback_enabled": settings.ai_rule_fallback_enabled,
        "description": (
            "AI outage blocks NEW entries; trailing/protection continue"
            if mode == "BLOCK_ON_AI_FAILURE"
            else f"AI outage: technical score >= {settings.ai_rule_fallback_min_score:g} may trade"
        ),
    }


def set_fallback_mode(database: Database, clock, mode: str, *, owner_id: int) -> dict:
    """Owner-only (enforced by the owner service). Audited. Never touches kill/pause/limits."""
    if mode not in FALLBACK_MODES:
        raise ValueError("unknown AI fallback mode")
    ensure_control_tables(database)
    with database.session() as session:
        row = session.get(AIOwnerSetting, "ai_fallback_mode")
        previous = row.value if row is not None else None
        if row is None:
            row = AIOwnerSetting(key="ai_fallback_mode", value=mode, updated_at=clock.now())
            session.add(row)
        row.value, row.updated_at, row.updated_by = mode, clock.now(), owner_id
        database.add_audit(
            session,
            "owner.ai_fallback_mode_changed",
            "owner",
            {"owner_id": owner_id, "from": previous, "to": mode},
        )
    _MODE_CACHE.pop(database.engine, None)
    LOG.info("AI_FALLBACK: owner set mode %s", mode)
    return {"mode": mode, "previous": previous}


# -- AI availability heartbeat --------------------------------------------------------------------
def publish_ai_status(database: Database, clock, *, mode: str, detail: str = "") -> None:
    """Runtime heartbeat: 'ai' when the provider answers (circuit closed), else 'rule'."""
    if not _tables_present(database):
        ensure_control_tables(database)
    with database.session() as session:
        row = session.get(AIOwnerSetting, "ai_status")
        value = {"mode": mode, "at": clock.now().isoformat(), "detail": detail[:120]}
        if row is None:
            session.add(AIOwnerSetting(key="ai_status", value=value, updated_at=clock.now()))
        else:
            row.value, row.updated_at = value, clock.now()


def ai_status(database: Database, clock, *, session=None) -> dict:
    if not _tables_present(database):
        return {"available": False, "mode": "unknown", "at": None}

    def read(s):
        row = s.get(AIOwnerSetting, "ai_status")
        return None if row is None else (dict(row.value), row.updated_at)

    if session is not None:
        found = read(session)
    else:
        with database.session() as fresh:
            found = read(fresh)
    if found is None:
        return {"available": False, "mode": "unknown", "at": None}
    value, updated = found
    fresh_enough = clock.now() - updated <= timedelta(seconds=STATUS_FRESH_SECONDS)
    return {
        "available": value.get("mode") == "ai" and fresh_enough,
        "mode": value.get("mode", "unknown"),
        "at": updated.isoformat(),
        "stale": not fresh_enough,
    }


# -- effective limits -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EffectiveLimits:
    max_daily_trades: int
    max_open_positions: int
    risk_percent: Decimal
    target_usd: Decimal
    source: str  # defaults | ai | static
    reason: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["risk_percent"], data["target_usd"] = str(self.risk_percent), str(self.target_usd)
        return data


def defaults(settings: Settings, *, source: str = "defaults", reason: str = "") -> EffectiveLimits:
    return EffectiveLimits(
        settings.max_daily_trades,
        settings.max_open_positions,
        settings.effective_risk_percent,
        settings.target_profit_usd_per_trade,
        source,
        reason,
    )


def dynamic_enabled(settings: Settings) -> bool:
    return bool(settings.ai_dynamic_limits_enabled and settings.mode != OperatingMode.BACKTEST)


def owner_default(settings: Settings, parameter: str):
    return {
        "max_daily_trades": settings.max_daily_trades,
        "max_open_positions": settings.max_open_positions,
        "risk_percent_per_trade": str(settings.effective_risk_percent),
        "target_profit_per_trade": str(settings.target_profit_usd_per_trade),
    }[parameter]


def _bounded(parameter: str, raw) -> Decimal | None:
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    low, high = DYNAMIC_BOUNDS[parameter]
    if not number.is_finite() or not low <= number <= high:
        return None
    return number


def read_dynamic(database: Database, *, session=None) -> dict:
    if not _tables_present(database):
        return {}

    def read(s):
        return {row.parameter: row.value for row in s.scalars(select(DynamicConfig)).all()}

    if session is not None:
        return read(session)
    with database.session() as fresh:
        return read(fresh)


def effective_limits(
    database: Database | None, settings: Settings, clock, *, session=None
) -> EffectiveLimits:
    """What the risk engine, brokers' guards and AI gate enforce RIGHT NOW. Never raises."""
    if not dynamic_enabled(settings):
        return defaults(settings, source="static", reason="dynamic_limits_disabled")
    if database is None:
        return defaults(settings, reason="no_database")
    try:
        status = ai_status(database, clock, session=session)
        if not status["available"]:
            return defaults(settings, reason="ai_unavailable_using_defaults")
        values = read_dynamic(database, session=session)
    except Exception:
        LOG.warning("Dynamic limits unreadable; owner defaults apply")
        return defaults(settings, reason="dynamic_limits_unreadable")
    if not values:
        return defaults(settings, reason="no_ai_adjustments")
    live = settings.mode == OperatingMode.LIVE
    trades, positions = settings.max_daily_trades, settings.max_open_positions
    risk, target = settings.effective_risk_percent, settings.target_profit_usd_per_trade
    if (n := _bounded("max_daily_trades", values.get("max_daily_trades"))) is not None:
        trades = min(
            int(n), HARD_MAX_DAILY_TRADES, settings.max_daily_trades if live else HARD_MAX_DAILY_TRADES
        )
    if (n := _bounded("max_open_positions", values.get("max_open_positions"))) is not None:
        positions = min(
            int(n), HARD_MAX_OPEN_POSITIONS, settings.max_open_positions if live else HARD_MAX_OPEN_POSITIONS
        )
    if (n := _bounded("risk_percent_per_trade", values.get("risk_percent_per_trade"))) is not None:
        ceiling = min(HARD_MAX_RISK_PERCENT, settings.max_daily_loss_percent)
        risk = min(n, ceiling, settings.effective_risk_percent if live else ceiling)
    if (n := _bounded("target_profit_per_trade", values.get("target_profit_per_trade"))) is not None:
        target = n
    return EffectiveLimits(trades, positions, risk, target, "ai", "ai_dynamic_within_hard_caps")


@dataclass(frozen=True, slots=True)
class BrokerCeilings:
    """Defense-in-depth broker guards: the most any layer-1 value may reach (never above layer 2)."""

    max_daily_trades: int
    max_open_positions: int
    risk_percent: Decimal


def broker_ceilings(settings: Settings) -> BrokerCeilings:
    if not dynamic_enabled(settings) or settings.mode == OperatingMode.LIVE:
        return BrokerCeilings(
            settings.max_daily_trades, settings.max_open_positions, settings.effective_risk_percent
        )
    return BrokerCeilings(
        max(settings.max_daily_trades, int(DYNAMIC_BOUNDS["max_daily_trades"][1])),
        max(settings.max_open_positions, int(DYNAMIC_BOUNDS["max_open_positions"][1])),
        max(settings.effective_risk_percent, min(HARD_MAX_RISK_PERCENT, settings.max_daily_loss_percent)),
    )


def write_dynamic(session, settings: Settings, clock, parameter: str, value, *, history_id, reason) -> None:
    """Called by the adjuster inside its own transaction (value already bound-checked)."""
    row = session.get(DynamicConfig, parameter)
    if row is None:
        row = DynamicConfig(
            parameter=parameter, value=value, default_value=owner_default(settings, parameter)
        )
        session.add(row)
    row.value, row.default_value = value, owner_default(settings, parameter)
    row.applied_at, row.history_id, row.reason = clock.now(), history_id, (reason or "")[:600]


def clear_dynamic(session) -> int:
    rows = session.scalars(select(DynamicConfig)).all()
    for row in rows:
        session.delete(row)
    return len(rows)
