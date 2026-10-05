"""Strict, bounded local replay contracts; declarations are not authenticity attestations."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.types import BrokerError, SymbolInfo

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.#-]{1,64}$")]
Money = Annotated[Decimal, Field(allow_inf_nan=False)]


def utc_time(value: str | datetime) -> datetime:
    if isinstance(value, str) and len(value) <= 40:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("explicit timezone-aware timestamp required") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("explicit timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def decimal_text(value) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError("exact, non-exponential decimal text required")
    if len(value) > 48:
        raise ValueError("bounded decimal text required")
    result = Decimal(value)
    if not result.is_finite() or abs(result) > Decimal("1e18"):
        raise ValueError("bounded finite decimal required")
    return result


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class LocalFile(FrozenContract):
    path: Annotated[str, Field(min_length=1, max_length=128)]
    sha256: Hash

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        # Cross-platform restriction: no URI, Windows drive, traversal, backslash or absolute path.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value):
            raise ValueError("relative local dataset path required")
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("dataset traversal forbidden")
        return value


class SymbolFile(LocalFile):
    symbol: Name


class HistoricalSpec(FrozenContract):
    logical_symbol: Name
    name: Name
    point: Money
    tick_size: Money
    contract_size: Money
    volume_min: Money
    volume_max: Money
    volume_step: Money
    digits: Annotated[int, Field(strict=True, ge=0, le=12)]
    currency_base: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,8}$")]
    currency_profit: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,8}$")]
    tick_value_profit: Money = Decimal("0")
    tick_value_loss: Money = Decimal("0")
    stops_level: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    freeze_level: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    trade_mode: Annotated[int, Field(strict=True, ge=0, le=4)] = 4
    profit_model: Literal["linear_contract"] = "linear_contract"
    margin_model: Literal["notional", "forex_base"] = "notional"
    effective_from: datetime
    description: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator(
        "point",
        "tick_size",
        "contract_size",
        "volume_min",
        "volume_max",
        "volume_step",
        "tick_value_profit",
        "tick_value_loss",
        mode="before",
        json_schema_input_type=str,
    )
    @classmethod
    def exact_money(cls, value):
        # Defaults are already Decimal; caller-supplied financial fields must be strings.
        if isinstance(value, Decimal):
            return value
        return decimal_text(value)

    @field_validator("effective_from", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    def broker_info(self) -> SymbolInfo:
        return SymbolInfo(
            **{
                name: getattr(self, name)
                for name in (
                    "name",
                    "point",
                    "tick_size",
                    "contract_size",
                    "volume_min",
                    "volume_max",
                    "volume_step",
                    "digits",
                    "currency_base",
                    "currency_profit",
                    "tick_value_profit",
                    "tick_value_loss",
                    "stops_level",
                    "freeze_level",
                    "trade_mode",
                )
            }
        )

    @model_validator(mode="after")
    def valid_spec(self):
        try:
            self.broker_info()
        except BrokerError:
            raise ValueError("invalid historical broker contract") from None
        return self


class SessionInterval(FrozenContract):
    symbol: Name
    start: datetime
    end: datetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def interval(self):
        if not self.start < self.end or self.start.second or self.start.microsecond:
            raise ValueError("positive, minute-aligned session interval required")
        if self.end.second or self.end.microsecond:
            raise ValueError("minute-aligned session end required")
        return self


class Origin(FrozenContract):
    kind: Literal["synthetic_fixture", "historical_import"]
    description: Annotated[str, Field(min_length=1, max_length=1200)]
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    acquired_at: datetime
    license_note: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator("acquired_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)


class ReplayModelSelection(FrozenContract):
    format: Literal["reflex-replay-model-selection-v1"]
    purpose: Literal["research_only"]
    description: Annotated[str, Field(min_length=1, max_length=1024)]
    available_at: datetime
    selected_at: datetime
    artifact: LocalFile
    learning_dataset: LocalFile

    @field_validator("available_at", "selected_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def frozen(self):
        if self.available_at > self.selected_at or self.artifact.path == self.learning_dataset.path:
            raise ValueError("distinct files and causal model availability/selection required")
        return self


class DatasetManifest(FrozenContract):
    format: Literal["reflex-history-v1"]
    origin: Origin
    quote_mode: Literal["ticks", "ohlc_conservative"]
    account_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,8}$")]
    replay_from: datetime
    replay_until: datetime
    symbols: Annotated[tuple[HistoricalSpec, ...], Field(min_length=1, max_length=40)]
    bars: Annotated[tuple[SymbolFile, ...], Field(min_length=1, max_length=40)]
    ticks: Annotated[tuple[SymbolFile, ...], Field(max_length=40)] = ()
    sessions: Annotated[tuple[SessionInterval, ...], Field(min_length=1, max_length=4096)]
    news: LocalFile | None = None
    reviews: LocalFile | None = None
    model: ReplayModelSelection | None = None

    @field_validator("replay_from", "replay_until", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def bindings(self):
        names = [item.name for item in self.symbols]
        logical = [item.logical_symbol for item in self.symbols]
        bar_names = [item.symbol for item in self.bars]
        tick_names = [item.symbol for item in self.ticks]
        paths = [item.path for item in (*self.bars, *self.ticks)]
        paths += [item.path for item in (self.news, self.reviews) if item is not None]
        if self.model is not None:
            paths += [self.model.artifact.path, self.model.learning_dataset.path]
            if self.model.selected_at > self.replay_from:
                raise ValueError("model selection cannot occur after replay starts")
        if len(set(names)) != len(names) or len(set(logical)) != len(logical):
            raise ValueError("unique native/logical historical contracts required")
        if set(bar_names) != set(names) or len(bar_names) != len(names):
            raise ValueError("exactly one M1 history per contract required")
        if self.quote_mode == "ticks" and (set(tick_names) != set(names) or len(tick_names) != len(names)):
            raise ValueError("exactly one tick history per contract required in tick mode")
        if self.quote_mode == "ohlc_conservative" and self.ticks:
            raise ValueError("OHLC mode cannot mix actual and modeled quotes")
        if len(paths) != len({path.casefold() for path in paths}) or any(
            item.symbol not in names for item in self.sessions
        ):
            raise ValueError("distinct file roles and known session symbols required")
        if not self.replay_from < self.replay_until:
            raise ValueError("positive replay period required")
        for name in names:
            intervals = [item for item in self.sessions if item.symbol == name]
            if not intervals or intervals != sorted(intervals, key=lambda item: item.start):
                raise ValueError("ordered session declarations required for every contract")
            if any(a.end > b.start for a, b in zip(intervals, intervals[1:], strict=False)):
                raise ValueError("overlapping declared sessions forbidden")
        return self


class BacktestOptions(FrozenContract):
    review_mode: Literal["veto", "archive", "synthetic_research"] = "veto"
    max_events: Annotated[int, Field(strict=True, ge=1, le=500000)] = 500000
    # Every chronological scheduler step is journaled; this is not native APScheduler or wall time.
    close_at_end: Annotated[bool, Field(strict=True)] = False
    simulate_orders: Annotated[bool, Field(strict=True)] = False
