"""Alert Center: real-time owner alerts for errors, warnings and critical events.

Levels
------
* ``INFO``     – stored only (Mini App Alerts page / ``/alerts``)
* ``WARNING``  – stored + Telegram
* ``ERROR``    – stored + Telegram on the next 5 s flush
* ``CRITICAL`` – stored + Telegram on the next flush + AUTO-PAUSE of new entries
  (``RuntimeControl.auto_pause("critical_alert")``; resume with the normal gated /resume).
  Never deduplicated, never rate limited.

Sources (no producer can be blocked by the alert path)
------------------------------------------------------
1. ``AlertCenter.emit(...)`` – explicit hooks (scheduler broker failure/recovery, AI circuit, ...).
   Thread-safe, in-memory, O(1), never touches the database and never raises.
2. ``AlertLogHandler`` – WARNING+ records of the bot's own loggers (database write failures, AI
   fallback, provider errors ...). Records marked ``extra={"alerted": True}`` are skipped (already
   emitted explicitly). Raw exception text is never copied; messages are sanitized.
3. ``AlertCenter.scan_audit`` – durable events written by other components or processes
   (kill switch, daily loss/drawdown latch, runtime halts, watchdog restart notices, broker order or
   SL/TP rejections, risk vetoes of AI-approved signals). The cursor starts at the current maximum id,
   so history is never replayed as new alerts.

Delivery (``flush``, scheduler job ``alerts`` every 5 s)
--------------------------------------------------------
* Dedup: the same fingerprint (level + component + digit-free message) within 5 minutes only
  increments ``repeat_count``; the next Telegram message for it says "repeated xN".
* Rate limit: at most 5 Telegram messages per rolling 5 minutes (CRITICAL exempt). Suppressed
  alerts stay stored; the next sent message reports how many were suppressed.
* Persistence failure never stops Telegram delivery (and vice versa). A Telegram failure is recorded
  as ``uncertain`` and NOT retried (at-most-once, like the other owner notifications).

Nothing here places, modifies or closes an order.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Integer, String, func, inspect, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.database import Database
from core.models import AuditLog, UTCDateTime
from core.security import sanitize_text

LOG = logging.getLogger("app.alerts")
LEVELS = ("INFO", "WARNING", "ERROR", "CRITICAL")
TELEGRAM_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})
DEDUP_SECONDS = 300
RATE_LIMIT_COUNT = 5
RATE_LIMIT_SECONDS = 300
MAX_PENDING = 500
EMOJI = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨", "CRITICAL": "🛑"}
OWN_LOGGERS = (
    "app.",
    "ai.",
    "trading.",
    "core.",
    "strategy.",
    "telegram_bot.",
    "miniapp.",
    "news.",
    "reflexbot.",
    "watchdog",
)
STATE_ONLY_VETOES = frozenset(
    {"paused", "kill_switch", "recovery_halt", "daily_loss_latch", "drawdown_latch", "unknown_baseline"}
)


class AlertBase(DeclarativeBase):
    pass


class Alert(AlertBase):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    action: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    telegram_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_required")
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


def ensure_alert_tables(database: Database) -> None:
    """Idempotent and additive (separate metadata; the core schema version is unchanged)."""
    AlertBase.metadata.create_all(database.engine, checkfirst=True)


def alerts_table_present(database: Database) -> bool:
    try:
        return "alerts" in set(inspect(database.engine).get_table_names())
    except Exception:  # pragma: no cover
        return False


def fingerprint(level: str, component: str, message: str) -> str:
    normalized = re.sub(r"\d+(\.\d+)?", "#", message.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return f"{level}|{component.lower()}|{normalized}"[:64]


@dataclass(slots=True)
class PendingAlert:
    level: str
    component: str
    message: str
    details: dict = field(default_factory=dict)
    action: str | None = None
    at: datetime | None = None


@dataclass(slots=True)
class _Window:
    alert_id: int | None
    first_seen: float
    repeats: int = 1
    unsent_repeats: int = 0


def format_telegram(alert: PendingAlert, *, when: datetime, repeats: int = 0, suppressed: int = 0) -> str:
    lines = [
        f"{EMOJI.get(alert.level, '')} {alert.level} ALERT",
        f"Time: {when.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"Level: {alert.level}",
        f"Component: {alert.component}",
        f"Message: {alert.message}",
    ]
    if alert.action:
        lines.append(f"Action: {alert.action}")
    if repeats > 1:
        lines.append(f"Repeated: x{repeats} in the last {DEDUP_SECONDS // 60} min")
    if suppressed:
        lines.append(f"Note: {suppressed} other alert(s) were rate-limited; see /alerts")
    if alert.level == "CRITICAL":
        lines.append("Trading auto-paused for new entries. Review, then /resume when safe.")
    return "\n".join(lines)[:1500]


class AlertCenter:
    def __init__(
        self,
        database: Database,
        settings,
        clock,
        *,
        control=None,
        account_key=None,
        monotonic=time.monotonic,
        secrets=(),
    ):
        self.database, self.settings, self.clock = database, settings, clock
        self.control, self.account_key = control, account_key or (lambda: "unbound")
        self.monotonic = monotonic
        self.secrets = tuple(secrets) or tuple(getattr(database, "secrets", ()))
        self._pending: deque[PendingAlert] = deque(maxlen=MAX_PENDING)
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}
        self._sent: deque[float] = deque()
        self._suppressed = 0
        self._audit_cursor: int | None = None
        self.dropped = 0
        ensure_alert_tables(database)
        self.stats = {
            "emitted": 0,
            "stored": 0,
            "sent": 0,
            "deduplicated": 0,
            "rate_limited": 0,
            "auto_pauses": 0,
        }

    # -- intake (any thread, never raises, never blocks on I/O) ---------------------------------
    def emit(
        self,
        level: str,
        component: str,
        message: str,
        *,
        details: dict | None = None,
        action: str | None = None,
    ) -> None:
        try:
            level = level.upper() if level.upper() in LEVELS else "WARNING"
            alert = PendingAlert(
                level,
                sanitize_text(str(component), self.secrets)[:64] or "bot",
                sanitize_text(str(message), self.secrets)[:500] or "-",
                _bounded_details(details),
                sanitize_text(action, self.secrets)[:200] if action else None,
                self.clock.now(),
            )
            with self._lock:
                if len(self._pending) == self._pending.maxlen:
                    self.dropped += 1
                self._pending.append(alert)
                self.stats["emitted"] += 1
        except Exception:  # pragma: no cover - the alert path must never break a producer
            pass

    def pending(self) -> int:
        return len(self._pending)

    # -- audit watcher ---------------------------------------------------------------------------
    def scan_audit(self, *, limit: int = 200) -> int:
        """Translate NEW durable audit events into alerts. Returns the number emitted."""
        with self.database.session() as session:
            if self._audit_cursor is None:
                self._audit_cursor = int(session.scalar(select(func.max(AuditLog.id))) or 0)
                return 0
            rows = session.scalars(
                select(AuditLog).where(AuditLog.id > self._audit_cursor).order_by(AuditLog.id).limit(limit)
            ).all()
            events = [(row.id, row.action, dict(row.details or {})) for row in rows]
        emitted = 0
        for row_id, action, details in events:
            self._audit_cursor = row_id
            mapped = audit_alert(action, details)
            if mapped is not None:
                self.emit(*mapped[:3], details=mapped[3], action=mapped[4])
                emitted += 1
        return emitted

    # -- delivery --------------------------------------------------------------------------------
    def _rate_allows(self, now: float) -> bool:
        while self._sent and now - self._sent[0] > RATE_LIMIT_SECONDS:
            self._sent.popleft()
        return len(self._sent) < RATE_LIMIT_COUNT

    def _store(self, alert: PendingAlert, key: str, status: str, existing: int | None) -> int | None:
        try:
            with self.database.session() as session:
                if existing is not None:
                    row = session.get(Alert, existing)
                    if row is not None:
                        row.repeat_count += 1
                        row.last_seen = alert.at
                        if status in {"sent", "uncertain"}:
                            row.telegram_status = status
                        return row.id
                row = Alert(
                    timestamp=alert.at,
                    level=alert.level,
                    component=alert.component,
                    message=alert.message,
                    details=alert.details,
                    action=alert.action,
                    fingerprint=key,
                    repeat_count=1,
                    last_seen=alert.at,
                    telegram_status=status,
                )
                session.add(row)
                session.flush()
                self.stats["stored"] += 1
                return row.id
        except Exception:
            # Never log at WARNING+ here (the log handler would loop); delivery continues.
            LOG.info("Alert persistence unavailable; Telegram delivery continues")
            return None

    def _halt(self, alert: PendingAlert) -> None:
        if self.control is None:
            return
        try:
            self.control.auto_pause("critical_alert", account_key=self.account_key() or "unbound")
            self.stats["auto_pauses"] += 1
        except Exception:
            LOG.info("Critical alert auto-pause could not be persisted")

    async def flush(self, bot=None, *, limit: int = 50) -> dict:
        """Persist, deduplicate, rate-limit and deliver pending alerts. Never raises."""
        import asyncio

        try:
            await asyncio.to_thread(self.scan_audit)
        except Exception:
            LOG.info("Audit alert scan unavailable this cycle")
        with self._lock:
            batch = [self._pending.popleft() for _ in range(min(limit, len(self._pending)))]
        result = {"processed": 0, "sent": 0, "deduplicated": 0, "rate_limited": 0, "stored_only": 0}
        owner = getattr(self.settings, "telegram_owner_id", None)
        for alert in batch:
            result["processed"] += 1
            now = self.monotonic()
            key = fingerprint(alert.level, alert.component, alert.message)
            critical = alert.level == "CRITICAL"
            previous = self._windows.get(key)
            if previous is not None and not critical and now - previous.first_seen <= DEDUP_SECONDS:
                previous.repeats += 1
                previous.unsent_repeats += 1
                self.stats["deduplicated"] += 1
                result["deduplicated"] += 1
                await asyncio.to_thread(self._store, alert, key, "deduplicated", previous.alert_id)
                continue
            # First message after a dedup window that swallowed repeats says "repeated xN".
            repeats = previous.repeats + 1 if previous is not None and previous.unsent_repeats else 0
            if critical:
                await asyncio.to_thread(self._halt, alert)
            deliver = alert.level in TELEGRAM_LEVELS and bot is not None and owner is not None
            status = "not_required" if alert.level == "INFO" else "pending"
            if deliver and not critical and not self._rate_allows(now):
                deliver, status = False, "rate_limited"
                self._suppressed += 1
                self.stats["rate_limited"] += 1
                result["rate_limited"] += 1
            if deliver:
                text = format_telegram(alert, when=alert.at, repeats=repeats, suppressed=self._suppressed)
                try:
                    await bot.send_message(chat_id=owner, text=text, parse_mode=None)
                    status = "sent"
                    self._suppressed = 0
                    result["sent"] += 1
                    self.stats["sent"] += 1
                except Exception:
                    status = "uncertain"  # At-most-once: never resent.
                if not critical:
                    self._sent.append(now)
            elif status == "pending":
                status = "no_transport"
                result["stored_only"] += 1
            alert_id = await asyncio.to_thread(self._store, alert, key, status, None)
            if critical:
                self._windows.pop(key, None)
            else:
                self._windows[key] = _Window(alert_id, now)
        # Bounded memory for long uptimes.
        stale = [k for k, w in self._windows.items() if self.monotonic() - w.first_seen > DEDUP_SECONDS * 2]
        for key in stale:
            self._windows.pop(key, None)
        result["pending"] = len(self._pending)
        return result


def _bounded_details(details: dict | None) -> dict:
    if not details:
        return {}
    result = {}
    for key, value in list(details.items())[:12]:
        if isinstance(value, (int, float, bool)) or value is None:
            result[str(key)[:40]] = value
        elif isinstance(value, (list, tuple)):
            result[str(key)[:40]] = [str(v)[:60] for v in value[:10]]
        else:
            result[str(key)[:40]] = str(value)[:200]
    return result


def audit_alert(action: str, details: dict):
    """Map a durable audit event to (level, component, message, details, action) or None."""
    if action == "owner.killed":
        return (
            "CRITICAL",
            "Risk Engine",
            "Kill switch activated",
            {"owner_id": details.get("owner_id")},
            "New entries latched off; open positions keep their protection",
        )
    if action in {"risk.daily_loss_latched", "risk.drawdown_latched"}:
        what = "Daily loss limit" if action == "risk.daily_loss_latched" else "Max drawdown limit"
        return (
            "CRITICAL",
            "Risk Engine",
            f"{what} reached - trading auto-paused",
            {"account": details.get("account")},
            "No new entries; owner review required",
        )
    if action == "runtime.halted":
        reason = str(details.get("reason", "unknown"))
        if reason == "persistence_failure":
            return ("ERROR", "Database", "Database write failure - trading halted", {}, "Check disk/DB")
        if reason == "broker_unstable":
            return None  # The scheduler emits the richer "Connection lost" alert explicitly.
        return (
            "ERROR",
            "Runtime",
            f"Trading halted: {reason}",
            {"reason": reason},
            "Entries halted until recovery review; open positions keep protective management",
        )
    if action in {
        "risk.balance_continuity_halt",
        "reconcile.ledger_halt",
        "reconcile.unsettled_halt",
        "broker.uncertain_halt",
        "broker.contradictory_result_halt",
    }:
        return (
            "ERROR",
            "Execution",
            action.replace("_", " ").replace(".", ": "),
            {},
            "Entries halted; reconcile",
        )
    if action == "runtime.notice_pending":
        kind = details.get("kind")
        if kind == "restart":
            return ("WARNING", "Watchdog", "Watchdog restarted the bot", {}, "Bot restarts PAUSED; /resume")
        if kind == "budget":
            return (
                "CRITICAL",
                "Watchdog",
                "Watchdog restart budget exhausted",
                {},
                "Restarts stopped; inspect errors.log before restarting",
            )
        if kind == "stalled":
            return (
                "ERROR",
                "Watchdog",
                "Runtime stalled; watchdog intervening",
                {},
                "Watchdog restart pending",
            )
        return None
    if action == "broker.acknowledged" and details.get("status") == "rejected":
        if details.get("operation") == "protect":
            return (
                "ERROR",
                "Execution",
                "SL/TP modification rejected by broker",
                {"order": details.get("order")},
                "Mechanical lock retried next cycle; check the position",
            )
        return (
            "ERROR",
            "Execution",
            f"Order rejected by broker ({details.get('operation', 'order')})",
            {"order": details.get("order")},
            "No position opened; next signal re-evaluated",
        )
    if action == "risk.entry_decision" and details.get("approved") is False:
        reasons = sorted(set(details.get("reasons") or ()))
        if not reasons or set(reasons) <= STATE_ONLY_VETOES:
            return None  # Paused/killed/latched state is already known to the owner.
        return (
            "WARNING",
            "Risk Engine",
            "Risk check blocked an approved trade: " + ",".join(reasons)[:200],
            {"reasons": reasons},
            "Trade not sent",
        )
    if action == "intent.authority_veto":
        return ("WARNING", "Risk Engine", "Execution authority vetoed a trade", {}, "Trade not sent")
    if action == "trailing.lock_not_claimed":
        return ("WARNING", "Execution", "Profit-lock SL update not confirmed", {}, "Retried next cycle")
    if action == "owner.ai_fallback_mode_changed":
        return ("INFO", "AI Provider", f"AI fallback mode set to {details.get('to')}", {}, None)
    return None


class AlertLogHandler(logging.Handler):
    """WARNING+ records of the bot's own loggers -> AlertCenter.emit (sanitized, no exceptions)."""

    COMPONENTS = (
        ("reflexbot.mt5", "MT5 Client"),
        ("trading.mt5", "MT5 Client"),
        ("trading.native", "MT5 Client"),
        ("ai.", "AI Provider"),
        ("core.database", "Database"),
        ("trading.execution", "Execution"),
        ("trading.risk", "Risk Engine"),
        ("trading.", "Trading"),
        ("news", "News"),
        ("telegram_bot.", "Telegram"),
        ("reflexbot.scheduler", "Scheduler"),
        ("reflexbot.", "Runtime"),
        ("app.", "Runtime"),
    )

    def __init__(self, center: AlertCenter):
        super().__init__(level=logging.WARNING)
        self.center = center

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name
            if name == LOG.name or getattr(record, "alerted", False) or getattr(record, "no_alert", False):
                return
            if not (name.startswith(OWN_LOGGERS) or name in {"watchdog", "news"}):
                return
            component = next((label for prefix, label in self.COMPONENTS if name.startswith(prefix)), name)
            level = {logging.WARNING: "WARNING", logging.ERROR: "ERROR"}.get(record.levelno, "ERROR")
            if record.levelno >= logging.CRITICAL:
                level = "CRITICAL"
            message = record.getMessage()  # Format args only; never exc_info / stack text.
            if "AI_FALLBACK" in message and "blocking" in message:
                action = "No new entries until the AI answers (/ai_fallback_technical to change)"
            elif "AI_FALLBACK" in message:
                action = "Technical score fallback active"
            else:
                action = None
            self.center.emit(level, component, message, action=action)
        except Exception:  # pragma: no cover
            pass


def attach_log_handler(center: AlertCenter) -> AlertLogHandler:
    handler = AlertLogHandler(center)
    logging.getLogger().addHandler(handler)
    return handler


def detach_log_handler(handler: AlertLogHandler | None) -> None:
    if handler is not None:
        logging.getLogger().removeHandler(handler)


# -- read / owner actions (used by OwnerReadModel / OwnerServices) --------------------------------
def list_alerts(database: Database, *, limit: int = 100, offset: int = 0, level: str | None = None) -> dict:
    if not alerts_table_present(database):
        return {"items": [], "counts": {}, "unacknowledged": 0, "table_present": False}
    limit, offset = max(1, min(int(limit), 100)), max(0, min(int(offset), 10000))
    with database.session() as session:
        query = select(Alert).order_by(Alert.id.desc())
        if level:
            query = query.where(Alert.level == level.upper())
        rows = session.scalars(query.limit(limit).offset(offset)).all()
        counts = dict(
            session.execute(
                select(Alert.level, func.count()).where(Alert.acknowledged.is_(False)).group_by(Alert.level)
            ).all()
        )
        return {
            "items": [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat(),
                    "level": r.level,
                    "component": r.component,
                    "message": r.message,
                    "details": r.details,
                    "action": r.action,
                    "repeat_count": r.repeat_count,
                    "last_seen": r.last_seen.isoformat(),
                    "telegram_status": r.telegram_status,
                    "acknowledged": r.acknowledged,
                    "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
                    "resolved": r.resolved,
                    "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                }
                for r in rows
            ],
            "counts": counts,
            "unacknowledged": sum(counts.values()),
            "table_present": True,
        }


def acknowledge(database: Database, clock, *, owner_id: int, alert_id: int | None = None) -> int:
    ensure_alert_tables(database)
    with database.session() as session:
        statement = update(Alert).where(Alert.acknowledged.is_(False))
        if alert_id is not None:
            statement = statement.where(Alert.id == alert_id)
        result = session.execute(
            statement.values(acknowledged=True, acknowledged_at=clock.now(), acknowledged_by=owner_id)
        )
        count = int(result.rowcount or 0)
        database.add_audit(
            session,
            "owner.alerts_acknowledged",
            "owner",
            {"owner_id": owner_id, "alert_id": alert_id, "count": count},
        )
        return count


def resolve_older_than(database: Database, clock, *, hours: int = 24) -> int:
    """Housekeeping: acknowledged alerts older than ``hours`` are marked resolved."""
    if not alerts_table_present(database):
        return 0
    with database.session() as session:
        result = session.execute(
            update(Alert)
            .where(
                Alert.acknowledged.is_(True),
                Alert.resolved.is_(False),
                Alert.timestamp < clock.now() - timedelta(hours=hours),
            )
            .values(resolved=True, resolved_at=clock.now())
        )
        return int(result.rowcount or 0)
