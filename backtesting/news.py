"""Causal archived headline/calendar windows. Empty input means UNKNOWN, never safe."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from backtesting.contracts import FrozenContract, utc_time
from backtesting.dataset import DatasetError
from core.security import sha256_json
from news.exposure import ExposureMapper
from news.sentiment_analyzer import SentimentAnalyzer
from news.types import Headline
from trading.risk_types import NewsWindow


class ArchivedHeadline(FrozenContract):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    summary: Annotated[str, Field(max_length=1000)] = ""
    published_at: datetime
    first_seen_at: datetime
    currencies: tuple[Annotated[str, Field(pattern=r"^[A-Z]{3}$")], ...] = ()
    instruments: tuple[Annotated[str, Field(pattern=r"^[A-Z0-9]{2,10}$")], ...] = ()
    language: Literal["en", "unsupported"] = "en"
    impact: Literal["low", "medium", "high", "unknown"] = "unknown"

    @field_validator("published_at", "first_seen_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def chronology(self):
        if self.published_at > self.first_seen_at or len(self.currencies) > 30 or len(self.instruments) > 30:
            raise ValueError("invalid archived headline chronology/exposure")
        return self


class ArchivedEvent(FrozenContract):
    event_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
    known_at: datetime
    scheduled_at: datetime
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    impact: Literal["low", "medium", "high", "unknown"]
    # Unknown/tentative times block affected symbols throughout the archived coverage.
    precise_time: Annotated[bool, Field(strict=True)] = True

    @field_validator("known_at", "scheduled_at", mode="before")
    @classmethod
    def time(cls, value):
        return utc_time(value)


class ArchivedNewsSnapshot(FrozenContract):
    available_at: datetime
    headlines_fetched_at: datetime
    calendar_fetched_at: datetime
    covered_from: datetime
    covered_until: datetime
    complete: Annotated[bool, Field(strict=True)]
    symbols: Annotated[tuple[str, ...], Field(min_length=1, max_length=30)]
    headlines: Annotated[tuple[ArchivedHeadline, ...], Field(max_length=1000)] = ()
    events: Annotated[tuple[ArchivedEvent, ...], Field(max_length=1000)] = ()

    @field_validator(
        "available_at",
        "headlines_fetched_at",
        "calendar_fetched_at",
        "covered_from",
        "covered_until",
        mode="before",
    )
    @classmethod
    def time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def chronology(self):
        if max(self.headlines_fetched_at, self.calendar_fetched_at) > self.available_at:
            raise ValueError("future polling metadata forbidden")
        if not self.covered_from <= self.available_at <= self.covered_until:
            raise ValueError("invalid calendar coverage")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("unique news symbols required")
        if any(item.first_seen_at > self.available_at for item in self.headlines):
            raise ValueError("future headline in archived snapshot")
        if any(item.known_at > self.available_at for item in self.events):
            raise ValueError("future calendar revision in archived snapshot")
        if len({item.event_id for item in self.events}) != len(self.events):
            raise ValueError("duplicate calendar event in snapshot")
        return self


class NewsArchive(FrozenContract):
    format: Literal["reflex-replay-news-v1"]
    snapshots: Annotated[tuple[ArchivedNewsSnapshot, ...], Field(max_length=4096)]

    @model_validator(mode="after")
    def chronology(self):
        if any(
            b.available_at <= a.available_at for a, b in zip(self.snapshots, self.snapshots[1:], strict=False)
        ):
            raise ValueError("strictly ordered news snapshots required")
        return self


class ReplayNews:
    def __init__(self, document: dict | None, settings):
        self.settings = settings
        try:
            self.archive = (
                NewsArchive.model_validate(document)
                if document is not None
                else NewsArchive(format="reflex-replay-news-v1", snapshots=())
            )
        except Exception:
            raise DatasetError("invalid causal news archive") from None
        self.times = tuple(item.available_at for item in self.archive.snapshots)
        self.mapper, self.sentiment = ExposureMapper(settings), SentimentAnalyzer()
        self._hashes = tuple(sha256_json(item.model_dump(mode="json")) for item in self.archive.snapshots)

    def window(self, logical_symbol: str, now: datetime) -> NewsWindow:
        index = bisect_right(self.times, now) - 1
        if index < 0:
            return NewsWindow()
        snapshot = self.archive.snapshots[index]
        known = snapshot.complete and logical_symbol in snapshot.symbols
        safe = known
        for event in snapshot.events:
            if logical_symbol not in self.mapper.calendar_symbols(event.currency):
                continue
            if event.impact in {"high", "unknown"}:
                before = event.scheduled_at - timedelta(minutes=self.settings.news_pre_event_minutes)
                after = event.scheduled_at + timedelta(minutes=self.settings.news_post_event_minutes)
                if not event.precise_time or before <= now <= after:
                    safe = False
        for headline in snapshot.headlines:
            # Archived first-seen times are never refreshed. Old delayed news is not made current.
            age = (now - headline.first_seen_at).total_seconds()
            if not 0 <= age <= self.settings.news_headline_block_minutes * 60:
                continue
            analyzed = self.sentiment.analyze(headline.title, headline.summary, language=headline.language)
            item = Headline(
                "replay-local",
                "Archived source",
                headline.title,
                headline.summary,
                "https://example.invalid/archived",
                headline.published_at,
                headline.first_seen_at,
                headline.currencies,
                headline.instruments,
            )
            high = headline.impact in {"high", "unknown"} or analyzed.impact in {"high", "unknown"}
            if high and logical_symbol in self.mapper.affected(
                item, source_symbols=snapshot.symbols, unknown_high=True
            ):
                safe = False
        return NewsWindow(
            known=known,
            safe=safe,
            headlines_fetched_at=snapshot.headlines_fetched_at,
            calendar_fetched_at=snapshot.calendar_fetched_at,
            calendar_covered_until=snapshot.covered_until,
            calendar_covered_from=snapshot.covered_from,
            evidence_hash=sha256_json(
                {
                    "format": "reflex-replay-news-window-v1",
                    "symbol": logical_symbol,
                    "snapshot": self._hashes[index],
                    "policy": self.settings.strategy_fingerprint(),
                }
            ),
        )
