"""Explicit complete-interval/currency/issued metadata and timezone/empty/file bounds."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from core.security import canonical_json
from core.settings import Settings
from news.economic_calendar import FileCalendar, FinnhubCalendar, explicit_provider_time, parse_calendar
from news.http_client import NewsHTTP
from news.types import NewsInvalid, NewsUnavailable
from scripts.synthetic_news_fixtures import calendar_document
from scripts.synthetic_signal_market import ANCHOR
from trading.types import ManualClock


def parse(data, **changes):
    return parse_calendar(
        canonical_json(data).encode(),
        settings=Settings(_env_file=None, **changes),
        fetched_at=ANCHOR,
        source_id="calendar:json_http",
    )


def test_complete_snapshot_carries_known_interval_currencies_and_issue_time():
    result = parse(calendar_document(ANCHOR))
    assert result.complete and result.fixture_only and result.covered_from == ANCHOR - timedelta(hours=24)
    assert result.produced_at == ANCHOR and result.events[0].currency == "USD"


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", "true"),
        ("complete", 1),
        ("currencies", []),
        ("currencies", ["usd"]),
        ("currencies", ["USD", "USD"]),
        ("produced_at", "2026-10-03T12:00:00"),
        ("produced_at", "2026-10-03T12:00:01Z"),
        ("covered_from", "2026-10-06T12:00:00Z"),
        ("covered_until", "2026-11-01T12:00:00Z"),
        ("origin", "unverified-web-scrape"),
        ("events", {}),
    ],
)
def test_calendar_header_is_strict_not_a_bare_news_array(field, value):
    data = calendar_document(ANCHOR)
    data[field] = value
    with pytest.raises(NewsInvalid):
        parse(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "ZZZ"),
        ("impact", "tentative"),
        ("starts_at", "2026-10-03T14:00:00"),
        ("ends_at", "2026-10-03T13:00:00Z"),
        ("tentative", 1),
        ("id", ""),
        ("title", ""),
    ],
)
def test_bad_or_unknown_event_fields_do_not_silently_disappear(field, value):
    data = calendar_document(ANCHOR)
    data["events"][0][field] = value
    with pytest.raises(NewsInvalid):
        parse(data)


def test_missing_completeness_or_duplicate_event_identity_rejected():
    data = calendar_document(ANCHOR)
    data.pop("complete")
    with pytest.raises(NewsInvalid):
        parse(data)
    data = calendar_document(ANCHOR)
    data["events"] *= 2
    with pytest.raises(NewsInvalid):
        parse(data)


def test_empty_requires_explicit_review_and_complete_false_remains_false():
    data = calendar_document(ANCHOR, events=[])
    with pytest.raises(NewsInvalid):
        parse(data)
    assert parse(data, calendar_allow_empty_reviewed=True).events == ()
    data["complete"] = False
    assert not parse(data, calendar_allow_empty_reviewed=True).complete


@pytest.mark.parametrize("text", ["2026-11-01 01:30:00", "2026-03-08 02:30:00"])
def test_finnhub_ambiguous_or_nonexistent_naive_dst_time_not_guessed(text):
    with pytest.raises(NewsInvalid):
        explicit_provider_time(text, ZoneInfo("America/New_York"))


def test_finnhub_explicit_offsets_and_owner_configured_utc():
    assert explicit_provider_time("2026-10-03 12:00:00", ZoneInfo("UTC")) == ANCHOR
    assert explicit_provider_time("2026-10-03T08:00:00-04:00", ZoneInfo("America/New_York")) == ANCHOR


async def test_owner_reviewed_file_reread_does_not_refresh_issued_time(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path, calendar_file_reviewed=True)
    clock = ManualClock(ANCHOR)
    path = cfg.resolve_path(cfg.calendar_file)
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(calendar_document(ANCHOR, origin="owner_reviewed")))
    adapter = FileCalendar(cfg, clock)
    first = await adapter.fetch()
    clock.advance(timedelta(minutes=5))
    second = await adapter.fetch()
    assert second.fetched_at > first.fetched_at and second.produced_at == first.produced_at
    assert first.source_digest == second.source_digest and not second.fixture_only


async def test_file_is_not_used_without_review_missing_regular_or_symlink(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    adapter = FileCalendar(cfg, ManualClock(ANCHOR))
    with pytest.raises(NewsUnavailable):
        await adapter.fetch()
    cfg = Settings(_env_file=None, project_root=tmp_path, calendar_file_reviewed=True)
    adapter = FileCalendar(cfg, ManualClock(ANCHOR))
    with pytest.raises(NewsUnavailable):
        await adapter.fetch()
    path = cfg.resolve_path(cfg.calendar_file)
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "other.json")
    with pytest.raises(NewsUnavailable):
        await adapter.fetch()


async def test_finnhub_premium_calendar_disabled_without_owner_entitlement_scope():
    calls = []
    cfg = Settings(_env_file=None, finnhub_api_key="FAKE_KEY")
    http = NewsHTTP(cfg, transport=httpx.MockTransport(lambda r: calls.append(r)))
    try:
        with pytest.raises(NewsUnavailable):
            await FinnhubCalendar(cfg, ManualClock(ANCHOR), http).fetch()
        assert not calls
    finally:
        await http.close()


async def test_finnhub_calendar_explicit_review_fixed_endpoint_and_country_mapping():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "economicCalendar": [
                    {
                        "country": "US",
                        "event": "TEST USD release",
                        "time": "2026-10-03 14:00:00",
                        "impact": "high",
                    }
                ]
            },
        )

    cfg = Settings(
        _env_file=None, symbols=("EURUSD",), finnhub_api_key="FAKE_KEY", finnhub_calendar_scope_reviewed=True
    )
    http = NewsHTTP(cfg, transport=httpx.MockTransport(handler))
    try:
        snapshot = await FinnhubCalendar(cfg, ManualClock(ANCHOR), http).fetch()
        assert snapshot.fixture_only and snapshot.complete and snapshot.events[0].currency == "USD"
        assert (
            calls[0].url.path == "/api/v1/calendar/economic"
            and calls[0].headers["x-finnhub-token"] == "FAKE_KEY"
        )
        assert set(calls[0].url.params) == {"from", "to"}
    finally:
        await http.close()
