"""Replay exact bound archived reviews, or explicitly artificial research reviews. No HTTP/LLM call."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from backtesting.contracts import FrozenContract, Hash, decimal_text, utc_time
from backtesting.dataset import DatasetError
from core.security import sha256_json
from strategy.base_strategy import AIEntryReview
from trading.risk_types import PositionReview
from trading.types import SourceKind, TradingDisabled


class ArchivedEntryReview(FrozenContract):
    available_at: datetime
    observed_at: datetime
    proposal_hash: Hash
    news_hash: Hash
    code_hash: Hash
    model_sha256: Hash
    decision: Literal["approve", "reject", "wait"]
    confidence: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    risk_percent: Annotated[Decimal | None, Field(gt=0, le=1)] = None
    original_provider: Literal["ollama", "openai"]
    provider_model: Annotated[str, Field(pattern=r"^[A-Za-z0-9_./:@+-]{1,128}$")]
    request_hash: Hash

    @field_validator("available_at", "observed_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def actual_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("actual numeric confidence required")
        return value

    @field_validator("risk_percent", mode="before", json_schema_input_type=str | None)
    @classmethod
    def exact_reduction(cls, value):
        return None if value is None else decimal_text(value)

    @model_validator(mode="after")
    def chronology(self):
        if self.observed_at > self.available_at:
            raise ValueError("future review timestamp forbidden")
        return self


class ArchivedPositionReview(FrozenContract):
    available_at: datetime
    observed_at: datetime
    logical_symbol: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.#-]{1,64}$")]
    position_identifier: Annotated[int, Field(strict=True, gt=0)]
    position_hash: Hash
    code_hash: Hash
    model_sha256: Hash
    news_hash: Hash
    confidence: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    momentum_continues: Annotated[bool, Field(strict=True)]
    volatility_safe: Annotated[bool, Field(strict=True)]
    original_provider: Literal["ollama", "openai"]

    @field_validator("available_at", "observed_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def actual_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("actual numeric confidence required")
        return value

    @model_validator(mode="after")
    def chronology(self):
        if self.observed_at > self.available_at:
            raise ValueError("future position review forbidden")
        return self


class ReviewArchive(FrozenContract):
    format: Literal["reflex-replay-reviews-v1"]
    description: Annotated[str, Field(min_length=1, max_length=1024)]
    entries: Annotated[tuple[ArchivedEntryReview, ...], Field(max_length=20000)]
    position_entries: Annotated[tuple[ArchivedPositionReview, ...], Field(max_length=20000)] = ()

    @model_validator(mode="after")
    def unique(self):
        if len({item.proposal_hash for item in self.entries}) != len(self.entries):
            raise ValueError("duplicate proposal reviews forbidden")
        if any(b.available_at < a.available_at for a, b in zip(self.entries, self.entries[1:], strict=False)):
            raise ValueError("chronological review archive required")
        if any(
            b.available_at < a.available_at
            for a, b in zip(self.position_entries, self.position_entries[1:], strict=False)
        ):
            raise ValueError("chronological position-review archive required")
        keys = [(item.position_identifier, item.available_at) for item in self.position_entries]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate position review forbidden")
        return self


class ReplayReviewer:
    def __init__(self, document, *, mode, dataset, profile, clock):
        if mode not in {"veto", "archive", "synthetic_research"}:
            raise TradingDisabled("unknown offline review mode")
        if mode == "synthetic_research" and dataset.manifest.origin.kind != "synthetic_fixture":
            raise TradingDisabled("artificial reviews are restricted to declared synthetic fixture datasets")
        if profile.data_source != SourceKind.HISTORICAL:
            raise TradingDisabled("replay reviews require historical BACKTEST scope")
        self.mode, self.clock, self.profile = mode, clock, profile
        try:
            self.archive = ReviewArchive.model_validate(document) if document is not None else None
        except Exception:
            raise DatasetError("invalid causal entry-review archive") from None
        self.times = (
            tuple(item.available_at for item in (*self.archive.entries, *self.archive.position_entries))
            if self.archive
            else ()
        )
        self._entries = {item.proposal_hash: item for item in self.archive.entries} if self.archive else {}
        self.matches = 0
        self.artificial_reviews = 0

    async def review(self, proposal, news):
        if self.mode == "veto" or not proposal.proposal_hash or not news.evidence_hash:
            return None
        if self.mode == "synthetic_research":
            self.artificial_reviews += 1
            return AIEntryReview(
                self.clock.now(),
                SourceKind.HISTORICAL,
                proposal.proposal_hash,
                self.profile.code_hash,
                self.profile.model_sha256,
                news.evidence_hash,
                "approve",
                90.0,
                "replay",
                request_hash=sha256_json({"synthetic_research_only": proposal.proposal_hash}),
                provider_model="artificial-fixture-not-ai",
            )
        entry = self._entries.get(proposal.proposal_hash)
        if entry is None or entry.available_at > self.clock.now() or entry.observed_at > self.clock.now():
            return None
        # Rebind NOTHING. Changed code/model/history/news must veto in the ordinary SignalStore.
        self.matches += 1
        return AIEntryReview(
            entry.observed_at,
            SourceKind.HISTORICAL,
            entry.proposal_hash,
            entry.code_hash,
            entry.model_sha256,
            entry.news_hash,
            entry.decision,
            entry.confidence,
            "replay",
            entry.risk_percent,
            entry.request_hash,
            entry.provider_model,
        )

    def position_reviews(self, news, settings):
        if self.mode != "archive" or self.archive is None or not settings.allow_tp_extension:
            return {}
        now, found = self.clock.now(), {}
        for item in self.archive.position_entries:
            if item.available_at > now:
                break
            if not 0 <= (now - item.observed_at).total_seconds() <= settings.order_max_age_seconds:
                continue
            window = news.window(item.logical_symbol, now)
            if window.evidence_hash != item.news_hash or not window.allows(
                settings, now, item.logical_symbol
            ):
                continue
            if item.code_hash != self.profile.code_hash or item.model_sha256 != self.profile.model_sha256:
                continue
            found[item.position_identifier] = PositionReview(
                item.observed_at,
                SourceKind.HISTORICAL,
                item.confidence,
                item.momentum_continues,
                item.volatility_safe,
                window,
                item.position_identifier,
                item.position_hash,
                item.code_hash,
                item.model_sha256,
            )
        return found
