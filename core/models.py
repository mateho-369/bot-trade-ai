"""Portable SQLAlchemy schema with exact decimals and UTC-aware timestamps."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive timestamps are forbidden")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ExactDecimal(TypeDecorator[Decimal]):
    # SQLite NUMERIC affinity silently uses binary floats. Store decimal strings
    # there; aggregate money in Python Decimal, never SQL SUM(text_column).
    impl = String(64)
    cache_ok = True

    def __init__(self, scale: int = 8) -> None:
        if not 0 <= scale <= 12:
            raise ValueError("invalid decimal scale")
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(28, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | str | int | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (float, bool)):
            raise TypeError("use Decimal(str(value)), not floats/bools, for financial columns")
        number = Decimal(value)
        if not number.is_finite() or abs(number) >= Decimal(10) ** (28 - self.scale):
            raise ValueError("financial value is non-finite or exceeds precision")
        try:
            number = number.quantize(Decimal(1).scaleb(-self.scale), rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError("financial value exceeds precision") from exc
        return format(number, "f") if dialect.name == "sqlite" else number

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(str(value))


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    __table_args__ = (CheckConstraint("id = 1", name="ck_schema_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BrokerDeal(Base):
    """Deduplicated fill/cash-flow ledger; reads deposits, never initiates them."""

    __tablename__ = "broker_deals"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_deal_account_ticket"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_deal_mode"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    ticket: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    entry: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    magic: Mapped[int] = mapped_column(BigInteger, default=0)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(ExactDecimal(12), default=Decimal("0"))
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    fee: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    comment: Mapped[str] = mapped_column(String(128), default="")


class OrderIntent(Base):
    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_intent_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_intent_direction"),
        CheckConstraint(
            "state IN ('prepared','submitting','acknowledged','rejected','unknown','reconciled','canceled')",
            name="ck_intent_state",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="prepared", index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    broker_request_id: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_trade_account_ticket"),
        UniqueConstraint("account_key", "mode", "position_identifier", name="uq_trade_position_id"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_trade_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_trade_direction"),
        CheckConstraint("status IN ('open','closed','unknown')", name="ck_trade_status"),
        CheckConstraint("CAST(volume AS NUMERIC) > 0", name="ck_trade_volume"),
        CheckConstraint(
            "signal_score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_trade_scores"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    order_intent_id: Mapped[str] = mapped_column(ForeignKey("order_intents.id"), unique=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    sl: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    tp: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    initial_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    target_profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    profit_lock_level: Mapped[float] = mapped_column(Float, default=0)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, nullable=False)
    news_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    comment: Mapped[str] = mapped_column(String(128), default="")
    close_reason: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "mode", "symbol", "timeframe", "bar_time", "strategy", "config_hash", name="uq_signal_bar"
        ),
        CheckConstraint("direction IN ('buy','sell','wait')", name="ck_signal_direction"),
        CheckConstraint("score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_signal_scores"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    bar_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, default=0)
    final_decision: Mapped[str] = mapped_column(String(24), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class News(Base):
    __tablename__ = "news"
    __table_args__ = (
        CheckConstraint("impact IN ('low','medium','high','unknown')", name="ck_news_impact"),
        CheckConstraint("sentiment BETWEEN -1 AND 1", name="ck_news_sentiment"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(String(10), default="unknown")
    sentiment: Mapped[float] = mapped_column(Float, default=0)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AISuggestion(Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low','medium','high')", name="ck_suggestion_risk"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','expired')", name="ck_suggestion_status"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    suggestion: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), default="high")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    based_on_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_scope: Mapped[str] = mapped_column(String(12), default="candidate")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BotState(Base):
    __tablename__ = "bot_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_state_singleton"),
        CheckConstraint("desired_state IN ('paused','running','killed')", name="ck_state_name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    desired_state: Mapped[str] = mapped_column(String(10), default="paused")
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    session_id: Mapped[str | None] = mapped_column(String(36))
    settings_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_config_hash: Mapped[str | None] = mapped_column(String(64))
    heartbeat: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    balance: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RiskState(Base):
    __tablename__ = "risk_state"
    __table_args__ = (UniqueConstraint("account_key", "mode", name="uq_risk_account_mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    day_start_equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity_high_water: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    net_realized_today: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    accepted_entries_today: Mapped[int] = mapped_column(Integer, default=0)
    reserved_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    daily_loss_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    drawdown_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DeploymentEvidence(Base):
    __tablename__ = "deployment_evidence"
    __table_args__ = (CheckConstraint("stage IN ('backtest','paper','demo')", name="ck_evidence_stage"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str | None] = mapped_column(String(160))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OwnerApproval(Base):
    __tablename__ = "owner_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','consumed','expired')", name="ck_approval_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_is_append_only(*args: Any) -> None:
    raise RuntimeError("audit logs are append-only")
