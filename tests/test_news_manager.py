"""Fail-closed collector lifecycle, scope, age, economic boundaries, dedup and cancellation."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from core.models import BotState, News
from news.evidence import verify_window
from news.types import NewsInvalid, rss_source_id
from scripts.synthetic_news_fixtures import RSS_URL, calendar_document, news_fixture_settings
from tests.news_helpers import make_news_runtime
from trading.types import SourceKind, TradingDisabled


@pytest.fixture
async def runtime(tmp_path):
    manager, fixture = await make_news_runtime(tmp_path)
    yield manager, fixture
    await manager.close()
    manager.database.close()


def valid(manager, window, logical="EURUSD"):
    with manager.database.session() as session:
        return verify_window(
            session,
            window,
            logical_symbol=logical,
            settings=manager.settings,
            profile=manager.profile,
            now=manager.clock.now(),
        )


async def test_startup_unknown_then_safe_poll_remains_paused_and_no_broker(runtime):
    m, f = runtime
    assert not (await m.window("EURUSD")).known
    result = await m.refresh()
    window = await m.window("EURUSD")
    assert result.symbols_known == 2 and result.symbols_safe == 2 and valid(m, window)
    assert window.logical_symbol == "EURUSD" and window.fixture_only and len(f.calls) == 2
    with m.database.session() as session:
        assert session.get(BotState, 1).desired_state == "paused"


async def test_no_cross_symbol_or_disabled_alias_reuse(runtime):
    m, _ = runtime
    await m.refresh()
    eur = await m.window("EURUSD")
    assert not eur.allows(m.settings, m.clock.now(), "GBPUSD") and not valid(m, eur, "GBPUSD")
    with pytest.raises(NewsInvalid):
        await m.window("EURUSD.a")


@pytest.mark.parametrize(
    "field,value",
    [
        ("rss_status", 503),
        ("rss_body", b"bad XML"),
        ("rss_body", b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'),
        ("calendar_status", 429),
        ("calendar_body", b"[]"),
        ("calendar_body", b'{"events":[]}'),
    ],
)
async def test_missing_malformed_empty_and_provider_errors_are_not_known_safe(runtime, field, value):
    m, f = runtime
    setattr(f, field, value)
    await m.refresh()
    decision = await m.decision("EURUSD")
    assert not decision.window.known and not decision.allowed


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed": False},
        {"realtime": False},
        {"currencies": ("USD",)},
        {"language": "unsupported"},
    ],
)
async def test_real_headlines_without_complete_owner_scopes_remain_unknown(tmp_path, change):
    data = news_fixture_settings()["news_source_coverage"]
    data[rss_source_id(RSS_URL)].update(change)
    m, _ = await make_news_runtime(tmp_path, news_source_coverage=data)
    try:
        await m.refresh()
        assert not (await m.window("EURUSD")).known
    finally:
        await m.close()
        m.database.close()


async def test_native_tagged_market_cannot_use_mock_http_news(tmp_path):
    m, _ = await make_news_runtime(tmp_path, source=SourceKind.MT5)
    try:
        await m.refresh()
        decision = await m.decision("EURUSD")
        assert not decision.allowed and "fixture_news_on_non_synthetic_market" in decision.reasons
    finally:
        await m.close()
        m.database.close()


async def test_calendar_specific_currency_blocks_only_affected_symbol(runtime):
    m, f = runtime
    at = m.clock.now() + timedelta(minutes=20)
    f.events = [
        {
            "id": "gbp-event",
            "title": "TEST GBP release",
            "currency": "GBP",
            "starts_at": at.isoformat(),
            "ends_at": at.isoformat(),
            "impact": "high",
            "tentative": False,
        }
    ]
    await m.refresh()
    eur = await m.decision("EURUSD")
    gbp = await m.decision("GBPUSD")
    assert eur.allowed and gbp.window.known and not gbp.allowed and gbp.blocking_ids == ("gbp-event",)
    assert valid(m, eur.window) and not valid(m, gbp.window, "GBPUSD")


async def test_green_expires_exactly_at_pre_event_boundary_without_poll(runtime):
    m, f = runtime
    at = m.clock.now() + timedelta(minutes=31)
    f.events = [
        {
            "id": "soon",
            "title": "TEST USD release",
            "currency": "USD",
            "starts_at": at.isoformat(),
            "ends_at": at.isoformat(),
            "impact": "high",
            "tentative": False,
        }
    ]
    await m.refresh()
    old = await m.window("EURUSD")
    assert old.expires_at == m.clock.now() + timedelta(minutes=1) and valid(m, old)
    m.clock.advance(timedelta(minutes=1))
    assert not old.allows(m.settings, m.clock.now()) and not valid(m, old)
    current = await m.decision("EURUSD")
    assert not current.allowed and "soon" in current.blocking_ids
    assert len(f.calls) == 2


async def test_calendar_post_window_inclusive_and_tentative_unknown_block(runtime):
    m, f = runtime
    at = m.clock.now() - timedelta(minutes=15)
    f.events = [
        {
            "id": "past",
            "title": "TEST tentative event",
            "currency": "GBP",
            "starts_at": at.isoformat(),
            "ends_at": at.isoformat(),
            "impact": "low",
            "tentative": True,
        }
    ]
    await m.refresh()
    assert not (await m.decision("GBPUSD")).allowed
    m.clock.advance(timedelta(microseconds=1))
    assert (await m.decision("GBPUSD")).allowed


async def test_calendar_unknown_impact_is_not_low_and_medium_policy_is_configurable(runtime):
    m, f = runtime
    at = m.clock.now() + timedelta(minutes=10)
    f.events = [
        {
            "id": "unknown",
            "title": "TEST uncertain release",
            "currency": "EUR",
            "starts_at": at.isoformat(),
            "ends_at": at.isoformat(),
            "impact": "unknown",
            "tentative": False,
        }
    ]
    await m.refresh()
    assert not (await m.decision("EURUSD")).allowed and (await m.decision("GBPUSD")).allowed


async def test_new_snapshot_invalidates_old_green_even_when_content_unchanged(runtime):
    m, f = runtime
    await m.refresh()
    old = await m.window("EURUSD")
    m.clock.advance(timedelta(seconds=1))
    await m.refresh()
    new = await m.window("EURUSD")
    assert (
        new.known
        and new.safe
        and new.snapshot_epoch != old.snapshot_epoch
        and not valid(m, old)
        and valid(m, new)
    )
    assert len(f.calls) == 3  # headline poll throttled; independent calendar refreshed.
    assert new.headlines_fetched_at == old.headlines_fetched_at


async def test_breaking_story_revokes_old_green_and_blocks_usd_exposure(runtime):
    m, f = runtime
    await m.refresh()
    old = await m.window("EURUSD")
    m.clock.advance(timedelta(seconds=30))
    f.title = "USD emergency FOMC rate decision"
    await m.refresh()
    decision = await m.decision("EURUSD")
    assert not valid(m, old) and decision.window.known and not decision.window.safe
    assert "breaking_headline_window" in decision.reasons and len(m.alerts.pending()) == 1
    assert not (await m.window("GBPUSD")).safe


async def test_dedup_never_refreshes_first_seen_pub_or_improves_impact(runtime):
    m, f = runtime
    f.title = "Euro market strong gains"
    await m.refresh()
    original = (await m.list_news())[0]
    m.clock.advance(timedelta(seconds=30))
    f.summary = "Emergency capital controls"
    await m.refresh()
    current = (await m.list_news())[0]
    assert (
        current["content_hash"] == original["content_hash"]
        and current["published_at"] == original["published_at"]
    )
    assert current["first_seen_at"] == original["first_seen_at"] and current["impact"] == "high"
    with m.database.session() as session:
        assert session.scalar(select(func.count()).select_from(News)) == 1
    m.clock.advance(timedelta(seconds=30))
    f.summary = "Quiet"
    await m.refresh()
    assert (await m.list_news())[0]["impact"] == "high"


async def test_calendar_issue_time_and_empty_feed_do_not_get_fresh_by_reread(runtime):
    m, f = runtime
    f.produced_at = m.clock.now() - timedelta(hours=7)
    await m.refresh()
    assert not (await m.window("EURUSD")).known
    assert "stale_incomplete_or_unreviewed_calendar" in (await m.decision("EURUSD")).reasons


async def test_source_and_calendar_range_must_cover_lookbehind_and_lookahead(runtime):
    m, f = runtime
    from core.security import canonical_json

    data = calendar_document(m.clock.now())
    data["covered_from"] = m.clock.now().isoformat()
    f.calendar_body = canonical_json(data).encode()
    await m.refresh()
    assert not (await m.window("EURUSD")).known


async def test_refresh_claim_invalidates_immediately_and_cancel_never_restores_green(runtime):
    m, f = runtime
    await m.refresh()
    old = await m.window("EURUSD")
    m.clock.advance(timedelta(seconds=30))
    f.block = asyncio.Event()
    started = asyncio.Event()
    original = m.http.get

    async def delayed(*args, **kwargs):
        started.set()
        return await original(*args, **kwargs)

    m.http.get = delayed
    task = asyncio.create_task(m.refresh())
    await started.wait()
    assert not valid(m, old) and not (await m.window("EURUSD")).known
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await m.window("EURUSD")).known
    f.block.set()
    m.clock.advance(timedelta(seconds=30))
    await m.refresh()
    assert (await m.window("EURUSD")).known


async def test_shutdown_restart_is_unknown_until_new_poll_and_does_not_resume(runtime):
    m, f = runtime
    await m.refresh()
    old = await m.window("EURUSD")
    await m.close()
    assert not valid(m, old)
    with pytest.raises(TradingDisabled):
        await m.window("EURUSD")
    await m.initialize()
    assert not (await m.window("EURUSD")).known
    await m.refresh()
    assert (await m.window("EURUSD")).known
    with m.database.session() as session:
        assert session.get(BotState, 1).desired_state == "paused"


async def test_snapshot_tamper_and_symbol_config_changes_cannot_certify_old_window(runtime):
    m, _ = runtime
    await m.refresh()
    window = await m.window("EURUSD")
    from news.news_cache import _path

    path = _path(m.settings, window.snapshot_hash)
    original = path.read_bytes()
    path.write_text("{}")
    assert not valid(m, window)
    assert not window.allows(m.settings, m.clock.now(), "GBPUSD")
    assert not valid(m, replace(window, evidence_hash="b" * 64))
    path.write_bytes(original)


async def test_provider_failure_never_uses_previous_good_batch_as_fresh(runtime):
    m, f = runtime
    await m.refresh()
    old = await m.window("EURUSD")
    m.clock.advance(timedelta(seconds=30))
    f.rss_status = 503
    await m.refresh()
    assert not (await m.window("EURUSD")).known and not valid(m, old)
    calls = len(f.calls)
    m.clock.advance(timedelta(seconds=1))
    await m.refresh()
    assert not (await m.window("EURUSD")).known and len(f.calls) == calls + 1
