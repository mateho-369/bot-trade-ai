"""Actual fixed-endpoint NewsAPI / Finnhub / CryptoPanic v2 read adapters.

Keys alone never enable HTTP. Paginated URLs are never followed; every page is a
bounded integer on the configured origin. Replies are source claims, not attestation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.security import secret_values
from core.settings import Settings
from news.http_client import NewsHTTP
from news.rss_parser import RSSAdapter
from news.types import (
    Headline,
    HeadlineBatch,
    NewsInvalid,
    NewsUnavailable,
    article_url,
    news_json,
    parse_time,
    plain_text,
)
from trading.types import Clock


def _items(value, settings):
    if not isinstance(value, list) or len(value) > settings.news_max_items_per_source:
        raise NewsInvalid("bounded_item_array")
    if any(not isinstance(item, dict) for item in value):
        raise NewsInvalid("item_object")
    return value


def _headline(source, publisher, title, summary, url, stamp, observed, settings, *, instruments=()):
    secrets = secret_values(settings)
    return Headline(
        source,
        plain_text(publisher, limit=64, secrets=secrets, required=True),
        plain_text(title, limit=300, secrets=secrets, required=True),
        plain_text(summary, limit=1000, secrets=secrets),
        article_url(url, secrets=secrets),
        stamp,
        observed,
        instruments=instruments,
    )


class NewsAPIAdapter:
    source_id = "newsapi"

    def __init__(self, settings: Settings, clock: Clock, http: NewsHTTP):
        self.settings, self.clock, self.http = settings, clock, http

    @property
    def configured(self):
        return self.settings.newsapi_enabled

    async def fetch(self):
        cfg = self.settings
        key = cfg.news_api_key.get_secret_value()
        if not self.configured or not key:
            raise NewsUnavailable("disabled_or_missing_newsapi_key")
        now = self.clock.now()
        items = []
        expected = None
        complete = False
        fresh = []
        for page in range(1, cfg.news_max_pages + 1):
            params = {
                "q": cfg.newsapi_query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": min(100, cfg.news_max_items_per_source),
                "page": page,
                "from": (
                    now - timedelta(hours=24 if cfg.newsapi_access_mode == "production" else 48)
                ).isoformat(),
                "to": now.isoformat(),
            }
            response = await self.http.get(
                "https://newsapi.org/v2/everything", params=params, headers={"X-Api-Key": key}
            )
            observed = self.clock.now()
            fresh.append(observed - timedelta(seconds=response.age_seconds))
            data = news_json(response.raw, cfg)
            if (
                data.get("status") != "ok"
                or type(data.get("totalResults")) is not int
                or not 0 <= data["totalResults"] <= 1000000
            ):
                raise NewsInvalid("newsapi_envelope")
            if expected is None:
                expected = data["totalResults"]
            if expected != data["totalResults"]:
                raise NewsInvalid("newsapi_changing_pagination")
            rows = _items(data.get("articles"), cfg)
            for row in rows:
                if row.get("title") == "[Removed]":
                    raise NewsInvalid("newsapi_removed_item")
                publisher = row.get("source", {})
                if not isinstance(publisher, dict):
                    raise NewsInvalid("publisher_object")
                items.append(
                    _headline(
                        self.source_id,
                        publisher.get("name") or self.source_id,
                        row.get("title"),
                        row.get("description"),
                        row.get("url"),
                        parse_time(row.get("publishedAt")),
                        observed,
                        cfg,
                    )
                )
            if len(items) > cfg.news_max_items_per_source:
                raise NewsInvalid("aggregate_source_bound")
            if len(items) >= expected:
                complete = len(items) == expected and len({(h.url, h.published_at) for h in items}) == len(
                    items
                )
                break
            if not rows:
                break
        return HeadlineBatch(
            self.source_id,
            self.clock.now(),
            tuple(items),
            self.http.fixture_only,
            complete,
            cfg.newsapi_access_mode == "production",
            fresh_as_of=min(fresh),
        )


class FinnhubAdapter:
    def __init__(self, category: str, settings: Settings, clock: Clock, http: NewsHTTP):
        if category not in {"general", "forex", "crypto"}:
            raise NewsInvalid("finnhub_category")
        self.category, self.settings, self.clock, self.http = category, settings, clock, http
        self.source_id = "finnhub:" + category

    @property
    def configured(self):
        return self.settings.finnhub_news_enabled

    async def fetch(self):
        cfg = self.settings
        key = cfg.finnhub_api_key.get_secret_value()
        if not self.configured or not key:
            raise NewsUnavailable("disabled_or_missing_finnhub_key")
        response = await self.http.get(
            "https://finnhub.io/api/v1/news",
            params={"category": self.category, "minId": 0},
            headers={"X-Finnhub-Token": key},
        )
        observed = self.clock.now()
        items = []
        for row in _items(news_json(response.raw, cfg, array=True), cfg):
            stamp = row.get("datetime")
            if type(stamp) is not int or not 0 < stamp < 4102444800:
                raise NewsInvalid("finnhub_epoch")
            items.append(
                _headline(
                    self.source_id,
                    row.get("source") or self.source_id,
                    row.get("headline"),
                    row.get("summary"),
                    row.get("url"),
                    datetime.fromtimestamp(stamp, timezone.utc),
                    observed,
                    cfg,
                )
            )
        # Rolling news endpoints do not attest exhaustive global coverage. Owner scopes stay mandatory.
        return HeadlineBatch(
            self.source_id,
            observed,
            tuple(items),
            self.http.fixture_only,
            True,
            True,
            fresh_as_of=observed - timedelta(seconds=response.age_seconds),
        )


class CryptoPanicAdapter:
    source_id = "cryptopanic"

    def __init__(self, settings: Settings, clock: Clock, http: NewsHTTP):
        self.settings, self.clock, self.http = settings, clock, http

    @property
    def configured(self):
        return self.settings.cryptopanic_enabled

    async def fetch(self):
        cfg = self.settings
        key = cfg.cryptopanic_api_key.get_secret_value()
        if not self.configured or not key:
            raise NewsUnavailable("disabled_or_missing_cryptopanic_key")
        items = []
        complete = False
        fresh = []
        endpoint = f"https://cryptopanic.com/api/{cfg.cryptopanic_plan}/v2/posts/"
        for page in range(1, cfg.news_max_pages + 1):
            response = await self.http.get(
                endpoint,
                params={
                    "auth_token": key,
                    "public": "true",
                    "currencies": ",".join(cfg.cryptopanic_currencies),
                    "regions": "en",
                    "kind": "news",
                    "page": page,
                },
            )
            observed = self.clock.now()
            fresh.append(observed - timedelta(seconds=response.age_seconds))
            data = news_json(response.raw, cfg)
            if not {"results", "next", "previous"}.issubset(data):
                raise NewsInvalid("cryptopanic_envelope")
            rows = _items(data["results"], cfg)
            if data["next"] is not None and (
                not isinstance(data["next"], str) or not 1 <= len(data["next"]) <= 4096
            ):
                raise NewsInvalid("cryptopanic_pagination")
            for row in rows:
                instruments = row.get("instruments", [])
                if (
                    not isinstance(instruments, list)
                    or len(instruments) > 30
                    or any(not isinstance(i, dict) for i in instruments)
                ):
                    raise NewsInvalid("cryptopanic_instruments")
                tags = tuple(sorted({i["code"] for i in instruments}))
                publisher = row.get("source", {})
                if not isinstance(publisher, dict):
                    raise NewsInvalid("cryptopanic_source")
                items.append(
                    _headline(
                        self.source_id,
                        publisher.get("title") or self.source_id,
                        row.get("title"),
                        row.get("description"),
                        row.get("original_url") or row.get("url"),
                        parse_time(row.get("published_at")),
                        observed,
                        cfg,
                        instruments=tags,
                    )
                )
            if len(items) > cfg.news_max_items_per_source:
                raise NewsInvalid("aggregate_source_bound")
            if data["next"] is None:
                complete = len({(h.url, h.published_at) for h in items}) == len(items)
                break
            # Never follow next, even on the same host: it may echo auth or point elsewhere.
            if not rows:
                break
        return HeadlineBatch(
            self.source_id,
            self.clock.now(),
            tuple(items),
            self.http.fixture_only,
            complete,
            cfg.cryptopanic_plan in {"growth", "enterprise"},
            fresh_as_of=min(fresh),
        )


def adapters(settings: Settings, clock: Clock, http: NewsHTTP):
    result = []
    if settings.use_free_news_sources and settings.use_rss:
        result.extend(RSSAdapter(url, settings, clock, http) for url in settings.rss_urls)
    if settings.newsapi_enabled:
        result.append(NewsAPIAdapter(settings, clock, http))
    if settings.finnhub_news_enabled:
        result.extend(FinnhubAdapter(c, settings, clock, http) for c in settings.finnhub_news_categories)
    if settings.cryptopanic_enabled:
        result.append(CryptoPanicAdapter(settings, clock, http))
    if len(result) > 16 or len({p.source_id for p in result}) != len(result):
        raise NewsInvalid("bounded_unique_sources")
    return tuple(result)
