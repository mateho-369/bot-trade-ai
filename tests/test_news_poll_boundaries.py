"""Whole-poll timeout, unknown required IDs, feed/cache timestamp contradictions and calendar DST."""

import asyncio
from datetime import timedelta

import pytest

from news.types import Headline, HeadlineBatch, NewsInvalid, NewsUnavailable
from scripts.synthetic_signal_market import ANCHOR
from tests.news_helpers import make_news_runtime, publish_contract_news


async def test_whole_poll_timeout_never_restores_preexisting_green(tmp_path):
    manager, fixture = await make_news_runtime(tmp_path, news_refresh_timeout_seconds=0.05)
    try:
        publish_contract_news(
            manager.database, manager.settings, manager.clock, manager.profile, fixture_only=True
        )
        assert (await manager.window("EURUSD")).known
        fixture.block = asyncio.Event()
        with pytest.raises(NewsUnavailable):
            await manager.refresh()
        assert not (await manager.window("EURUSD")).known
        fixture.block.set()
    finally:
        await manager.close()
        manager.database.close()


async def test_unconfigured_required_source_never_creates_partial_green(tmp_path):
    manager, _ = await make_news_runtime(tmp_path, news_required_source_ids=("missing-provider",))
    try:
        await manager.refresh()
        assert not (await manager.window("EURUSD")).known
    finally:
        await manager.close()
        manager.database.close()


def test_origin_cache_timestamp_cannot_predate_its_own_headline_publication():
    item = Headline(
        "test-feed",
        "TEST",
        "USD market news",
        "",
        "https://article.example/a",
        ANCHOR - timedelta(minutes=5),
        ANCHOR,
    )
    with pytest.raises(NewsInvalid):
        HeadlineBatch("test-feed", ANCHOR, (item,), fresh_as_of=ANCHOR - timedelta(minutes=10))
