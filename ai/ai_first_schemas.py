"""Strict JSON contracts for the AI-FIRST brain, adaptive trailing, config review and trade review.

Every contract has (a) a hand-reviewed Groq/OpenAI ``json_schema`` strict schema (all properties
required, ``additionalProperties: false``, nullable values as ``[type, "null"]``) and (b) a local
pydantic validator that runs REGARDLESS of provider enforcement. Anything that fails local
validation is an *invalid reply*: callers fall back to the deterministic rule path, never guess.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_REPLY_BYTES = 16384
STRATEGY_KEYS = ("trend", "mean_reversion", "breakout", "momentum")
DECISION_ACTIONS = ("open_buy", "open_sell", "wait", "close_position", "modify_sl", "modify_tp")
TRAILING_DECISIONS = ("hold_to_60", "hold_to_90", "close_now", "tighten_lock", "extend_tp_to_120")
# What the AI may answer after each MECHANICAL lock. Anything else is invalid => mechanical rules.
TRAILING_ALLOWED: dict[int, tuple[str, ...]] = {
    30: ("hold_to_60", "hold_to_90", "close_now", "tighten_lock"),
    60: ("hold_to_90", "close_now", "tighten_lock"),
    90: ("extend_tp_to_120", "close_now", "tighten_lock"),
}


_BOOL_FIELDS = frozenset({"pause_recommended", "risk_appropriate", "trailing_effective"})


class InvalidAIReply(ValueError):
    """Provider content that is not exactly one of the reviewed contracts."""


Confidence = Annotated[float, Field(ge=0, le=100)]
Reason = Annotated[str, Field(min_length=1, max_length=600)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _no_bool_numbers(cls, value, info):
        # JSON true/false must never satisfy a numeric field (bool is an int subclass).
        if isinstance(value, bool) and info.field_name not in _BOOL_FIELDS:
            raise ValueError("boolean is not a number/enum")
        return value

    @field_validator("reason", "lessons", check_fields=False)
    @classmethod
    def _printable(cls, value):
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            if any(ord(ch) < 32 and ch not in "\n\t" or ord(ch) == 127 for ch in item):
                raise ValueError("control characters are not allowed")
        return value


class AIDecision(_Strict):
    """Requirement 1 decision, consulted before every entry/close/modify action."""

    action: Literal["open_buy", "open_sell", "wait", "close_position", "modify_sl", "modify_tp"]
    confidence: Confidence
    reason: Reason
    suggested_risk_percent: Annotated[float, Field(ge=0.1, le=1.0)]
    suggested_target_profit: Annotated[float, Field(ge=0, le=1000)]
    suggested_sl_distance: Annotated[float, Field(ge=0, le=1_000_000)]
    news_risk: Literal["low", "medium", "high"]
    market_condition: Literal["trending", "ranging", "volatile", "news_impact"]

    @property
    def opens(self) -> bool:
        return self.action in {"open_buy", "open_sell"}


class TrailingDecision(_Strict):
    """Lock-first trailing advice, requested only AFTER the mechanical lock is confirmed."""

    decision: Literal["hold_to_60", "hold_to_90", "close_now", "tighten_lock", "extend_tp_to_120"]
    confidence: Confidence
    reason: Reason


class StrategyWeights(_Strict):
    trend: Annotated[float, Field(ge=0, le=1)]
    mean_reversion: Annotated[float, Field(ge=0, le=1)]
    breakout: Annotated[float, Field(ge=0, le=1)]
    momentum: Annotated[float, Field(ge=0, le=1)]


class ConfigSuggestion(_Strict):
    """Requirement 2: SUGGESTIONS only. Bounds/hard limits are enforced by ai.config_adjuster."""

    risk_percent_per_trade: float | None
    target_profit_per_trade: float | None
    max_daily_trades: int | None
    max_spread_points: int | None
    skip_trailing_levels: Annotated[list[Literal[30, 60, 90]], Field(max_length=3)]
    strategy_weights: StrategyWeights | None
    symbols_to_trade: (
        Annotated[list[Annotated[str, Field(min_length=1, max_length=32)]], Field(max_length=30)] | None
    )
    pause_recommended: bool
    confidence: Confidence
    reason: Reason


class TradeReview(_Strict):
    """Requirement 5 learning-loop analysis of ONE closed trade."""

    entry_timing: Literal["good", "early", "late", "unknown"]
    risk_appropriate: bool
    news_effect: Literal["none", "helped", "hurt", "unknown"]
    trailing_effective: bool
    lessons: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=5)]
    confidence: Confidence
    reason: Reason

    @field_validator("risk_appropriate", "trailing_effective", mode="before")
    @classmethod
    def _real_bool(cls, value):
        if type(value) is not bool:
            raise ValueError("actual boolean required")
        return value


# ---------------------------------------------------------------------------------------------
# Reviewed provider schemas (Groq strict mode). Keep in lock-step with the models above; the test
# suite asserts every property is required and every object forbids additional properties.
# ---------------------------------------------------------------------------------------------
_CONF = {"type": "number", "minimum": 0, "maximum": 100}
_REASON = {"type": "string", "maxLength": 600}

STRICT_SCHEMAS: dict[str, dict] = {
    "decision": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(DECISION_ACTIONS)},
            "confidence": _CONF,
            "reason": _REASON,
            "suggested_risk_percent": {"type": "number", "minimum": 0.1, "maximum": 1.0},
            "suggested_target_profit": {"type": "number", "minimum": 0},
            "suggested_sl_distance": {"type": "number", "minimum": 0},
            "news_risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "market_condition": {
                "type": "string",
                "enum": ["trending", "ranging", "volatile", "news_impact"],
            },
        },
        "required": [
            "action",
            "confidence",
            "reason",
            "suggested_risk_percent",
            "suggested_target_profit",
            "suggested_sl_distance",
            "news_risk",
            "market_condition",
        ],
        "additionalProperties": False,
    },
    "trailing": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": list(TRAILING_DECISIONS)},
            "confidence": _CONF,
            "reason": _REASON,
        },
        "required": ["decision", "confidence", "reason"],
        "additionalProperties": False,
    },
    "config": {
        "type": "object",
        "properties": {
            "risk_percent_per_trade": {"type": ["number", "null"]},
            "target_profit_per_trade": {"type": ["number", "null"]},
            "max_daily_trades": {"type": ["integer", "null"]},
            "max_spread_points": {"type": ["integer", "null"]},
            "skip_trailing_levels": {"type": "array", "items": {"type": "integer", "enum": [30, 60, 90]}},
            "strategy_weights": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {key: {"type": "number"} for key in STRATEGY_KEYS},
                        "required": list(STRATEGY_KEYS),
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ]
            },
            "symbols_to_trade": {"type": ["array", "null"], "items": {"type": "string"}},
            "pause_recommended": {"type": "boolean"},
            "confidence": _CONF,
            "reason": _REASON,
        },
        "required": [
            "risk_percent_per_trade",
            "target_profit_per_trade",
            "max_daily_trades",
            "max_spread_points",
            "skip_trailing_levels",
            "strategy_weights",
            "symbols_to_trade",
            "pause_recommended",
            "confidence",
            "reason",
        ],
        "additionalProperties": False,
    },
    "trade_review": {
        "type": "object",
        "properties": {
            "entry_timing": {"type": "string", "enum": ["good", "early", "late", "unknown"]},
            "risk_appropriate": {"type": "boolean"},
            "news_effect": {"type": "string", "enum": ["none", "helped", "hurt", "unknown"]},
            "trailing_effective": {"type": "boolean"},
            "lessons": {"type": "array", "items": {"type": "string", "maxLength": 200}},
            "confidence": _CONF,
            "reason": _REASON,
        },
        "required": [
            "entry_timing",
            "risk_appropriate",
            "news_effect",
            "trailing_effective",
            "lessons",
            "confidence",
            "reason",
        ],
        "additionalProperties": False,
    },
}

MODELS: dict[str, type[_Strict]] = {
    "decision": AIDecision,
    "trailing": TrailingDecision,
    "config": ConfigSuggestion,
    "trade_review": TradeReview,
}


def schema_name(schema: dict) -> str | None:
    """Name of a REVIEWED AI-first schema (identity by content), else None."""
    return next((name for name, known in STRICT_SCHEMAS.items() if known == schema), None)


def _reject_constant(token: str):
    raise InvalidAIReply(f"non-finite JSON number {token}")


def decode(kind: str, content: str) -> _Strict:
    """Parse provider text into exactly one reviewed contract or raise InvalidAIReply."""
    model = MODELS.get(kind)
    if model is None:
        raise InvalidAIReply("unknown contract")
    if not isinstance(content, str) or not content.strip() or len(content.encode()) > MAX_REPLY_BYTES:
        raise InvalidAIReply("empty or oversized reply")
    try:
        data = json.loads(content, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise InvalidAIReply("reply is not JSON") from None
    if not isinstance(data, dict):
        raise InvalidAIReply("reply must be one JSON object")
    for value in data.values():
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidAIReply("non-finite number")
    try:
        return model.model_validate(data)
    except ValidationError:
        raise InvalidAIReply("reply does not match the reviewed contract") from None
