"""AI Decision Journal: every AI (or fallback) decision, its inputs, execution and outcome.

Additive tables in a SEPARATE metadata: ``Database.verify_schema`` only requires the core schema,
so creating these tables never changes SCHEMA_VERSION, never needs a migration and never alters an
existing table. Rows are bounded and sanitized (no secrets, no raw provider bodies).

Tables
------
``ai_decision_journal``  one row per consultation (entry, position, trailing, config, lesson)
``config_history``       every bounded AI config adjustment + owner decision (ai.config_adjuster)

The CURRENT AI-dynamic limits live in ``dynamic_config`` and owner AI toggles in
``ai_owner_settings`` (both ``trading.ai_controls``; created here too).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    case,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.database import Database
from core.models import UTCDateTime
from core.security import canonical_json, sanitize_data, sanitize_text, sha256_json
from trading.types import Clock

KINDS = frozenset({"entry", "position", "trailing", "config", "lesson", "deep_review"})
SOURCES = frozenset({"ai", "cache", "rule_fallback", "ai_blocked", "mechanical", "deterministic", "owner"})
MAX_SUMMARY_BYTES = 8192


class JournalBase(DeclarativeBase):
    pass


class AIDecisionJournal(JournalBase):
    __tablename__ = "ai_decision_journal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    position_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer)
    threshold_reached: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    # Registry label of the AI that answered (groq, groq2, a+b), RULE_FALLBACK, MECHANICAL or none.
    provider_label: Mapped[str | None] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    adjustments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    final_action: Mapped[str | None] = mapped_column(String(64))
    outcome_account: Mapped[str | None] = mapped_column(String(40))
    outcome_usd: Mapped[str | None] = mapped_column(String(40))
    outcome_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AIConfigOverlay(JournalBase):
    """One row per AI config adjustment (history). Applied rows form the active overlay."""

    __tablename__ = "config_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    parameter: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    previous: Mapped[Any] = mapped_column(JSON, nullable=True)
    classification: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    suggestion_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


def ensure_journal_tables(database: Database) -> None:
    """Idempotent, additive. Safe on a fresh or an existing init-db database."""
    from trading.ai_controls import ensure_control_tables

    names = set(inspect(database.engine).get_table_names())
    if "ai_config_overlay" in names and "config_history" not in names:
        with database.engine.begin() as connection:  # Pre-rename AI-first databases keep history.
            connection.execute(text("ALTER TABLE ai_config_overlay RENAME TO config_history"))
    JournalBase.metadata.create_all(database.engine, checkfirst=True)
    columns = {c["name"] for c in inspect(database.engine).get_columns("ai_decision_journal")}
    if "provider_label" not in columns:  # Additive in-place upgrade; old rows stay NULL ("unknown").
        with database.engine.begin() as connection:
            connection.execute(text("ALTER TABLE ai_decision_journal ADD COLUMN provider_label VARCHAR(64)"))
    ensure_control_tables(database)
    from ai.trade_attribution import ensure_attribution_tables

    ensure_attribution_tables(database)


def _text(value: object, limit: int) -> str:
    return "".join(ch for ch in str(value) if ord(ch) >= 32 and ord(ch) != 127)[:limit]


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("finite Decimal outcome required")
    return str(value)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: int
    kind: str
    action: str
    source: str


class DecisionJournal:
    def __init__(self, database: Database, clock: Clock):
        self.database, self.clock = database, clock
        ensure_journal_tables(database)

    # -- writes ---------------------------------------------------------------------------------
    def record(
        self,
        *,
        kind: str,
        source: str,
        action: str,
        reason: str,
        input_summary: dict | None = None,
        confidence: float | None = None,
        symbol: str | None = None,
        position_id: int | None = None,
        signal_id: int | None = None,
        threshold_reached: int | None = None,
        model: str | None = None,
        adjustments: dict | None = None,
        executed: bool = False,
        rejection_reason: str | None = None,
        final_action: str | None = None,
        provider_label: str | None = None,
    ) -> JournalEntry:
        if kind not in KINDS or source not in SOURCES:
            raise ValueError("unknown journal kind/source")
        if confidence is not None and not 0 <= float(confidence) <= 100:
            raise ValueError("confidence must be 0..100")
        secrets = self.database.secrets
        summary = sanitize_data(input_summary or {}, secrets)
        encoded = canonical_json(summary).encode()
        if len(encoded) > MAX_SUMMARY_BYTES:
            summary = {"truncated": True, "bytes": len(encoded), "keys": sorted(summary)[:40]}
        changes = sanitize_data(adjustments or {}, secrets)
        if len(canonical_json(changes).encode()) > MAX_SUMMARY_BYTES:
            changes = {"truncated": True}
        row = AIDecisionJournal(
            time=self.clock.now(),
            kind=kind,
            symbol=_text(symbol, 64) if symbol else None,
            position_id=position_id,
            signal_id=signal_id,
            threshold_reached=threshold_reached,
            source=source,
            model=_text(model, 128) if model else None,
            provider_label=_text(provider_label, 64) if provider_label else None,
            input_hash=sha256_json(summary),
            input_summary=json.loads(canonical_json(summary)),
            action=_text(action, 32),
            confidence=None if confidence is None else float(confidence),
            reason=_text(sanitize_text(reason, secrets), 600) or "-",
            adjustments=json.loads(canonical_json(changes)),
            executed=bool(executed),
            rejection_reason=_text(rejection_reason, 128) if rejection_reason else None,
            final_action=_text(final_action, 64) if final_action else None,
        )
        with self.database.session() as session:
            session.add(row)
            session.flush()
            return JournalEntry(row.id, row.kind, row.action, row.source)

    def mark(
        self,
        entry_id: int,
        *,
        executed: bool,
        final_action: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.get(AIDecisionJournal, entry_id)
            if row is None:
                raise ValueError("unknown journal entry")
            row.executed = bool(executed)
            row.final_action = _text(final_action, 64) if final_action else row.final_action
            row.rejection_reason = _text(rejection_reason, 128) if rejection_reason else None

    def link_signal(
        self, signal_id: int, *, executed: bool, position_id: int | None = None, final_action: str
    ) -> int:
        """Record what the execution pipeline actually did with an entry decision."""
        with self.database.session() as session:
            rows = session.scalars(
                select(AIDecisionJournal).where(
                    AIDecisionJournal.signal_id == signal_id, AIDecisionJournal.kind == "entry"
                )
            ).all()
            for row in rows:
                row.executed = bool(executed)
                row.final_action = _text(final_action, 64)
                if position_id is not None:
                    row.position_id = position_id
            return len(rows)

    def record_outcome(
        self, position_id: int, *, outcome_account: Decimal, outcome_usd: Decimal | None = None
    ) -> int:
        """Attach a realized result to every still-open journal row for this position."""
        account, usd = _money(outcome_account), _money(outcome_usd)
        with self.database.session() as session:
            rows = session.scalars(
                select(AIDecisionJournal).where(
                    AIDecisionJournal.position_id == position_id, AIDecisionJournal.outcome_at.is_(None)
                )
            ).all()
            for row in rows:
                row.outcome_account, row.outcome_usd, row.outcome_at = account, usd, self.clock.now()
            return len(rows)

    # -- reads ----------------------------------------------------------------------------------
    def trailing_thresholds(self, position_id: int) -> set[int]:
        """Thresholds already handled for this position (survives restarts; no double consult)."""
        with self.database.session() as session:
            values = session.scalars(
                select(AIDecisionJournal.threshold_reached).where(
                    AIDecisionJournal.position_id == position_id,
                    AIDecisionJournal.kind == "trailing",
                    AIDecisionJournal.threshold_reached.is_not(None),
                )
            ).all()
            return {int(v) for v in values}

    @staticmethod
    def _view(row: AIDecisionJournal) -> dict:
        return {
            "id": row.id,
            "time": row.time.isoformat(),
            "kind": row.kind,
            "symbol": row.symbol,
            "position_id": row.position_id,
            "threshold_reached": row.threshold_reached,
            "source": row.source,
            "model": row.model,
            "provider_label": row.provider_label,
            "action": row.action,
            "confidence": row.confidence,
            "reason": row.reason,
            "adjustments": row.adjustments,
            "executed": row.executed,
            "rejection_reason": row.rejection_reason,
            "final_action": row.final_action,
            "outcome_account": row.outcome_account,
            "outcome_usd": row.outcome_usd,
            "input_hash": row.input_hash,
        }

    def recent(self, *, limit: int = 50, offset: int = 0, kind: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("bounded paging required")
        query = select(AIDecisionJournal).order_by(AIDecisionJournal.id.desc())
        if kind is not None:
            if kind not in KINDS:
                raise ValueError("unknown journal kind")
            query = query.where(AIDecisionJournal.kind == kind)
        with self.database.session() as session:
            return [self._view(row) for row in session.scalars(query.offset(offset).limit(limit)).all()]

    def lessons(self, *, limit: int = 10) -> list[str]:
        """Most recent lessons learned, fed back into future prompts (bounded)."""
        with self.database.session() as session:
            rows = session.scalars(
                select(AIDecisionJournal)
                .where(AIDecisionJournal.kind == "lesson")
                .order_by(AIDecisionJournal.id.desc())
                .limit(max(1, min(limit, 20)))
            ).all()
            return [row.reason[:200] for row in rows]

    def stats(self, *, hours: int = 24 * 7) -> dict:
        """AI decision quality: per source/kind counts, execution rate and realized outcome."""
        since = self.clock.now() - timedelta(hours=hours)
        with self.database.session() as session:
            rows = session.execute(
                select(
                    AIDecisionJournal.kind,
                    AIDecisionJournal.source,
                    func.count(),
                    func.sum(case((AIDecisionJournal.executed.is_(True), 1), else_=0)),
                    func.avg(AIDecisionJournal.confidence),
                )
                .where(AIDecisionJournal.time >= since)
                .group_by(AIDecisionJournal.kind, AIDecisionJournal.source)
            ).all()
            outcomes = session.execute(
                select(AIDecisionJournal.source, AIDecisionJournal.outcome_usd).where(
                    AIDecisionJournal.time >= since,
                    AIDecisionJournal.executed.is_(True),
                    AIDecisionJournal.outcome_usd.is_not(None),
                    AIDecisionJournal.kind == "entry",
                )
            ).all()
        groups = [
            {
                "kind": kind,
                "source": source,
                "decisions": int(count),
                "executed": int(executed or 0),
                "avg_confidence": None if avg is None else round(float(avg), 2),
            }
            for kind, source, count, executed, avg in rows
        ]
        by_source: dict[str, dict] = {}
        for source, value in outcomes:
            item = by_source.setdefault(source, {"trades": 0, "wins": 0, "net_usd": Decimal("0")})
            amount = Decimal(value)
            item["trades"] += 1
            item["wins"] += amount > 0
            item["net_usd"] += amount
        performance = {
            source: {
                "trades": item["trades"],
                "win_rate": round(item["wins"] / item["trades"] * 100, 2),
                "net_usd": str(item["net_usd"]),
            }
            for source, item in by_source.items()
        }
        return {"window_hours": hours, "groups": groups, "entry_performance": performance}
