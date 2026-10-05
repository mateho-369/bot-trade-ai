"""Entitled complete-range calendar snapshots, never headline/HTML-calendar substitutes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.security import secret_values, sha256_json
from core.settings import Settings
from news.http_client import NewsHTTP
from news.types import (
    CalendarSnapshot,
    EconomicEvent,
    NewsInvalid,
    NewsUnavailable,
    news_json,
    parse_time,
    plain_text,
)
from trading.types import BrokerError, Clock, aware_utc

COUNTRY_CURRENCY = {
    "US": "USD",
    "USA": "USD",
    "EU": "EUR",
    "EA": "EUR",
    "EZ": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "PT": "EUR",
    "GR": "EUR",
    "IE": "EUR",
    "NL": "EUR",
    "BE": "EUR",
    "AT": "EUR",
    "FI": "EUR",
    "GB": "GBP",
    "UK": "GBP",
    "JP": "JPY",
    "AU": "AUD",
    "CA": "CAD",
    "CH": "CHF",
    "NZ": "NZD",
    "CN": "CNY",
    "HK": "HKD",
    "SE": "SEK",
    "NO": "NOK",
    "MX": "MXN",
    "SG": "SGD",
    "IN": "INR",
    "BR": "BRL",
    "ZA": "ZAR",
    "KR": "KRW",
    "TR": "TRY",
}


def confined_read(settings: Settings, path: Path):
    root = settings.project_root.resolve()
    candidate = root / path
    try:
        resolved = settings.resolve_path(path)
        for p in (candidate, *candidate.parents):
            if p == root.parent:
                break
            if p.is_symlink():
                raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(resolved, flags)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or not 1 <= meta.st_size <= settings.news_http_max_bytes:
                raise ValueError
            raw = stream.read(settings.news_http_max_bytes + 1)
            if len(raw) > settings.news_http_max_bytes:
                raise ValueError
            return raw
    except (OSError, ValueError):
        raise NewsUnavailable("missing_unsafe_or_oversized_calendar_file") from None


def parse_calendar(
    raw: bytes, *, settings: Settings, fetched_at: datetime, source_id: str, fixture_only=False
):
    data = news_json(raw, settings)
    try:
        if set(data) != {
            "format",
            "source",
            "origin",
            "produced_at",
            "covered_from",
            "covered_until",
            "currencies",
            "complete",
            "events",
        }:
            raise ValueError
        if (
            data["format"] != "reflex-calendar-v1"
            or not isinstance(data["source"], str)
            or not 1 <= len(data["source"]) <= 64
        ):
            raise ValueError
        currencies = data["currencies"]
        if (
            not isinstance(currencies, list)
            or not isinstance(data["events"], list)
            or len(data["events"]) > 2000
        ):
            raise ValueError
        events = []
        secrets = secret_values(settings)
        for row in data["events"]:
            if not isinstance(row, dict) or set(row) != {
                "id",
                "title",
                "currency",
                "starts_at",
                "ends_at",
                "impact",
                "tentative",
            }:
                raise ValueError
            events.append(
                EconomicEvent(
                    row["id"],
                    plain_text(row["title"], limit=300, secrets=secrets, required=True),
                    row["currency"],
                    parse_time(row["starts_at"]),
                    parse_time(row["ends_at"]),
                    row["impact"],
                    row["tentative"],
                )
            )
        result = CalendarSnapshot(
            source_id,
            parse_time(data["produced_at"]),
            fetched_at,
            parse_time(data["covered_from"]),
            parse_time(data["covered_until"]),
            tuple(currencies),
            tuple(events),
            data["complete"],
            data["origin"],
            fixture_only or data["origin"] == "fixture",
        )
        if not result.events and not settings.calendar_allow_empty_reviewed:
            raise NewsInvalid("empty_calendar_not_reviewed")
        return result
    except (ValueError, TypeError, KeyError, AttributeError, BrokerError):
        raise NewsInvalid("calendar_contract") from None


class FileCalendar:
    source_id = "calendar:file"

    def __init__(self, settings: Settings, clock: Clock):
        self.settings, self.clock = settings, clock

    async def fetch(self):
        if not self.settings.use_economic_calendar or not self.settings.calendar_file_reviewed:
            raise NewsUnavailable("calendar_file_not_reviewed")
        raw = await asyncio.to_thread(confined_read, self.settings, self.settings.calendar_file)
        snapshot = await asyncio.to_thread(
            parse_calendar,
            raw,
            settings=self.settings,
            fetched_at=self.clock.now(),
            source_id=self.source_id,
        )
        return replace(snapshot, source_digest=hashlib.sha256(raw).hexdigest())


class JSONCalendar:
    source_id = "calendar:json_http"

    def __init__(self, settings: Settings, clock: Clock, http: NewsHTTP):
        self.settings, self.clock, self.http = settings, clock, http

    async def fetch(self):
        if not self.settings.use_economic_calendar or not self.settings.calendar_source_url:
            raise NewsUnavailable("calendar_disabled_or_url_missing")
        response = await self.http.get(self.settings.calendar_source_url)
        snapshot = await asyncio.to_thread(
            parse_calendar,
            response.raw,
            settings=self.settings,
            fetched_at=self.clock.now(),
            source_id=self.source_id,
            fixture_only=self.http.fixture_only,
        )
        if snapshot.produced_at > snapshot.fetched_at - timedelta(seconds=response.age_seconds):
            raise NewsInvalid("calendar_http_age_inconsistent")
        return replace(snapshot, source_digest=hashlib.sha256(response.raw).hexdigest())


def explicit_provider_time(value: str, zone: ZoneInfo):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return aware_utc(parsed)
        a, b = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
        if (
            a.utcoffset() != b.utcoffset()
            or a.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != parsed
        ):
            raise ValueError
        return aware_utc(a)
    except (ValueError, TypeError, BrokerError):
        raise NewsInvalid("ambiguous_or_invalid_calendar_time") from None


class FinnhubCalendar:
    source_id = "calendar:finnhub"

    def __init__(self, settings: Settings, clock: Clock, http: NewsHTTP):
        self.settings, self.clock, self.http = settings, clock, http

    async def fetch(self):
        cfg = self.settings
        key = cfg.finnhub_api_key.get_secret_value()
        if not cfg.use_economic_calendar or not key or not cfg.finnhub_calendar_scope_reviewed:
            raise NewsUnavailable("finnhub_calendar_entitlement_scope_not_reviewed")
        now = self.clock.now()
        zone = ZoneInfo(cfg.finnhub_calendar_timezone)
        start = (now - timedelta(hours=cfg.calendar_lookback_hours)).astimezone(zone).date()
        finish = (now + timedelta(hours=cfg.calendar_lookahead_hours)).astimezone(zone).date()
        left = explicit_provider_time(start.isoformat() + " 00:00:00", zone)
        right = explicit_provider_time((finish + timedelta(days=1)).isoformat() + " 00:00:00", zone)
        response = await self.http.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": start.isoformat(), "to": finish.isoformat()},
            headers={"X-Finnhub-Token": key},
        )
        fetched = self.clock.now()
        data = news_json(response.raw, cfg)
        rows = data.get("economicCalendar")
        if not isinstance(rows, list) or len(rows) > 2000:
            raise NewsInvalid("finnhub_calendar_envelope")
        events = []
        currencies = tuple(
            sorted({c for name in cfg.symbols for c in cfg.symbol_news_currencies.get(name, ())})
        )
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                country = row.get("country")
                currency = COUNTRY_CURRENCY.get(country)
                if currency is None:
                    raise ValueError  # Do not quietly drop an unmapped market.
                if currency not in currencies:
                    continue
                stamp = explicit_provider_time(row["time"], zone)
                title = plain_text(row["event"], limit=300, secrets=secret_values(cfg), required=True)
                impact = row["impact"]
                if not isinstance(impact, str) or impact.lower() not in {"low", "medium", "high", "unknown"}:
                    raise ValueError
                identity = sha256_json({"country": country, "title": title, "at": stamp})
                events.append(EconomicEvent(identity, title, currency, stamp, stamp, impact.lower()))
            snapshot = CalendarSnapshot(
                self.source_id,
                fetched - timedelta(seconds=response.age_seconds),
                fetched,
                left,
                right,
                currencies,
                tuple(events),
                True,
                "provider",
                self.http.fixture_only,
            )
            if not snapshot.events and not cfg.calendar_allow_empty_reviewed:
                raise ValueError
            return snapshot
        except (KeyError, TypeError, ValueError, BrokerError):
            raise NewsInvalid("finnhub_calendar_contract") from None


class DisabledCalendar:
    source_id = "calendar:disabled"

    async def fetch(self):
        raise NewsUnavailable("calendar_disabled")


def calendar_adapter(settings: Settings, clock: Clock, http: NewsHTTP):
    mode = settings.calendar_provider
    if mode == "auto":
        mode = "json_http" if settings.calendar_source_url else "file"
    if not settings.use_economic_calendar or mode == "disabled":
        return DisabledCalendar()
    if mode == "file":
        return FileCalendar(settings, clock)
    if mode == "json_http":
        return JSONCalendar(settings, clock, http)
    return FinnhubCalendar(settings, clock, http)
