"""Provider content is untrusted. Strict typed replies cannot become broker commands."""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from ai.json_validation import AIInvalidResponse, strict_json
from trading.types import SourceKind

Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Reason = Literal[
    "trend_confirmed",
    "range_confirmed",
    "momentum_confirmed",
    "technical_conflict",
    "risk_high",
    "news_risk",
    "volatility_high",
    "spread_high",
    "uncertain",
    "insufficient_context",
    "quality_confirmed",
    "risk_reduction",
    "weight_rebalance",
    "position_deteriorating",
    "position_stable",
    "no_change",
    "untrusted_content",
]
Purpose = Literal["entry", "market", "position", "news", "settings"]


def decimal_text(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,8})?", value):
        raise ValueError("bounded Decimal text required, not booleans/floats/NaN")
    return Decimal(value)


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    request_hash: Hash
    source: Literal["mt5", "synthetic", "test_sdk", "paper"]
    code_hash: Hash
    model_sha256: Hash
    confidence: Score
    reason_codes: tuple[Reason, ...]
    rationale: Annotated[StrictStr, Field(min_length=1, max_length=600)]

    @field_validator("confidence", mode="before")
    @classmethod
    def numeric_score(cls, value):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("finite numeric heuristic required")
        return float(value)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def array_codes(cls, value):
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8:
            raise ValueError("bounded nonempty reason-code array required")
        if len(set(value)) != len(value):
            raise ValueError("duplicate reason codes")
        return tuple(value)

    @field_validator("rationale")
    @classmethod
    def clean_text(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("single-line bounded rationale required")
        return value.strip()


class EntryReply(Reply):
    format: Literal["reflex-ai-entry-v1"]
    proposal_hash: Hash
    news_hash: Hash
    decision: Literal["approve", "reject", "wait"]
    risk_percent: Annotated[Decimal | None, Field(gt=0, le=1)]

    @field_validator("risk_percent", mode="before")
    @classmethod
    def risk_text(cls, value):
        return decimal_text(value)


class MarketReply(Reply):
    format: Literal["reflex-ai-market-v1"]
    regime: Literal["trend", "range", "volatile", "unknown"]
    sentiment: Literal["bullish", "bearish", "neutral"]


class NewsReply(Reply):
    format: Literal["reflex-ai-news-v1"]
    sentiment: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    impact: Literal["low", "medium", "high", "unknown"]

    @field_validator("sentiment", mode="before")
    @classmethod
    def sentiment_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("sentiment must be a finite number")
        return float(value)


class PositionReply(Reply):
    format: Literal["reflex-ai-position-v1"]
    position_hash: Hash
    news_hash: Hash
    decision: Literal["hold", "close", "reduce", "wait"]
    momentum_continues: StrictBool
    volatility_safe: StrictBool
    close_fraction: Annotated[Decimal | None, Field(gt=0, le=1)]

    @field_validator("close_fraction", mode="before")
    @classmethod
    def fraction_text(cls, value):
        return decimal_text(value)

    @model_validator(mode="after")
    def action_shape(self):
        if self.decision == "close" and self.close_fraction != 1:
            raise ValueError("close must specify the entire owned position")
        if self.decision == "reduce" and (
            self.close_fraction is None or self.close_fraction > Decimal("0.5")
        ):
            raise ValueError("reduction must be at most half; it is only an owner proposal")
        if self.decision in {"hold", "wait"} and self.close_fraction is not None:
            raise ValueError("hold/wait may not hide an action")
        if self.decision != "hold" and (self.momentum_continues or self.volatility_safe):
            raise ValueError("non-hold cannot approve protective target extension")
        return self


class SettingsReply(Reply):
    format: Literal["reflex-ai-settings-v1"]
    action: Literal["no_change", "reduce_risk", "rebalance_weights"]
    risk_percent: Annotated[Decimal | None, Field(gt=0, le=1)]
    weights: dict[str, Decimal] | None

    @field_validator("risk_percent", mode="before")
    @classmethod
    def risk_text(cls, value):
        return decimal_text(value)

    @field_validator("weights", mode="before")
    @classmethod
    def strict_weights(cls, value):
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"trend", "mean_reversion", "breakout", "momentum"}:
            raise ValueError("all four exact strategy keys required")
        result = {name: decimal_text(item) for name, item in value.items()}
        if any(item is None or not 0 <= item <= 1 for item in result.values()) or sum(result.values()) != 1:
            raise ValueError("weights must total exactly one")
        return result

    @model_validator(mode="after")
    def action_shape(self):
        expected = {
            "no_change": (False, False),
            "reduce_risk": (True, False),
            "rebalance_weights": (False, True),
        }[self.action]
        if (self.risk_percent is not None, self.weights is not None) != expected:
            raise ValueError("exactly the named action parameters are permitted")
        return self


REPLY_CLASSES = {
    "entry": EntryReply,
    "market": MarketReply,
    "position": PositionReply,
    "news": NewsReply,
    "settings": SettingsReply,
}


def decode_reply(content: str, purpose: Purpose, binding: dict) -> Reply:
    try:
        cls = REPLY_CLASSES[purpose]
        values = strict_json(content, max_bytes=16384, max_string=1000, allow_fence=True)
        reply = cls.model_validate(values)
        expected = {key: binding[key] for key in ("request_hash", "source", "code_hash", "model_sha256")}
        if purpose in {"entry", "position"}:
            expected["news_hash"] = binding["news_hash"]
            key = "proposal_hash" if purpose == "entry" else "position_hash"
            expected[key] = binding[key]
        if any(getattr(reply, key) != value for key, value in expected.items()):
            raise ValueError("bound request identity changed")
        if reply.source not in {item.value for item in SourceKind}:
            raise ValueError
        return reply
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise AIInvalidResponse("invalid_or_unbound_reply") from None
