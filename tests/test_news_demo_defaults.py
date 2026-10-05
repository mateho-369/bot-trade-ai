"""Demo news defaults: faireconomy weekly calendar, inspect --fetch probe, NEWS_UNAVAILABLE_POLICY.

Offline: feeds are httpx.MockTransport fixtures shaped like the live
nfs.faireconomy.media/ff_calendar_thisweek.json and fxstreet RSS payloads.
"""

import json
from datetime import timedelta, timezone

import httpx
import pytest

from core.database import Database
from core.settings import Settings
from news.economic_calendar import (
    FAIRECONOMY_URL,
    FaireconomyCalendar,
    calendar_adapter,
    parse_faireconomy,
)
from news.evidence import MIN_LOT_DEMO_REASON, project_window
from news.http_client import NewsHTTP
from news.news_cache import NewsCache
from news.types import NewsInvalid, NewsUnavailable, rss_source_id
from scripts import inspect_news_sources
from scripts.synthetic_news_fixtures import RSS_URL, ScriptedNewsHTTP, news_fixture_settings
from scripts.synthetic_signal_market import ANCHOR
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind

ET = timezone(timedelta(hours=-4))


def feed(now, **overrides):
    """ForexFactory-format rows around ``now`` (same shape as the live weekly feed)."""
    local = now.astimezone(ET)
    spec = (
        ("FOMC Meeting Minutes", "USD", timedelta(hours=3), "High"),
        ("German Factory Orders", "EUR", timedelta(hours=1), "Low"),
        ("BOJ Gov Ueda Speaks", "JPY", timedelta(hours=2), "High"),
        ("Bank Holiday", "CNY", timedelta(0), "Holiday"),
        ("G20 Meetings", "All", timedelta(minutes=30), "Medium"),
    )
    rows = [{"title": t, "country": c, "date": local + d, "impact": i} for t, c, d, i in spec]
    for row in rows:
        row.update(forecast="", previous="")
        row["date"] = row["date"].replace(microsecond=0).isoformat()
    for key, value in overrides.items():
        rows[0][key] = value
    return json.dumps(rows).encode()


def settings(tmp_path, **changes):
    values = dict(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD", "XAUUSD"),
        calendar_provider="faireconomy",
        calendar_faireconomy_reviewed=True,
    )
    values.update(changes)
    return Settings(**values)


# -- faireconomy parsing ---------------------------------------------------------------------------
def test_weekly_feed_becomes_a_complete_sunday_to_sunday_snapshot(tmp_path):
    cfg = settings(tmp_path)
    now = ANCHOR
    snap = parse_faireconomy(feed(now), settings=cfg, fetched_at=now, produced_at=now)
    assert snap.source_id == "calendar:faireconomy" and snap.complete and not snap.fixture_only
    assert snap.covered_from <= now < snap.covered_until
    assert snap.covered_until - snap.covered_from == timedelta(days=7)
    assert snap.covered_from.astimezone(ET).weekday() == 6  # Sunday 00:00 New York.
    by_title = {(e.title, e.currency): e.impact for e in snap.events}
    assert by_title[("FOMC Meeting Minutes", "USD")] == "high"
    assert by_title[("German Factory Orders", "EUR")] == "low"
    assert ("BOJ Gov Ueda Speaks", "JPY") not in by_title  # Not an exposure of EURUSD/XAUUSD.
    assert {c for t, c in by_title if t == "G20 Meetings"} == {"EUR", "USD"}  # "All" -> every exposure.


def test_unknown_impact_fails_closed_as_unknown(tmp_path):
    snap = parse_faireconomy(
        feed(ANCHOR, impact="Very High"), settings=settings(tmp_path), fetched_at=ANCHOR, produced_at=ANCHOR
    )
    assert {e.impact for e in snap.events if e.title == "FOMC Meeting Minutes"} == {"unknown"}


@pytest.mark.parametrize(
    "overrides",
    [{"date": "2026-10-05T10:00:00"}, {"country": "Mars"}, {"title": ""}, {"impact": None}],
    ids=["naive-time", "bad-country", "empty-title", "missing-impact"],
)
def test_malformed_rows_reject_the_whole_calendar(tmp_path, overrides):
    with pytest.raises(NewsInvalid):
        parse_faireconomy(
            feed(ANCHOR, **overrides), settings=settings(tmp_path), fetched_at=ANCHOR, produced_at=ANCHOR
        )


def test_empty_feed_is_not_a_quiet_week(tmp_path):
    with pytest.raises(NewsInvalid):
        parse_faireconomy(b"[]", settings=settings(tmp_path), fetched_at=ANCHOR, produced_at=ANCHOR)


async def test_adapter_requires_review_and_caches_for_thirty_minutes(tmp_path):
    clock = ManualClock(ANCHOR)
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=feed(clock.now()), headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handle)
    unreviewed = settings(tmp_path, calendar_faireconomy_reviewed=False)
    with pytest.raises(NewsUnavailable):
        await FaireconomyCalendar(unreviewed, clock, NewsHTTP(unreviewed, transport=transport)).fetch()
    cfg = settings(tmp_path)
    http = NewsHTTP(cfg, transport=transport)
    adapter = calendar_adapter(cfg, clock, http)
    assert isinstance(adapter, FaireconomyCalendar)
    first = await adapter.fetch()
    clock.advance(timedelta(minutes=29))
    assert await adapter.fetch() is first and calls == [FAIRECONOMY_URL]
    clock.advance(timedelta(minutes=2))
    await adapter.fetch()
    assert len(calls) == 2
    await http.close()


# -- inspect_news_sources --fetch ------------------------------------------------------------------
async def test_inspect_fetch_reports_working_sources(tmp_path):
    clock = ManualClock(ANCHOR)
    scripted = ScriptedNewsHTTP(clock)

    async def handle(request):
        if str(request.url) == FAIRECONOMY_URL:
            body = feed(clock.now())
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return await scripted.handle(request)

    data = news_fixture_settings(("EURUSD", "XAUUSD"))
    data.pop("calendar_source_url")
    cfg = settings(tmp_path, **data)
    result = await inspect_news_sources.probe(cfg, clock, httpx.MockTransport(handle))
    assert result["ok"] is True, result
    [source] = result["sources"]
    assert source["source_id"] == rss_source_id(RSS_URL) and source["items"] >= 1 and source["fresh_enough"]
    assert result["calendar"]["source_id"] == "calendar:faireconomy" and result["calendar"]["events"] >= 3


async def test_inspect_fetch_reports_a_dead_feed_without_secrets(tmp_path):
    clock = ManualClock(ANCHOR)

    def handle(request):
        return httpx.Response(503)

    data = news_fixture_settings(("EURUSD", "XAUUSD"))
    data.pop("calendar_source_url")
    cfg = settings(tmp_path, **data, finnhub_api_key="secret-finnhub-value")
    result = await inspect_news_sources.probe(cfg, clock, httpx.MockTransport(handle))
    assert result["ok"] is False and result["sources"][0]["error"]
    assert result["calendar"]["error"] and "secret-finnhub-value" not in json.dumps(result)


def test_inspect_without_fetch_makes_no_http_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inspect_news_sources, "get_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(inspect_news_sources, "probe", lambda *a, **k: pytest.fail("HTTP probe ran"))
    assert inspect_news_sources.main() == 0
    assert json.loads(capsys.readouterr().out)["news_unavailable_policy"] == "block"


# -- NEWS_UNAVAILABLE_POLICY -----------------------------------------------------------------------
def unavailable_window(tmp_path, *, demo=True, reviewed=True, **changes):
    data = news_fixture_settings(("EURUSD",))
    policy = dict(data["news_source_coverage"][rss_source_id(RSS_URL)], reviewed=reviewed)
    data["news_source_coverage"] = {rss_source_id(RSS_URL): policy}
    values = dict(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD",),
        calendar_provider="json_http",
        **data,
    )
    if demo:
        values.update(paper_trading=False, mt5_backend="real")
    values.update(changes)
    cfg = Settings(**values)
    database = Database(cfg)
    database.initialize()
    clock = ManualClock(ANCHOR)
    profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
    cache = NewsCache(database, cfg, clock, profile)
    claim, previous = cache.begin()
    failed = ((rss_source_id(RSS_URL), "NewsUnavailable"),)
    epoch, digest = cache.finish(claim, failed, None, "NewsUnavailable", previous)
    _, state = cache.latest()
    try:
        return project_window(
            state,
            epoch=epoch,
            snapshot_hash=digest,
            logical_symbol="EURUSD",
            settings=cfg,
            profile=profile,
            now=clock.now(),
        )
    finally:
        database.close()


def test_unavailable_news_blocks_by_default(tmp_path):
    decision = unavailable_window(tmp_path)
    assert not decision.window.known and not decision.window.safe
    assert "calendar_unavailable" in decision.reasons
    assert MIN_LOT_DEMO_REASON not in decision.reasons


def test_min_lot_demo_allows_demo_when_sources_are_truly_unavailable(tmp_path):
    decision = unavailable_window(tmp_path, news_unavailable_policy="min_lot_demo")
    assert decision.window.known and decision.window.safe
    assert MIN_LOT_DEMO_REASON in decision.reasons


def test_min_lot_demo_is_ignored_outside_demo(tmp_path):
    decision = unavailable_window(tmp_path, demo=False, news_unavailable_policy="min_lot_demo")
    assert not decision.window.safe and MIN_LOT_DEMO_REASON not in decision.reasons


def test_min_lot_demo_never_overrides_an_unreviewed_source(tmp_path):
    decision = unavailable_window(tmp_path, reviewed=False, news_unavailable_policy="min_lot_demo")
    assert not decision.window.safe and "unreviewed_or_delayed_headline_scope" in decision.reasons
