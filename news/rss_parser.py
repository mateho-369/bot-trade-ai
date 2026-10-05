"""RSS 2 / RSS 1 / Atom with defused XML preflight; no missing-date/bozo safe feeds."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import feedparser
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from core.security import secret_values
from core.settings import Settings
from news.http_client import NewsHTTP
from news.types import (
    Headline,
    HeadlineBatch,
    NewsInvalid,
    article_url,
    parse_time,
    plain_text,
    rss_source_id,
)
from trading.types import Clock


def parse_rss(raw: bytes, *, url: str, settings: Settings, observed_at, source=None):
    identifier = source or rss_source_id(url)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= settings.news_http_max_bytes:
        raise NewsInvalid("rss_bytes")
    try:
        root = ElementTree.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        count = 0

        def walk(node, depth=0):
            nonlocal count
            count += 1
            if depth > 20 or count > 20000:
                raise ValueError
            if node.text and len(node.text) > 16384 or node.tail and len(node.tail) > 16384:
                raise ValueError
            for child in node:
                walk(child, depth + 1)

        walk(root)
        if root.tag.rsplit("}", 1)[-1].lower() not in {"rss", "rdf", "feed"}:
            raise ValueError
        parsed = feedparser.parse(raw)
        if parsed.get("bozo") or len(parsed.entries) > settings.news_max_items_per_source:
            raise ValueError
        secrets = secret_values(settings)
        items = []
        for entry in parsed.entries:
            raw_date = entry.get("published")
            # Atom updated alone is modification time, not a trusted original publication.
            if not raw_date:
                raise ValueError
            stamp = parse_time(raw_date, rss="," in raw_date or raw_date.split(" ")[0].isdigit())
            items.append(
                Headline(
                    identifier,
                    plain_text(
                        parsed.feed.get("title", identifier), limit=64, secrets=secrets, required=True
                    ),
                    plain_text(entry.get("title"), limit=300, secrets=secrets, required=True),
                    plain_text(entry.get("summary", ""), limit=1000, secrets=secrets),
                    article_url(entry.get("link"), secrets=secrets),
                    stamp,
                    observed_at,
                )
            )
        return tuple(items)
    except (
        DefusedXmlException,
        ElementTree.ParseError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
    ):
        raise NewsInvalid("rss_xml_or_entries") from None


class RSSAdapter:
    def __init__(self, url: str, settings: Settings, clock: Clock, http: NewsHTTP):
        self.url, self.settings, self.clock, self.http = url, settings, clock, http
        self.source_id = rss_source_id(url)
        self._previous = None

    @property
    def configured(self):
        return self.settings.use_rss and self.settings.use_free_news_sources

    async def fetch(self):
        from news.types import NewsUnavailable

        if not self.configured:
            raise NewsUnavailable("disabled_rss")
        headers = {}
        if self._previous:
            if self._previous.etag:
                headers["If-None-Match"] = self._previous.etag
            if self._previous.last_modified:
                headers["If-Modified-Since"] = self._previous.last_modified
        result = await self.http.get(self.url, headers=headers, xml=True, conditional=bool(headers))
        now = self.clock.now()
        fresh = now - timedelta(seconds=result.age_seconds)
        if result.status == 304:
            if self._previous is None:
                raise NewsInvalid("missing_304_cache")
            batch = replace(self._previous, fetched_at=now, fresh_as_of=fresh)
        else:
            items = await asyncio.to_thread(
                parse_rss, result.raw, url=self.url, settings=self.settings, observed_at=now
            )
            batch = HeadlineBatch(
                self.source_id,
                now,
                items,
                self.http.fixture_only,
                True,
                True,
                result.etag,
                result.last_modified,
                fresh,
            )
        self._previous = batch
        return batch
