"""TEST ONLY news helpers. Native-tagged DTOs are contract tests, never actual native proof."""

from __future__ import annotations

from datetime import timedelta

from core.database import Database
from core.settings import Settings
from news.evidence import project_window
from news.http_client import NewsHTTP
from news.news_cache import NewsCache
from news.news_manager import NewsManager
from news.types import CalendarSnapshot, EconomicEvent, Headline, HeadlineBatch, rss_source_id
from scripts.synthetic_news_fixtures import RSS_URL, ScriptedNewsHTTP, news_fixture_settings
from scripts.synthetic_signal_market import ANCHOR
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


async def make_news_runtime(tmp_path, *, source=SourceKind.SYNTHETIC, **changes):
    data = news_fixture_settings()
    data.update(changes)
    cfg = Settings(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD", "GBPUSD"),
        **data,
    )
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(ANCHOR)
    fixture = ScriptedNewsHTTP(clock)
    http = NewsHTTP(cfg, transport=fixture.transport)
    manager = NewsManager(db, cfg, clock, RuntimeProfile.current(cfg, source), http=http)
    await manager.initialize()
    return manager, fixture


def publish_contract_news(
    database,
    settings,
    clock,
    profile,
    *,
    title="USD and Euro stable market commentary",
    events=None,
    fixture_only=False,
):
    """Hand-authored source claims to isolate verifier contracts. ZERO API/broker calls.

    fixture_only=False is a test of the claimed genuine branch, not genuine data.
    No training, promotion, SDK execution or live capability may use this helper.
    """
    now = clock.now()
    identifier = rss_source_id(RSS_URL)
    article = Headline(
        identifier,
        "HAND AUTHORED CONTRACT TEST",
        title,
        "No real news or source validation.",
        "https://article-fixture.example/contract",
        now - timedelta(minutes=1),
        now,
    )
    batch = HeadlineBatch(identifier, now, (article,), fixture_only)
    if events is None:
        start = now + timedelta(hours=2)
        events = (EconomicEvent("test-contract-event", "TEST ONLY USD release", "USD", start, start, "high"),)
    calendar = CalendarSnapshot(
        "calendar:json_http",
        now,
        now,
        now - timedelta(hours=24),
        now + timedelta(hours=48),
        ("USD", "EUR", "GBP"),
        tuple(events),
        True,
        "fixture" if fixture_only else "provider",
        fixture_only,
    )
    cache = NewsCache(database, settings, clock, profile)
    claim, previous = cache.begin()
    epoch, digest = cache.finish(claim, (batch,), calendar, None, previous)
    _, state = cache.latest()
    return {
        symbol: project_window(
            state,
            epoch=epoch,
            snapshot_hash=digest,
            logical_symbol=symbol,
            settings=settings,
            profile=profile,
            now=now,
        ).window
        for symbol in settings.symbols
    }
