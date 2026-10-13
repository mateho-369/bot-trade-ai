"""Trusted internal decision DTOs. None of these are an HTTP authorization token."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from core.security import canonical_json, sha256_json
from core.settings import Settings
from trading.types import ZERO, BrokerError, Position, SourceKind, aware_utc, valid_key


def json_dict(value: object) -> dict:
    return json.loads(canonical_json(value))


def source_code_hash(root: Path) -> str:
    """Bind runnable local code, not docs/tests, credentials, caches or runtime data."""
    names = [
        "main.py",
        "config.py",
        "requirements.txt",
        "watchdog.py",
        "launcher.py",
        "install.bat",
        "start.bat",
        "start_demo.bat",
    ]
    for directory in (
        "core",
        "trading",
        "strategy",
        "ai",
        "news",
        "app",
        "backtesting",
        "readiness",
        "scripts",
    ):
        folder = root / directory
        if folder.exists():
            names.extend(
                str(path.relative_to(root)).replace("\\", "/")
                for path in folder.rglob("*")
                if path.is_file()
                and path.suffix in {".py", ".js", ".html", ".css", ".vbs", ".ps1"}
                and "__pycache__" not in path.parts
            )
    payload = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in sorted(set(names))
        if (root / name).is_file()
    }
    if getattr(sys, "frozen", False):
        # Compiled deployments have a DISTINCT evidence identity. They cannot
        # reuse source-only stage approvals as if the executable were identical.
        payload["__executable__"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    return sha256_json(payload)


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    code_hash: str
    model_sha256: str
    data_source: SourceKind

    def __post_init__(self):
        valid_key(self.code_hash)
        valid_key(self.model_sha256)
        if not isinstance(self.data_source, SourceKind):
            raise BrokerError("invalid runtime data provenance")

    @classmethod
    def current(cls, settings: Settings, data_source: SourceKind, *, model_sha256: str | None = None):
        code_root = (
            settings.project_root
            if (settings.project_root / "main.py").is_file()
            else Path(__file__).resolve().parents[1]
        )
        code = source_code_hash(code_root)
        # Identifies an explicit deterministic baseline, NOT a trained model.
        model = model_sha256 or sha256_json({"baseline": "rule-based-v1", "code_hash": code})
        return cls(code, model, data_source)


@dataclass(frozen=True, slots=True)
class NewsWindow:
    known: bool = False
    safe: bool = False
    headlines_fetched_at: datetime | None = None
    calendar_fetched_at: datetime | None = None
    calendar_covered_until: datetime | None = None
    evidence_hash: str | None = None

    # Part 8 managed evidence; unbound legacy DTOs are diagnostic-only at native gates.
    logical_symbol: str | None = None
    config_hash: str | None = None
    code_hash: str | None = None
    data_source: SourceKind | None = None
    snapshot_hash: str | None = None
    snapshot_epoch: int | None = None
    expires_at: datetime | None = None
    calendar_covered_from: datetime | None = None
    fixture_only: bool = False

    def __post_init__(self):
        if type(self.known) is not bool or type(self.safe) is not bool:
            raise BrokerError("news flags must be actual booleans")
        for value in (self.headlines_fetched_at, self.calendar_fetched_at, self.calendar_covered_until):
            if value is not None:
                aware_utc(value)
        if self.evidence_hash is not None:
            valid_key(self.evidence_hash)
        if type(self.fixture_only) is not bool:
            raise BrokerError("actual news fixture flag required")
        fields = (
            self.logical_symbol,
            self.config_hash,
            self.code_hash,
            self.data_source,
            self.snapshot_hash,
            self.snapshot_epoch,
            self.expires_at,
        )
        if any(v is not None for v in fields):
            if (
                any(v is None for v in fields)
                or not isinstance(self.logical_symbol, str)
                or not 1 <= len(self.logical_symbol) <= 64
                or not isinstance(self.data_source, SourceKind)
                or type(self.snapshot_epoch) is not int
                or self.snapshot_epoch <= 0
            ):
                raise BrokerError("complete managed news binding required")
            for value in (self.config_hash, self.code_hash, self.snapshot_hash):
                valid_key(value)
            aware_utc(self.expires_at)
        if self.calendar_covered_from is not None:
            aware_utc(self.calendar_covered_from)

    def to_dict(self):
        return json_dict(asdict(self))

    @property
    def managed(self):
        return self.snapshot_hash is not None

    def allows(self, settings: Settings, now: datetime, logical_symbol: str | None = None) -> bool:
        if not (self.known and self.safe and self.evidence_hash):
            return False
        if self.managed and (
            self.config_hash != settings.safety_fingerprint()
            or now >= self.expires_at
            or logical_symbol is not None
            and logical_symbol != self.logical_symbol
            or self.calendar_covered_from is None
            or self.calendar_covered_from > now
            or self.fixture_only
            and self.data_source == SourceKind.MT5
        ):
            return False
        for when, age in (
            (self.headlines_fetched_at, settings.news_max_age_seconds),
            (self.calendar_fetched_at, settings.calendar_max_age_seconds),
        ):
            if when is None or not -2 <= (now - when).total_seconds() <= age:
                return False
        return self.calendar_covered_until is not None and self.calendar_covered_until >= now

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        for name in (
            "headlines_fetched_at",
            "calendar_fetched_at",
            "calendar_covered_until",
            "expires_at",
            "calendar_covered_from",
        ):
            if row.get(name) is not None:
                row[name] = datetime.fromisoformat(row[name])
        if row.get("data_source") is not None:
            row["data_source"] = SourceKind(row["data_source"])
        return cls(**row)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    observed_at: datetime
    bar_closed_at: datetime
    source: SourceKind
    signal_score: float = 0
    ai_confidence: float = 0
    news: NewsWindow = field(default_factory=NewsWindow)
    signal_id: int | None = None
    risk_percent: Decimal | None = None
    features: dict = field(default_factory=dict)

    def __post_init__(self):
        aware_utc(self.observed_at)
        aware_utc(self.bar_closed_at)
        if not isinstance(self.source, SourceKind):
            raise BrokerError("invalid decision provenance")
        for value in (self.signal_score, self.ai_confidence):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise BrokerError("invalid technical/AI confidence")
        if self.risk_percent is not None and (
            not isinstance(self.risk_percent, Decimal)
            or not self.risk_percent.is_finite()
            or self.risk_percent <= ZERO
        ):
            raise BrokerError("invalid reduced risk percent")
        if len(canonical_json(self.features).encode()) > 8192:
            raise BrokerError("decision features exceed 8 KiB")
        # Copy caller-owned containers; the persisted digest binds subsequent use.
        object.__setattr__(self, "features", json_dict(self.features))

    @property
    def digest(self) -> str:
        return sha256_json(asdict(self))

    def to_dict(self) -> dict:
        return json_dict(asdict(self))

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        row["observed_at"] = datetime.fromisoformat(row["observed_at"])
        row["bar_closed_at"] = datetime.fromisoformat(row["bar_closed_at"])
        row["source"] = SourceKind(row["source"])
        row["news"] = NewsWindow.from_dict(row["news"])
        if row.get("risk_percent") is not None:
            row["risk_percent"] = Decimal(row["risk_percent"])
        return cls(**row)


def position_review_hash(position: Position) -> str:
    # Exclude floating P&L/SL: an improved SL must not invalidate a hold review.
    # TP/volume/ticket changes DO invalidate it; final authority rechecks protection.
    return sha256_json(
        {
            "ticket": position.ticket,
            "identifier": position.identifier,
            "symbol": position.symbol,
            "direction": position.side.value,
            "volume": position.volume,
            "entry_price": position.entry_price,
            "tp": position.tp,
            "magic": position.magic,
            "opened_at": position.time,
        }
    )


@dataclass(frozen=True, slots=True)
class PositionReview:
    observed_at: datetime
    source: SourceKind
    ai_confidence: float
    momentum_continues: bool = False
    volatility_safe: bool = False
    news: NewsWindow = field(default_factory=NewsWindow)
    position_identifier: int | None = None
    position_hash: str | None = None
    code_hash: str | None = None
    model_sha256: str | None = None

    def __post_init__(self):
        aware_utc(self.observed_at)
        binding = (self.position_identifier, self.position_hash, self.code_hash, self.model_sha256)
        if any(v is not None for v in binding):
            if (
                type(self.position_identifier) is not int
                or self.position_identifier <= 0
                or any(v is None for v in binding)
            ):
                raise BrokerError("complete positive position-review binding required")
            for v in binding[1:]:
                valid_key(v)
        if (
            not isinstance(self.source, SourceKind)
            or isinstance(self.ai_confidence, bool)
            or not isinstance(self.ai_confidence, (int, float))
            or not math.isfinite(self.ai_confidence)
            or not 0 <= self.ai_confidence <= 100
        ):
            raise BrokerError("invalid position-review confidence/source")
        if type(self.momentum_continues) is not bool or type(self.volatility_safe) is not bool:
            raise BrokerError("review flags must be actual booleans")

    def allows_extension(
        self,
        settings: Settings,
        now: datetime,
        source: SourceKind,
        *,
        position: Position | None = None,
        profile: RuntimeProfile | None = None,
    ) -> bool:
        if source == SourceKind.MT5 and self.position_identifier is None:
            return False  # Native extension may never use an unbound legacy generic review.
        if self.position_identifier is not None and (
            position is None
            or position.identifier != self.position_identifier
            or position_review_hash(position) != self.position_hash
            or profile is not None
            and (self.code_hash != profile.code_hash or self.model_sha256 != profile.model_sha256)
        ):
            return False
        return (
            settings.allow_tp_extension
            and self.source == source
            and -2 <= (now - self.observed_at).total_seconds() <= settings.order_max_age_seconds
            and self.ai_confidence >= settings.ai_confidence_threshold
            and self.momentum_continues
            and self.volatility_safe
            and self.news.allows(settings, now)
        )

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        row["observed_at"] = datetime.fromisoformat(row["observed_at"])
        row["source"] = SourceKind(row["source"])
        row["news"] = NewsWindow.from_dict(row["news"])
        return cls(**row)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    risk_account: Decimal = ZERO
    aggregate_risk_account: Decimal = ZERO
    risk_budget_account: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class OwnedTrade:
    trade_id: int
    ticket: int
    identifier: int
    symbol: str
    direction: str
    volume: Decimal
    original_volume: Decimal
    entry_price: Decimal
    original_tp: Decimal
    target_usd: Decimal
    entry_costs_account: Decimal
    lock_level: float
    intent_key: str
