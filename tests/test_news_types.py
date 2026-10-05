"""Actual boolean/UTC/shape DTOs; no permissive provider string/numeric coercion."""

from dataclasses import replace
from datetime import timedelta

import pytest

from news.types import CalendarSnapshot, EconomicEvent, Headline, HeadlineBatch, NewsInvalid
from scripts.synthetic_signal_market import ANCHOR


@pytest.fixture
def story():
    return Headline(
        "test-feed",
        "TEST",
        "USD market commentary",
        "",
        "https://article.example/story",
        ANCHOR - timedelta(minutes=5),
        ANCHOR,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("fixture_only", 1),
        ("complete", "true"),
        ("entry_eligible", 1),
        ("items", []),
        ("source_id", "test/invalid"),
        ("fresh_as_of", ANCHOR + timedelta(seconds=1)),
        ("etag", "bad\nvalue"),
    ],
)
def test_headline_batch_is_not_permissive(story, field, value):
    data = {"source_id": "test-feed", "fetched_at": ANCHOR, "items": (story,)}
    data[field] = value
    with pytest.raises(NewsInvalid):
        HeadlineBatch(**data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "bad\x00title"),
        ("currencies", ("usd",)),
        ("instruments", ("BTC", "BTC")),
        ("published_at", ANCHOR + timedelta(seconds=1)),
        ("source_id", "bad id"),
    ],
)
def test_sanitized_source_dto_still_rejects_bad_internal_values(story, field, value):
    with pytest.raises(NewsInvalid):
        replace(story, **{field: value})


def test_story_canonical_syndication_id_ignores_source_tracking_and_case_not_day(story):
    syndicated = replace(
        story, source_id="test-other", title="USD MARKET commentary!", url="https://other.example/a"
    )
    assert story.content_hash == syndicated.content_hash
    nextday = replace(
        story,
        published_at=story.published_at + timedelta(days=1),
        first_seen_at=story.first_seen_at + timedelta(days=1),
    )
    assert story.content_hash != nextday.content_hash


def test_calendar_unknown_impact_can_block_but_ambiguous_shape_rejects():
    event = EconomicEvent("test", "TEST release", "USD", ANCHOR, ANCHOR, "unknown")
    snap = CalendarSnapshot(
        "calendar:file",
        ANCHOR,
        ANCHOR,
        ANCHOR - timedelta(days=1),
        ANCHOR + timedelta(days=1),
        ("USD",),
        (event,),
        True,
        "owner_reviewed",
    )
    assert snap.events[0].impact == "unknown"
    with pytest.raises(NewsInvalid):
        replace(snap, complete=1)
    with pytest.raises(NewsInvalid):
        replace(snap, events=(event, event))
    with pytest.raises(NewsInvalid):
        replace(event, ends_at=ANCHOR - timedelta(seconds=1))
