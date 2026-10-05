"""Coverage TTL and late discovery/update risk; cancellation/durability never mint coverage."""

import asyncio
import threading
from datetime import timedelta

import pytest

from core.security import canonical_json
from news.types import NewsUnavailable
from scripts.synthetic_news_fixtures import calendar_document, rss_document
from tests.news_helpers import make_news_runtime


@pytest.fixture
async def runtime(tmp_path):
    m, f = await make_news_runtime(tmp_path)
    yield m, f
    await m.close()
    m.database.close()


def feed_two(now, old_title, old_pub, old_summary):
    fresh = rss_document(now, title="Fresh USD and Euro market fixture news")
    earlier = rss_document(now, title=old_title, summary=old_summary, published_at=old_pub)
    item = earlier[earlier.index(b"<item>") : earlier.index(b"</item>") + 7]
    return fresh.replace(b"</channel>", item + b"</channel>")


async def test_dedup_preserves_pub_first_seen_but_new_high_risk_content_gets_observation_time(runtime):
    m, f = runtime
    f.title = "USD and Euro policy commentary"
    await m.refresh()
    original = (await m.list_news())[0]
    old_pub = m.clock.now() - timedelta(minutes=5)
    m.clock.advance(timedelta(minutes=70))
    f.rss_body = feed_two(m.clock.now(), f.title, old_pub, "Emergency capital controls announced")
    await m.refresh()
    decision = await m.decision("EURUSD")
    assert decision.window.known and not decision.allowed
    rows = await m.list_news()
    changed = next(r for r in rows if r["content_hash"] == original["content_hash"])
    assert (
        changed["published_at"] == original["published_at"]
        and changed["first_seen_at"] == original["first_seen_at"]
    )
    _, state = m.cache.latest()
    risk = next(s for s in state["stories"] if s["content_hash"] == original["content_hash"])
    assert risk["risk_observed_at"] == m.clock.now().isoformat()
    m.clock.advance(timedelta(seconds=30))
    await m.refresh()
    _, again = m.cache.latest()
    assert (
        next(s for s in again["stories"] if s["content_hash"] == original["content_hash"])["risk_observed_at"]
        == risk["risk_observed_at"]
    )


async def test_high_risk_new_to_this_collector_delayed_publication_still_blocks(runtime):
    m, f = runtime
    f.rss_body = feed_two(
        m.clock.now(), "USD emergency decision", m.clock.now() - timedelta(hours=2), "Major policy release"
    )
    await m.refresh()
    d = await m.decision("EURUSD")
    assert d.window.known and not d.allowed
    # Publication is NOT freshened: the OTHER genuinely recent fixture article
    # provides headline cadence, while the late discovery conservatively blocks.
    rows = await m.list_news()
    high = next(r for r in rows if r["impact"] == "high")
    assert high["published_at"] != high["first_seen_at"]


async def test_disappearing_high_story_retained_until_risk_hold_ends(runtime):
    m, f = runtime
    f.title = "USD emergency decision"
    await m.refresh()
    original = (await m.list_news())[0]
    m.clock.advance(timedelta(seconds=30))
    f.title = "Different quiet fresh EUR USD report"
    await m.refresh()
    assert not (await m.decision("EURUSD")).allowed
    _, state = m.cache.latest()
    assert original["content_hash"] in {s["content_hash"] for s in state["stories"]}


async def test_news_http_304_and_quiet_feed_publication_deadline_not_freshened(runtime):
    m, f = runtime
    await m.refresh()
    pub = (await m.list_news())[0]["published_at"]
    f.not_modified = True
    m.clock.advance(timedelta(minutes=56))
    await m.refresh()
    d = await m.decision("EURUSD")
    assert not d.window.known and "stale_or_quiet_headline_source" in d.reasons
    assert (await m.list_news())[0]["published_at"] == pub


async def test_fresh_calendar_required_even_if_headline_sentiment_positive(runtime):
    m, f = runtime
    f.title = "Euro bullish strong rally"
    data = calendar_document(m.clock.now())
    data["complete"] = False
    f.calendar_body = canonical_json(data).encode()
    await m.refresh()
    d = await m.decision("EURUSD")
    assert not d.window.known and d.sentiment > 0


async def test_calendar_end_expires_pre_horizon_before_full_covered_until(runtime):
    m, f = runtime
    now = m.clock.now()
    at = now + timedelta(minutes=20)
    data = calendar_document(
        now,
        events=[
            {
                "id": "low",
                "title": "TEST low event",
                "currency": "USD",
                "starts_at": at.isoformat(),
                "ends_at": at.isoformat(),
                "impact": "low",
                "tentative": False,
            }
        ],
    )
    data["covered_until"] = (now + timedelta(minutes=31)).isoformat()
    f.calendar_body = canonical_json(data).encode()
    await m.refresh()
    window = await m.window("EURUSD")
    assert window.known and window.expires_at == now + timedelta(minutes=1)
    m.clock.advance(timedelta(minutes=1))
    assert not window.allows(m.settings, m.clock.now())


async def test_cancel_during_uncancellable_sql_thread_waits_and_revokes_its_epoch(runtime, monkeypatch):
    m, _ = runtime
    await m.refresh()
    began = threading.Event()
    release = threading.Event()
    original = m.cache.finish

    def held(*args):
        began.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(m.cache, "finish", held)
    task = asyncio.create_task(m.refresh())
    assert await asyncio.to_thread(began.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await m.window("EURUSD")).known


async def test_storage_failure_after_claim_is_not_previous_green(runtime, monkeypatch):
    m, _ = runtime
    await m.refresh()
    original = m.cache.finish

    def failed(*args):
        raise NewsUnavailable("durability_failed")

    monkeypatch.setattr(m.cache, "finish", failed)
    with pytest.raises(NewsUnavailable):
        await m.refresh()
    assert not (await m.window("EURUSD")).known
    monkeypatch.setattr(m.cache, "finish", original)


async def test_source_timeout_generic_unknown_and_no_old_good_restore(runtime):
    m, f = runtime
    await m.refresh()
    f.block = asyncio.Event()
    original = m.http.get

    async def cancelled(*args, **kwargs):
        raise TimeoutError("RAW_PRIVATE_TIMEOUT")

    m.http.get = cancelled
    from datetime import timedelta

    m.clock.advance(timedelta(seconds=30))
    await m.refresh()
    assert not (await m.window("EURUSD")).known
    m.http.get = original
    f.block.set()


@pytest.mark.parametrize(
    "field,value", [("news_impact_threshold", "medium"), ("block_trading_high_impact_news", False)]
)
async def test_medium_rules_and_disabled_block_policy_do_not_grant_new_permissions(tmp_path, field, value):
    m, f = await make_news_runtime(tmp_path, **{field: value})
    try:
        f.title = "Euro inflation outlook"
        await m.refresh()
        d = await m.decision("EURUSD")
        assert not d.allowed
        if field == "block_trading_high_impact_news":
            assert not d.window.known
    finally:
        await m.close()
        m.database.close()
