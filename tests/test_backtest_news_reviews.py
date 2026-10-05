"""Archived knowledge times, stale/unknown/blocking windows and no fake provider fallback."""

from dataclasses import replace
from datetime import timedelta

import pytest

from backtesting.dataset import DatasetError, HistoricalDataset
from backtesting.news import ReplayNews
from backtesting.reviews import ReplayReviewer
from core.settings import Settings
from tests.backtest_helpers import document, fixture_path
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def data(tmp_path):
    path = fixture_path(tmp_path)
    dataset = HistoricalDataset.load(path)
    cfg = Settings(_env_file=None, symbols=("EURUSD",))
    now = dataset.manifest.replay_from
    news = document(path.parent / "synthetic_news.json")
    return path, dataset, cfg, now, news


def test_absent_archive_is_unknown_and_no_green_default(data):
    _, _, cfg, now, _ = data
    window = ReplayNews(None, cfg).window("EURUSD", now)
    assert not window.known and not window.safe and not window.allows(cfg, now, "EURUSD")


def test_snapshot_is_not_visible_before_first_observation_or_refreshed_at_read(data):
    _, _, cfg, now, news = data
    tape = ReplayNews(news, cfg)
    assert not tape.window("EURUSD", now - timedelta(microseconds=1)).known
    current = tape.window("EURUSD", now)
    later = tape.window("EURUSD", now + timedelta(seconds=20))
    assert current.evidence_hash == later.evidence_hash
    assert later.headlines_fetched_at == now and later.calendar_fetched_at == now
    assert later.allows(cfg, now + timedelta(seconds=20), "EURUSD")
    assert not tape.window("GBPUSD", now).known
    assert not tape.window("EURUSD", now + timedelta(seconds=901)).allows(cfg, now + timedelta(seconds=901))


@pytest.mark.parametrize("field", ["available_at", "headlines_fetched_at", "calendar_fetched_at"])
def test_no_future_poll_metadata_laundering(data, field):
    _, _, cfg, now, news = data
    news["snapshots"][0][field] = (now + timedelta(minutes=1)).isoformat()
    if field == "available_at":
        tape = ReplayNews(news, cfg)
        assert not tape.window("EURUSD", now).known
    else:
        with pytest.raises(DatasetError):
            ReplayNews(news, cfg)


@pytest.mark.parametrize("kind", ["unknown", "high"])
@pytest.mark.parametrize("offset,blocked", [(-31, False), (-30, True), (0, True), (15, True), (16, False)])
def test_known_future_calendar_is_legal_but_pre_post_risk_blocks(data, kind, offset, blocked):
    _, _, cfg, now, news = data
    snap = news["snapshots"][0]
    event = now + timedelta(minutes=31)
    snap["covered_until"] = (event + timedelta(hours=1)).isoformat()
    snap["events"] = [
        {
            "event_id": "TEST_EVENT",
            "known_at": now.isoformat(),
            "scheduled_at": event.isoformat(),
            "currency": "USD",
            "impact": kind,
            "precise_time": True,
        }
    ]
    tape = ReplayNews(news, cfg)
    when = event + timedelta(minutes=offset)
    assert tape.window("EURUSD", when).safe is not blocked


def test_tentative_event_time_blocks_entire_declared_window(data):
    _, _, cfg, now, news = data
    news["snapshots"][0]["events"] = [
        {
            "event_id": "tentative",
            "known_at": now.isoformat(),
            "scheduled_at": (now + timedelta(hours=1)).isoformat(),
            "currency": "USD",
            "impact": "high",
            "precise_time": False,
        }
    ]
    assert not ReplayNews(news, cfg).window("EURUSD", now).safe


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", 1),
        ("complete", "true"),
        ("covered_from", "2030-01-01T00:00:00Z"),
        ("covered_until", "2020-01-01T00:00:00Z"),
        ("symbols", ["EURUSD", "EURUSD"]),
    ],
)
def test_news_snapshot_schema_and_coverage_are_not_guessed(data, field, value):
    _, _, cfg, _, news = data
    news["snapshots"][0][field] = value
    with pytest.raises(DatasetError):
        ReplayNews(news, cfg)


def test_calendar_revision_cannot_exist_before_known_at(data):
    _, _, cfg, now, news = data
    news["snapshots"][0]["events"] = [
        {
            "event_id": "future",
            "known_at": (now + timedelta(seconds=1)).isoformat(),
            "scheduled_at": (now + timedelta(hours=1)).isoformat(),
            "currency": "USD",
            "impact": "high",
        }
    ]
    with pytest.raises(DatasetError):
        ReplayNews(news, cfg)


@pytest.mark.parametrize(
    "title,impact,language,blocked",
    [
        ("Federal Reserve emergency rate hike", "low", "en", True),
        ("New unknown market event", "unknown", "en", True),
        ("USD market calm and steady", "low", "en", False),
        ("Untranslated headline", "low", "unsupported", True),
        ("Global invasion announced", "low", "en", True),
    ],
)
def test_reuse_deterministic_impact_exposure_not_sentiment_as_permission(
    data, title, impact, language, blocked
):
    _, _, cfg, now, news = data
    news["snapshots"][0]["headlines"] = [
        {
            "title": title,
            "summary": "",
            "published_at": now.isoformat(),
            "first_seen_at": now.isoformat(),
            "currencies": ["USD"],
            "language": language,
            "impact": impact,
        }
    ]
    assert ReplayNews(news, cfg).window("EURUSD", now).safe is not blocked


def test_future_news_revision_cannot_change_earlier_safe_prefix(data):
    _, _, cfg, now, news = data
    before = ReplayNews(news, cfg).window("EURUSD", now)
    next_snap = dict(news["snapshots"][0])
    next_snap.update(available_at=(now + timedelta(seconds=30)).isoformat(), complete=False)
    news["snapshots"].append(next_snap)
    tape = ReplayNews(news, cfg)
    assert tape.window("EURUSD", now) == before
    assert not tape.window("EURUSD", now + timedelta(seconds=30)).known


def archive_entry(now, **changes):
    row = {
        "available_at": (now + timedelta(seconds=5)).isoformat(),
        "observed_at": now.isoformat(),
        "proposal_hash": "a" * 64,
        "news_hash": "b" * 64,
        "code_hash": "c" * 64,
        "model_sha256": "d" * 64,
        "decision": "approve",
        "confidence": 90,
        "risk_percent": None,
        "original_provider": "ollama",
        "provider_model": "TEST_NOT_A_REAL_MODEL",
        "request_hash": "e" * 64,
    }
    row.update(changes)
    return {
        "format": "reflex-replay-reviews-v1",
        "description": "TEST ONLY alleged archive",
        "entries": [row],
    }


async def test_reviewer_does_not_rebind_hashes_or_see_future_replies(data):
    from types import SimpleNamespace

    from trading.risk_types import NewsWindow

    _, dataset, _, now, _ = data
    clock = ManualClock(now)
    profile = RuntimeProfile("f" * 64, "1" * 64, SourceKind.HISTORICAL)
    reviewer = ReplayReviewer(
        archive_entry(now), mode="archive", dataset=dataset, profile=profile, clock=clock
    )
    proposal = SimpleNamespace(proposal_hash="a" * 64)
    news = NewsWindow(evidence_hash="b" * 64)
    assert await reviewer.review(proposal, news) is None
    clock.advance(timedelta(seconds=5))
    review = await reviewer.review(proposal, news)
    assert review.provider == "replay" and review.source == SourceKind.HISTORICAL
    assert review.code_hash == "c" * 64 and review.model_sha256 == "d" * 64  # NOT refreshed to f/1.
    assert review.observed_at == now and review.confidence == 90
    assert await reviewer.review(SimpleNamespace(proposal_hash="9" * 64), news) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", True),
        ("confidence", "90"),
        ("confidence", 101),
        ("risk_percent", 0.1),
        ("risk_percent", "NaN"),
        ("risk_percent", "2"),
        ("decision", "buy"),
        ("original_provider", "test"),
        ("proposal_hash", "bad"),
        ("provider_model", "bad model"),
        ("observed_at", "2030-01-01T00:00:00Z"),
    ],
)
def test_archived_reviews_require_real_types_and_chronology(data, field, value):
    _, dataset, _, now, _ = data
    with pytest.raises(DatasetError):
        ReplayReviewer(
            archive_entry(now, **{field: value}),
            mode="archive",
            dataset=dataset,
            profile=RuntimeProfile("c" * 64, "d" * 64, SourceKind.HISTORICAL),
            clock=ManualClock(now),
        )


def test_artificial_reviewer_is_not_available_on_imported_historical_prices(data):
    _, dataset, _, now, _ = data
    origin = dataset.manifest.origin.model_copy(update={"kind": "historical_import"})
    claimed = replace(dataset, manifest=dataset.manifest.model_copy(update={"origin": origin}))
    with pytest.raises(TradingDisabled):
        ReplayReviewer(
            None,
            mode="synthetic_research",
            dataset=claimed,
            profile=RuntimeProfile("c" * 64, "d" * 64, SourceKind.HISTORICAL),
            clock=ManualClock(now),
        )


def test_duplicate_archive_proposals_and_news_snapshot_epochs_are_rejected(data):
    _, dataset, cfg, now, news = data
    reviews = archive_entry(now)
    reviews["entries"].append(dict(reviews["entries"][0]))
    with pytest.raises(DatasetError):
        ReplayReviewer(
            reviews,
            mode="archive",
            dataset=dataset,
            profile=RuntimeProfile("c" * 64, "d" * 64, SourceKind.HISTORICAL),
            clock=ManualClock(now),
        )
    news["snapshots"].append(dict(news["snapshots"][0]))
    with pytest.raises(DatasetError):
        ReplayNews(news, cfg)


@pytest.mark.parametrize(
    "delta,code,news_hash,expect",
    [
        (0, "c" * 64, None, True),
        (1, "c" * 64, None, False),
        (-31, "c" * 64, None, False),
        (0, "f" * 64, None, False),
        (0, "c" * 64, "f" * 64, False),
    ],
)
def test_position_reviews_need_causal_scope_news_and_explicit_extension_policy(
    data, delta, code, news_hash, expect
):
    _, dataset, _, now, news = data
    cfg = Settings(_env_file=None, symbols=("EURUSD",), allow_tp_extension=True)
    tape = ReplayNews(news, cfg)
    digest = tape.window("EURUSD", now).evidence_hash
    item = {
        "available_at": (now + timedelta(seconds=max(0, delta))).isoformat(),
        "observed_at": (now + timedelta(seconds=delta)).isoformat(),
        "logical_symbol": "EURUSD",
        "position_identifier": 123,
        "position_hash": "a" * 64,
        "code_hash": code,
        "model_sha256": "d" * 64,
        "news_hash": news_hash or digest,
        "confidence": 90,
        "momentum_continues": True,
        "volatility_safe": True,
        "original_provider": "openai",
    }
    body = {
        "format": "reflex-replay-reviews-v1",
        "description": "TEST ONLY position review",
        "entries": [],
        "position_entries": [item],
    }
    reviewer = ReplayReviewer(
        body,
        mode="archive",
        dataset=dataset,
        profile=RuntimeProfile("c" * 64, "d" * 64, SourceKind.HISTORICAL),
        clock=ManualClock(now),
    )
    result = reviewer.position_reviews(tape, cfg)
    assert bool(result) is expect
    if expect:
        assert result[123].position_hash == "a" * 64 and result[123].source == SourceKind.HISTORICAL
    assert reviewer.position_reviews(tape, cfg.model_copy(update={"allow_tp_extension": False})) == {}
