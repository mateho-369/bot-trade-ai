"""Immutable technical contracts; votes/reviews are NOT broker write permits."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from core.security import canonical_json, sha256_json
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import BrokerError, Side, SourceKind, SymbolInfo, Tick, aware_utc, valid_key

ROUTER_ID = "weighted_router_v1"
SIGNAL_FORMAT = "reflex-signal-v1"
FEATURE_FORMAT = "reflex-features-v1"
STRATEGY_NAMES = ("trend", "mean_reversion", "breakout", "momentum")


def score_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BrokerError("score must be a finite numeric heuristic")
    if not 0 <= value <= 100:
        raise BrokerError("score must be in [0,100]")
    return float(value)


def reason_codes(values: tuple[str, ...]) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) > 16
        or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", item) for item in values)
    ):
        raise BrokerError("bounded internal reason codes required")


@dataclass(frozen=True, slots=True)
class CandlePatterns:
    bullish_engulfing: bool
    bearish_engulfing: bool
    hammer: bool
    shooting_star: bool
    doji: bool
    inside_bar: bool
    body_fraction: float
    close_location: float

    def __post_init__(self):
        for name in (
            "bullish_engulfing",
            "bearish_engulfing",
            "hammer",
            "shooting_star",
            "doji",
            "inside_bar",
        ):
            if type(getattr(self, name)) is not bool:
                raise BrokerError("pattern flags must be booleans")
        for value in (self.body_fraction, self.close_location):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise BrokerError("invalid candle geometry")
            if not 0 <= value <= 1:
                raise BrokerError("invalid normalized candle geometry")


@dataclass(frozen=True, slots=True)
class TimeframeFeatures:
    timeframe: str
    bar_time: datetime
    closed_at: datetime
    bars: int
    history_hash: str
    metrics: tuple[tuple[str, float], ...]
    patterns: CandlePatterns

    def __post_init__(self):
        from core.settings import TIMEFRAME_MINUTES

        if self.timeframe not in TIMEFRAME_MINUTES or type(self.bars) is not int or self.bars < 200:
            raise BrokerError("insufficient/invalid feature timeframe")
        if aware_utc(self.closed_at) <= aware_utc(self.bar_time):
            raise BrokerError("invalid finalized bar boundary")
        valid_key(self.history_hash)
        keys = [key for key, _ in self.metrics]
        if len(set(keys)) != len(keys) or not 1 <= len(keys) <= 50:
            raise BrokerError("invalid feature keys")
        for key, value in self.metrics:
            if (
                not re.fullmatch(r"[a-z0-9_]{1,40}", key)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise BrokerError("nonfinite/invalid technical feature")

    def value(self, name: str) -> float:
        for key, value in self.metrics:
            if key == name:
                return value
        raise BrokerError("required feature is missing")

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "bar_time": self.bar_time.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "bars": self.bars,
            "history_hash": self.history_hash,
            "metrics": dict(self.metrics),
            "patterns": asdict(self.patterns),
        }


@dataclass(frozen=True, slots=True)
class FeatureBundle:
    logical_symbol: str
    symbol: str
    source: SourceKind
    observed_at: datetime
    info: SymbolInfo
    tick: Tick
    primary: TimeframeFeatures
    higher: TimeframeFeatures
    trend: TimeframeFeatures

    def __post_init__(self):
        now = aware_utc(self.observed_at)
        if (
            not isinstance(self.source, SourceKind)
            or self.tick.symbol != self.symbol
            or self.info.name != self.symbol
        ):
            raise BrokerError("feature symbol/provenance changed")
        if self.primary.closed_at > now or any(
            item.closed_at > self.primary.closed_at for item in (self.higher, self.trend)
        ):
            raise BrokerError("higher timeframe is not aligned as-of primary close")

    @property
    def history_hash(self) -> str:
        return sha256_json(
            {
                "symbol": self.symbol,
                "source": self.source.value,
                "frames": [
                    (item.timeframe, item.history_hash) for item in (self.primary, self.higher, self.trend)
                ],
            }
        )

    def to_dict(self) -> dict:
        return {
            "format": FEATURE_FORMAT,
            "logical_symbol": self.logical_symbol,
            "symbol": self.symbol,
            "source": self.source.value,
            "observed_at": self.observed_at.isoformat(),
            "history_hash": self.history_hash,
            "quote": {
                "bid": str(self.tick.bid),
                "ask": str(self.tick.ask),
                "time": self.tick.time.isoformat(),
            },
            "frames": [item.to_dict() for item in (self.primary, self.higher, self.trend)],
        }


@dataclass(frozen=True, slots=True)
class StrategyVote:
    strategy: str
    side: Side | None
    score: float
    reasons: tuple[str, ...]

    def __post_init__(self):
        if self.strategy not in STRATEGY_NAMES or self.side is not None and not isinstance(self.side, Side):
            raise BrokerError("unknown strategy/side")
        score_value(self.score)
        reason_codes(self.reasons)
        if self.side is None and self.score != 0:
            raise BrokerError("an abstention must not create vote strength")

    @classmethod
    def wait(cls, strategy: str, reason: str):
        return cls(strategy, None, 0, (reason,))

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "direction": self.side.value if self.side else "wait",
            "score": self.score,
            "reasons": list(self.reasons),
        }


class BaseStrategy(Protocol):
    name: str

    def evaluate(self, features: FeatureBundle) -> StrategyVote: ...


@dataclass(frozen=True, slots=True)
class TechnicalDecision:
    side: Side | None
    score: float
    votes: tuple[StrategyVote, ...]
    coverage: Decimal
    agreement: Decimal
    reasons: tuple[str, ...]

    def __post_init__(self):
        score_value(self.score)
        reason_codes(self.reasons)
        if self.side is not None and not isinstance(self.side, Side):
            raise BrokerError("invalid routed side")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1
            for value in (self.coverage, self.agreement)
        ):
            raise BrokerError("invalid router coverage/agreement")
        if self.side is None and self.score != 0:
            raise BrokerError("wait cannot be represented as approval confidence")

    def to_dict(self) -> dict:
        return {
            "direction": self.side.value if self.side else "wait",
            "score": self.score,
            "votes": [vote.to_dict() for vote in self.votes],
            "coverage": str(self.coverage),
            "agreement": str(self.agreement),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class AIEntryReview:
    observed_at: datetime
    source: SourceKind
    proposal_hash: str
    code_hash: str
    model_sha256: str
    news_hash: str
    decision: Literal["approve", "reject", "wait"]
    confidence: float
    # Registry label of the AI that answered (groq, groq2, groq+groq2 for all_must_approve, ollama,
    # openai...), or a provenance marker: test (simulated), replay (backtest), rule_fallback.
    provider: str
    risk_percent: Decimal | None = None
    request_hash: str | None = None
    provider_model: str | None = None

    def __post_init__(self):
        aware_utc(self.observed_at)
        if self.request_hash is not None:
            valid_key(self.request_hash)
        if self.provider_model is not None and (
            not isinstance(self.provider_model, str)
            or not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,128}", self.provider_model)
        ):
            raise BrokerError("invalid reviewed provider model identifier")
        if not isinstance(self.source, SourceKind) or self.decision not in {"approve", "reject", "wait"}:
            raise BrokerError("invalid AI decision/provenance")
        if not isinstance(self.provider, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_.+-]{0,63}", self.provider
        ):
            raise BrokerError("unknown review provider")
        for value in (self.proposal_hash, self.code_hash, self.model_sha256, self.news_hash):
            valid_key(value)
        score_value(self.confidence)
        if self.risk_percent is not None and (
            not isinstance(self.risk_percent, Decimal)
            or not self.risk_percent.is_finite()
            or self.risk_percent <= 0
        ):
            raise BrokerError("review risk must be a positive Decimal reduction")

    @property
    def digest(self) -> str:
        return sha256_json(asdict(self))

    def to_dict(self) -> dict:
        return json.loads(canonical_json(asdict(self)))

    @classmethod
    def from_dict(cls, values: dict):
        try:
            row = dict(values)
            row["observed_at"] = datetime.fromisoformat(row["observed_at"])
            row["source"] = SourceKind(row["source"])
            if row.get("risk_percent") is not None:
                if not isinstance(row["risk_percent"], str):
                    raise ValueError
                row["risk_percent"] = Decimal(row["risk_percent"])
            return cls(**row)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise BrokerError("invalid stored review contract") from None


@dataclass(frozen=True, slots=True)
class SignalResult:
    signal_id: int | None
    state: str
    symbol: str
    source: SourceKind
    side: Side | None
    score: float
    proposal_hash: str | None
    stop_price: Decimal | None
    reasons: tuple[str, ...]
    payload_json: str = "{}"
    context: DecisionContext | None = None

    def __post_init__(self):
        if self.state not in {"pending", "approved", "rejected", "wait", "revoked", "expired", "blocked"}:
            raise BrokerError("invalid signal state")
        score_value(self.score)
        reason_codes(self.reasons)
        if self.proposal_hash is not None:
            valid_key(self.proposal_hash)
        if len(self.payload_json.encode()) > 32768:
            raise BrokerError("signal payload exceeds 32 KiB")
        if self.state == "approved" and (
            self.context is None or self.side is None or self.stop_price is None
        ):
            raise BrokerError("approved signal has no bound context/protection")

    @property
    def approved(self) -> bool:
        # Technical/review approval ONLY. Owner/risk/stage approval remains separate.
        return self.state == "approved" and self.context is not None

    def payload(self) -> dict:
        return json.loads(self.payload_json)  # A fresh copy; cannot mutate a persisted review request.


class EntryReviewer(Protocol):
    async def review(self, proposal: SignalResult, news: NewsWindow) -> AIEntryReview | None: ...
