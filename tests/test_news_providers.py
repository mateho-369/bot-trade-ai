"""Actual adapter parsing/request contracts exercised with local fixtures, not entitled APIs."""

from datetime import timedelta

import httpx
import pytest

from core.security import canonical_json
from core.settings import Settings
from news.http_client import NewsHTTP
from news.providers import CryptoPanicAdapter, FinnhubAdapter, NewsAPIAdapter
from news.rss_parser import RSSAdapter
from news.types import NewsInvalid, NewsUnavailable
from scripts.synthetic_news_fixtures import RSS_URL, ScriptedNewsHTTP
from scripts.synthetic_signal_market import ANCHOR
from trading.types import ManualClock

KEY = "FAKE_NEWS_CREDENTIAL_NEVER_REAL"


def make(provider, handler, **changes):
    data = {
        "news_api_key": KEY,
        "finnhub_api_key": KEY,
        "cryptopanic_api_key": KEY,
        "newsapi_enabled": True,
        "finnhub_news_enabled": True,
        "cryptopanic_enabled": True,
        "news_request_spacing_seconds": 0.2,
    }
    data.update(changes)
    cfg = Settings(_env_file=None, **data)
    clock = ManualClock(ANCHOR)
    http = NewsHTTP(cfg, transport=httpx.MockTransport(handler))
    cls = {"newsapi": NewsAPIAdapter, "cryptopanic": CryptoPanicAdapter}.get(provider)
    adapter = cls(cfg, clock, http) if cls else FinnhubAdapter("forex", cfg, clock, http)
    return adapter, http


def article():
    return {
        "source": {"name": "TEST"},
        "title": "EUR dollar market update",
        "description": "Strong but quiet",
        "url": "https://article.example/story?auth_token=FAKE&tracking=x",
        "publishedAt": (ANCHOR - timedelta(minutes=5)).isoformat(),
    }


def crypto_item():
    return {
        "id": 1,
        "title": "Bitcoin market update",
        "description": "Local fixture.",
        "published_at": (ANCHOR - timedelta(minutes=5)).isoformat(),
        "source": {"title": "TEST"},
        "original_url": "https://article.example/crypto",
        "instruments": [{"code": "BTC"}],
    }


async def test_newsapi_header_auth_developer_is_diagnostic_and_original_timestamps_preserved():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"status": "ok", "totalResults": 1, "articles": [article()]})

    adapter, http = make("newsapi", handler)
    try:
        result = await adapter.fetch()
        assert calls[0].headers["x-api-key"] == KEY and KEY not in str(calls[0].url)
        assert result.fixture_only and not result.entry_eligible and result.complete
        assert result.items[0].published_at == ANCHOR - timedelta(minutes=5)
        assert "auth_token" not in result.items[0].url
    finally:
        await http.close()


async def test_newsapi_integer_fixed_origin_pagination_and_incomplete_caps():
    calls = []

    def handler(req):
        calls.append(req)
        row = article()
        row["url"] = f"https://article.example/{req.url.params['page']}"
        return httpx.Response(200, json={"status": "ok", "totalResults": 2, "articles": [row]})

    adapter, http = make("newsapi", handler, newsapi_access_mode="production")
    try:
        result = await adapter.fetch()
        assert (
            result.complete and result.entry_eligible and [q.url.params["page"] for q in calls] == ["1", "2"]
        )
        assert all(q.url.host == "newsapi.org" for q in calls)
    finally:
        await http.close()
    adapter, http = make("newsapi", handler, news_max_pages=1)
    try:
        assert not (await adapter.fetch()).complete
    finally:
        await http.close()


@pytest.mark.parametrize(
    "body",
    [
        {"status": "error", "code": "RAW_PRIVATE_ERROR"},
        {"status": "ok", "totalResults": True, "articles": []},
        {"status": "ok", "totalResults": 1, "articles": [{"title": "[Removed]"}]},
        {"status": "ok", "totalResults": 1, "articles": [{**article(), "publishedAt": None}]},
        {
            "status": "ok",
            "totalResults": 1,
            "articles": [{**article(), "publishedAt": "2026-10-03T11:55:00"}],
        },
        {
            "status": "ok",
            "totalResults": 1,
            "articles": [{**article(), "publishedAt": "2026-10-04T11:55:00Z"}],
        },
    ],
)
async def test_newsapi_error_envelopes_and_unknown_dates_fail_whole_batch(body):
    adapter, http = make("newsapi", lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(NewsInvalid):
            await adapter.fetch()
    finally:
        await http.close()


async def test_finnhub_header_auth_epoch_datetime_and_empty_not_safe_batch():
    calls = []
    row = {
        "id": 1,
        "datetime": int((ANCHOR - timedelta(minutes=5)).timestamp()),
        "headline": "USD rate update",
        "summary": "TEST",
        "source": "TEST",
        "url": "https://article.example/fx",
    }

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=[row])

    adapter, http = make("finnhub", handler)
    try:
        batch = await adapter.fetch()
        assert calls[0].headers["x-finnhub-token"] == KEY and KEY not in str(calls[0].url)
        assert calls[0].url.params["category"] == "forex" and batch.items[
            0
        ].published_at == ANCHOR - timedelta(minutes=5)
    finally:
        await http.close()
    adapter, http = make("finnhub", lambda _: httpx.Response(200, json=[]))
    try:
        assert not (await adapter.fetch()).items
    finally:
        await http.close()


@pytest.mark.parametrize("value", [True, "1791028800", 0, -1, 5000000000])
async def test_finnhub_non_integer_or_out_of_contract_epoch(value):
    adapter, http = make("finnhub", lambda _: httpx.Response(200, json=[{"datetime": value}]))
    try:
        with pytest.raises(NewsInvalid):
            await adapter.fetch()
    finally:
        await http.close()


async def test_cryptopanic_current_v2_instruments_and_ignores_untrusted_next_urls():
    calls = []

    def handler(req):
        calls.append(req)
        second = req.url.params["page"] == "2"
        row = crypto_item()
        row["original_url"] += "/2" if second else "/1"
        return httpx.Response(
            200,
            json={
                "next": None if second else "https://evil.example/?auth_token=BAD",
                "previous": None,
                "results": [row],
            },
        )

    adapter, http = make("cryptopanic", handler, cryptopanic_plan="growth")
    try:
        batch = await adapter.fetch()
        assert batch.complete and batch.entry_eligible and batch.items[0].instruments == ("BTC",)
        assert len(calls) == 2 and all(
            q.url.host == "cryptopanic.com" and q.url.path == "/api/growth/v2/posts/" for q in calls
        )
        assert all(q.url.params["auth_token"] == KEY and "size" not in q.url.params for q in calls)
    finally:
        await http.close()


async def test_cryptopanic_developer_or_truncated_pages_cannot_claim_safe_realtime():
    adapter, http = make(
        "cryptopanic",
        lambda _: httpx.Response(
            200, json={"next": "ANY_URL_IGNORED", "previous": None, "results": [crypto_item()]}
        ),
        news_max_pages=1,
    )
    try:
        batch = await adapter.fetch()
        assert not batch.complete and not batch.entry_eligible
    finally:
        await http.close()


@pytest.mark.parametrize("provider", ["newsapi", "finnhub", "cryptopanic"])
async def test_keys_alone_do_not_make_requests(provider):
    calls = []
    adapter, http = make(
        provider,
        lambda req: calls.append(req),
        newsapi_enabled=False,
        finnhub_news_enabled=False,
        cryptopanic_enabled=False,
    )
    try:
        with pytest.raises(NewsUnavailable):
            await adapter.fetch()
        assert not calls
    finally:
        await http.close()


@pytest.mark.parametrize(
    "raw", [b'{"status":"ok","status":"error"}', b'{"x":NaN}', b"```json\n{}\n```", b"not JSON", b"[]"]
)
async def test_provider_json_is_bounded_strict_not_repaired(raw):
    adapter, http = make(
        "newsapi", lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    )
    try:
        with pytest.raises(NewsInvalid):
            await adapter.fetch()
    finally:
        await http.close()


async def test_rss_304_validates_cache_without_new_publication_or_first_seen():
    cfg = Settings(_env_file=None, news_request_spacing_seconds=0.2)
    clock = ManualClock(ANCHOR)
    fixture = ScriptedNewsHTTP(clock)
    http = NewsHTTP(cfg, transport=fixture.transport)
    adapter = RSSAdapter(RSS_URL, cfg, clock, http)
    try:
        original = await adapter.fetch()
        clock.advance(timedelta(minutes=1))
        fixture.not_modified = True
        cached = await adapter.fetch()
        assert (
            cached.fetched_at == clock.now()
            and cached.items[0].published_at == original.items[0].published_at
        )
        assert cached.items[0].first_seen_at == original.items[0].first_seen_at
        assert fixture.calls[-1].headers["if-none-match"] == "fixture-etag"
    finally:
        await http.close()


async def test_secret_echo_never_reaches_stored_article_text():
    row = article()
    row["title"] = "API token " + KEY
    adapter, http = make(
        "newsapi",
        lambda _: httpx.Response(
            200,
            content=canonical_json({"status": "ok", "totalResults": 1, "articles": [row]}),
            headers={"content-type": "application/json"},
        ),
    )
    try:
        assert KEY not in (await adapter.fetch()).items[0].title
    finally:
        await http.close()
