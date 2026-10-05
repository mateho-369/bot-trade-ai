"""EXPLICIT synthetic local HTTP fixtures; never genuine news, coverage or stage evidence."""

from __future__ import annotations

import html
from datetime import timedelta
from email.utils import format_datetime

import httpx

from core.security import canonical_json
from news.types import rss_source_id

RSS_URL = "https://news-fixture.example/feed.xml"
CALENDAR_URL = "https://news-fixture.example/calendar.json"


def news_fixture_settings(symbols=("EURUSD", "GBPUSD")):
    return {
        "rss_urls": (RSS_URL,),
        "calendar_source_url": CALENDAR_URL,
        "news_request_spacing_seconds": 0.2,
        "news_source_coverage": {
            rss_source_id(RSS_URL): {
                "symbols": tuple(symbols),
                "currencies": ("USD", "EUR", "GBP"),
                "reviewed": True,
                "realtime": True,
                "poll_seconds": 30,
                "max_publication_lag_seconds": 3600,
            }
        },
    }


def calendar_document(now, *, events=None, origin="fixture", produced_at=None):
    if events is None:
        at = now + timedelta(hours=2)
        events = [
            {
                "id": "synthetic-usd-event",
                "title": "SYNTHETIC USD release",
                "currency": "USD",
                "starts_at": at.isoformat(),
                "ends_at": at.isoformat(),
                "impact": "high",
                "tentative": False,
            }
        ]
    return {
        "format": "reflex-calendar-v1",
        "source": "synthetic-fixture",
        "origin": origin,
        "produced_at": (produced_at or now).isoformat(),
        "covered_from": (now - timedelta(hours=24)).isoformat(),
        "covered_until": (now + timedelta(hours=48)).isoformat(),
        "currencies": ["USD", "EUR", "GBP"],
        "complete": True,
        "events": events,
    }


def rss_document(
    now,
    *,
    title="Synthetic Euro and dollar market commentary",
    summary="Local fixture, not real news.",
    published_at=None,
    url="https://article-fixture.example/story",
):
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>LOCAL TEST NEWS</title>'
        f"<link>{RSS_URL}</link>"
        f"<description>Scripted fixture.</description><item><title>{html.escape(title)}</title>"
        f"<description>{html.escape(summary)}</description><link>{html.escape(url)}</link>"
        f"<pubDate>{format_datetime(published_at or now - timedelta(minutes=5))}</pubDate>"
        "</item></channel></rss>"
    ).encode()


class ScriptedNewsHTTP:
    def __init__(self, clock):
        self.clock = clock
        self.title = "Synthetic Euro and dollar market commentary"
        self.summary = "Fixture only."
        self.rss_status = 200
        self.calendar_status = 200
        self.not_modified = False
        self.calls = []
        self.block = None
        self.events = None
        self.produced_at = None
        self.published_at = None
        self.rss_body = None
        self.calendar_body = None
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.calls.append(request)
        if self.block:
            await self.block.wait()
        if str(request.url).split("?")[0] == RSS_URL:
            if self.not_modified and request.headers.get("if-none-match"):
                return httpx.Response(304, headers={"etag": "fixture-etag"})
            return httpx.Response(
                self.rss_status,
                content=self.rss_body
                if self.rss_body is not None
                else rss_document(
                    self.clock.now(), title=self.title, summary=self.summary, published_at=self.published_at
                ),
                headers={"content-type": "application/rss+xml", "etag": "fixture-etag"},
            )
        if str(request.url).split("?")[0] == CALENDAR_URL:
            body = (
                self.calendar_body
                if self.calendar_body is not None
                else canonical_json(
                    calendar_document(self.clock.now(), events=self.events, produced_at=self.produced_at)
                ).encode()
            )
            return httpx.Response(
                self.calendar_status, content=body, headers={"content-type": "application/json"}
            )
        raise AssertionError("Fixture refuses unexpected origin/path")
