"""Immutable bounded provider DTOs, strict UTC chronology and sanitized content."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ai.json_validation import AIInvalidResponse, strict_json
from core.security import canonical_json, sanitize_text, sha256_json
from trading.types import BrokerError, aware_utc, valid_key

IMPACTS = {"low", "medium", "high", "unknown"}


class NewsUnavailable(BrokerError):
    def __init__(self, code="source_unavailable"):
        self.code = code
        super().__init__("News/calendar source unavailable; safe entry coverage withheld")


class NewsInvalid(BrokerError):
    def __init__(self, code="invalid_source"):
        self.code = code
        super().__init__("News/calendar source rejected by bounded contract validation")


def source_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.:-]{1,63}", value):
        raise NewsInvalid("source_identity")
    return value


def rss_source_id(url: str) -> str:
    return "rss-" + hashlib.sha256(url.encode()).hexdigest()[:16]


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value, *, limit=1000, secrets=(), required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > 16384:
        raise NewsInvalid("bounded_text")
    try:
        parser = _Plain()
        parser.feed(value)
        parser.close()
        text = unicodedata.normalize("NFKC", html.unescape("".join(parser.parts)))
        text = " ".join(text.split())
        text = sanitize_text(text, secrets)
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError
        text.encode("utf-8", errors="strict")
        if required and not text:
            raise ValueError
        return text[:limit]
    except (ValueError, UnicodeError):
        raise NewsInvalid("bounded_text") from None


def article_url(value, *, secrets=()):
    if not isinstance(value, str) or len(value) > 2048:
        raise NewsInvalid("article_url")
    p = urlparse(value)
    if (
        p.scheme not in {"http", "https"}
        or not p.hostname
        or p.username
        or p.password
        or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
    ):
        raise NewsInvalid("article_url")
    # Links are display-only; never follow them. Preserve only harmless story identity.
    query = urlencode(
        [(k, v) for k, v in parse_qsl(p.query) if k in {"id", "story", "article", "p"} and len(v) <= 128]
    )
    cleaned = urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", query, ""))
    if sanitize_text(cleaned, secrets) != cleaned:
        raise NewsInvalid("article_url_secret")
    return cleaned


def parse_time(value, *, rss=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise NewsInvalid("timestamp")
    try:
        result = parsedate_to_datetime(value) if rss else datetime.fromisoformat(value.replace("Z", "+00:00"))
        aware_utc(result)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, BrokerError, OverflowError):
        raise NewsInvalid("explicit_utc_timestamp") from None


def news_json(raw, settings, *, array=False):
    try:
        if isinstance(raw, str):
            raw = raw.encode()
        if len(raw) > settings.news_http_max_bytes:
            raise ValueError
        wrapped = b'{"items":' + raw + b"}" if array else raw
        result = strict_json(
            wrapped,
            max_bytes=settings.news_http_max_bytes + 32,
            max_depth=12,
            max_nodes=40000,
            max_string=16384,
            max_array=max(2000, settings.news_max_items_per_source),
        )
        if array and not isinstance(result["items"], list):
            raise ValueError
        return result["items"] if array else result
    except (AIInvalidResponse, ValueError, TypeError, KeyError):
        raise NewsInvalid("strict_json") from None


@dataclass(frozen=True, slots=True)
class Headline:
    source_id: str
    publisher: str
    title: str
    summary: str
    url: str
    published_at: datetime
    first_seen_at: datetime
    currencies: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()

    def __post_init__(self):
        source_id(self.source_id)
        for v in (self.published_at, self.first_seen_at):
            aware_utc(v)
        if self.published_at > self.first_seen_at:
            raise NewsInvalid("future_headline")
        for name, limit in (("publisher", 64), ("title", 300), ("summary", 1000), ("url", 2048)):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > limit or name != "summary" and not value:
                raise NewsInvalid("headline_shape")
            value.encode("utf-8", errors="strict")
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise NewsInvalid("headline_controls")
        article_url(self.url)
        for values in (self.currencies, self.instruments):
            if not isinstance(values, tuple) or len(values) > 30 or len(set(values)) != len(values):
                raise NewsInvalid("tags")
            if any(not isinstance(v, str) or not re.fullmatch(r"[A-Z0-9]{2,10}", v) for v in values):
                raise NewsInvalid("tags")

    @property
    def content_hash(self):
        # Same-title same-day syndication is one story. Evidence keeps every source.
        title = " ".join(re.sub(r"[^\w\s]", " ", self.title.casefold()).split())
        return sha256_json(
            {"format": "reflex-story-v1", "title": title, "day": self.published_at.date().isoformat()}
        )

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeadlineBatch:
    source_id: str
    fetched_at: datetime
    items: tuple[Headline, ...]
    fixture_only: bool = False
    complete: bool = True
    entry_eligible: bool = True
    etag: str | None = None
    last_modified: str | None = None
    fresh_as_of: datetime | None = None

    def __post_init__(self):
        source_id(self.source_id)
        aware_utc(self.fetched_at)
        if self.fresh_as_of is not None:
            aware_utc(self.fresh_as_of)
            if self.fresh_as_of > self.fetched_at:
                raise NewsInvalid("future_source_freshness")
            if any(isinstance(h, Headline) and h.published_at > self.fresh_as_of for h in self.items):
                raise NewsInvalid("headline_after_origin_cache_time")
        if (
            not isinstance(self.items, tuple)
            or len(self.items) > 2000
            or any(
                not isinstance(h, Headline)
                or h.source_id != self.source_id
                or h.first_seen_at > self.fetched_at
                for h in self.items
            )
            or any(type(v) is not bool for v in (self.fixture_only, self.complete, self.entry_eligible))
        ):
            raise NewsInvalid("batch_shape")
        for value in (self.etag, self.last_modified):
            if value is not None and (
                not isinstance(value, str)
                or len(value) > 256
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
            ):
                raise NewsInvalid("cache_validator")


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    event_id: str
    title: str
    currency: str
    starts_at: datetime
    ends_at: datetime
    impact: str
    tentative: bool = False

    def __post_init__(self):
        for v in (self.starts_at, self.ends_at):
            aware_utc(v)
        if (
            not isinstance(self.event_id, str)
            or not 1 <= len(self.event_id) <= 128
            or not isinstance(self.title, str)
            or not 1 <= len(self.title) <= 300
            or not isinstance(self.currency, str)
            or not re.fullmatch(r"[A-Z]{3}", self.currency)
            or self.impact not in IMPACTS
            or type(self.tentative) is not bool
            or self.ends_at < self.starts_at
            or (self.ends_at - self.starts_at).total_seconds() > 172800
        ):
            raise NewsInvalid("calendar_event")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.event_id + self.title):
            raise NewsInvalid("event_controls")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CalendarSnapshot:
    source_id: str
    produced_at: datetime
    fetched_at: datetime
    covered_from: datetime
    covered_until: datetime
    currencies: tuple[str, ...]
    events: tuple[EconomicEvent, ...]
    complete: bool
    origin: str
    fixture_only: bool = False
    source_digest: str | None = None

    def __post_init__(self):
        source_id(self.source_id)
        if self.source_digest is not None:
            valid_key(self.source_digest)
        for v in (self.produced_at, self.fetched_at, self.covered_from, self.covered_until):
            aware_utc(v)
        if (
            self.produced_at > self.fetched_at
            or self.covered_from >= self.covered_until
            or (self.covered_until - self.covered_from).total_seconds() > 86400 * 11
            or not isinstance(self.currencies, tuple)
            or not 1 <= len(self.currencies) <= 30
            or len(set(self.currencies)) != len(self.currencies)
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z]{3}", c) for c in self.currencies)
            or not isinstance(self.events, tuple)
            or len(self.events) > 2000
            or len({e.event_id for e in self.events}) != len(self.events)
            or any(
                not isinstance(e, EconomicEvent)
                or e.currency not in self.currencies
                or e.starts_at < self.covered_from
                or e.ends_at > self.covered_until
                for e in self.events
            )
            or type(self.complete) is not bool
            or type(self.fixture_only) is not bool
            or self.origin not in {"provider", "owner_reviewed", "fixture"}
        ):
            raise NewsInvalid("calendar_snapshot")
        if self.origin == "fixture" and not self.fixture_only:
            raise NewsInvalid("fixture_origin")

    def to_dict(self):
        return asdict(self)

    @property
    def digest(self):
        return sha256_json(self.to_dict())


def json_copy(value):
    import json

    return json.loads(canonical_json(value))
