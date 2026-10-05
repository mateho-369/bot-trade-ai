"""Managed proof contracts, immutable snapshot hashes, latest-pointer/CAS/file/generation checks."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from core.database import Database
from core.models import AuditLog
from core.security import canonical_json
from core.settings import Settings
from news.economic_calendar import FileCalendar
from news.evidence import project_window, verify_window
from news.news_cache import NewsCache, _path, load_snapshot
from news.types import Headline, HeadlineBatch, NewsInvalid, NewsUnavailable, rss_source_id
from scripts.synthetic_news_fixtures import RSS_URL, calendar_document, news_fixture_settings
from scripts.synthetic_signal_market import ANCHOR
from tests.news_helpers import make_news_runtime, publish_contract_news
from tests.signal_helpers import news
from trading.risk_types import NewsWindow, RuntimeProfile
from trading.types import BrokerError, ManualClock, SourceKind


@pytest.fixture
async def runtime(tmp_path):
    m, f = await make_news_runtime(tmp_path)
    await m.refresh()
    yield m, f
    await m.close()
    m.database.close()


def verify(m, w, **changes):
    data = {"logical_symbol": "EURUSD", "settings": m.settings, "profile": m.profile, "now": m.clock.now()}
    data.update(changes)
    with m.database.session() as session:
        return verify_window(session, w, **data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("config_hash", "b" * 64),
        ("code_hash", "b" * 64),
        ("data_source", SourceKind.TEST_SDK),
        ("snapshot_hash", "b" * 64),
        ("snapshot_epoch", 999999),
        ("evidence_hash", "b" * 64),
        ("logical_symbol", "GBPUSD"),
        ("headlines_fetched_at", ANCHOR - timedelta(seconds=1)),
        ("calendar_covered_from", ANCHOR - timedelta(hours=23)),
    ],
)
async def test_each_projection_field_is_recomputed_not_trusted(runtime, field, value):
    m, _ = runtime
    w = await m.window("EURUSD")
    assert not verify(m, replace(w, **{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"logical_symbol": "EURUSD"},
        {"snapshot_epoch": True},
        {"fixture_only": 1},
        {"data_source": "mt5"},
        {"snapshot_hash": "not-a-hash"},
    ],
)
def test_partial_or_ambiguous_managed_binding_fails_dto(changes):
    with pytest.raises(BrokerError):
        NewsWindow(**changes)


async def test_round_trip_preserves_enum_datetimes_and_binding(runtime):
    m, _ = runtime
    w = await m.window("EURUSD")
    import json

    restored = NewsWindow.from_dict(json.loads(canonical_json(w.to_dict())))
    assert restored == w and verify(m, restored)


async def test_latest_pointer_required_and_orphan_file_cannot_grant_permission(runtime):
    m, _ = runtime
    w = await m.window("EURUSD")
    original = m.cache._base(
        refreshing=False, sources=[], calendar=None, calendar_error="no_source", stories=[], fixture=False
    )
    from news.news_cache import _immutable_write

    orphan = _immutable_write(m.settings, original)
    assert orphan != w.snapshot_hash and not verify(m, replace(w, snapshot_hash=orphan))
    assert verify(m, w)


async def test_late_claim_cannot_replace_new_generation(runtime):
    m, _ = runtime
    old = await m.window("EURUSD")
    claim, previous = m.cache.begin()
    with pytest.raises(NewsUnavailable):
        m.cache.begin()
    m.clock.advance(timedelta(seconds=36))
    newclaim, _ = m.cache.begin()
    assert newclaim != claim
    with pytest.raises(NewsUnavailable):
        m.cache.finish(claim, (), None, "invalid", previous)
    assert not m.cache.fail(claim, "late_failure") and not verify(m, old)
    assert m.cache.fail(newclaim, "test_end")


async def test_snapshot_symlink_or_corrupt_bytes_fail_closed(runtime, tmp_path):
    m, _ = runtime
    w = await m.window("EURUSD")
    path = _path(m.settings, w.snapshot_hash)
    original = path.read_bytes()
    try:
        path.write_bytes(b"{}")
        assert not verify(m, w)
        with pytest.raises(NewsInvalid):
            load_snapshot(m.settings, w.snapshot_hash)
        path.unlink()
        target = tmp_path / "target.json"
        target.write_bytes(original)
        path.symlink_to(target)
        assert not verify(m, w)
    finally:
        path.unlink()
        path.write_bytes(original)


async def test_shutdown_epoch_invalidation_only_if_current_not_newer(runtime):
    m, _ = runtime
    old = await m.window("EURUSD")
    await m.refresh()
    new = await m.window("EURUSD")
    assert not m.cache.invalidate_epoch(old.snapshot_epoch, "old_task") and verify(m, new)
    assert m.cache.invalidate_epoch(new.snapshot_epoch, "current_task") and not verify(m, new)


async def test_native_requires_managed_current_non_fixture_claims_not_legacy_booleans(tmp_path):
    cfg = Settings(
        _env_file=None, project_root=tmp_path, symbols=("EURUSD",), **news_fixture_settings(("EURUSD",))
    )
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(ANCHOR)
    profile = RuntimeProfile.current(cfg, SourceKind.MT5)
    try:
        with db.session() as session:
            assert not verify_window(
                session, news(clock), logical_symbol="EURUSD", settings=cfg, profile=profile, now=clock.now()
            )
        w = publish_contract_news(db, cfg, clock, profile)["EURUSD"]
        with db.session() as session:
            assert verify_window(
                session, w, logical_symbol="EURUSD", settings=cfg, profile=profile, now=clock.now()
            )
        # False here tests a source claim path; the DTOs above are NOT real native evidence.
        assert not w.fixture_only
    finally:
        db.close()


async def test_local_file_change_detected_at_gate_before_next_poll(tmp_path):
    data = news_fixture_settings(("EURUSD",))
    data["calendar_source_url"] = ""
    data["calendar_provider"] = "file"
    data["calendar_file_reviewed"] = True
    cfg = Settings(_env_file=None, project_root=tmp_path, symbols=("EURUSD",), **data)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(ANCHOR)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    cache = NewsCache(db, cfg, clock, profile)
    path = cfg.resolve_path(cfg.calendar_file)
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(calendar_document(ANCHOR, origin="owner_reviewed")))
    cal = await FileCalendar(cfg, clock).fetch()
    identifier = rss_source_id(RSS_URL)
    article = Headline(
        identifier,
        "TEST",
        "Euro USD market commentary",
        "",
        "https://article.example/test",
        ANCHOR - timedelta(minutes=1),
        ANCHOR,
    )
    claim, prev = cache.begin()
    epoch, digest = cache.finish(
        claim, (HeadlineBatch(identifier, ANCHOR, (article,), True),), cal, None, prev
    )
    _, state = cache.latest()
    w = project_window(
        state,
        epoch=epoch,
        snapshot_hash=digest,
        logical_symbol="EURUSD",
        settings=cfg,
        profile=profile,
        now=clock.now(),
    ).window
    try:
        with db.session() as session:
            assert verify_window(
                session, w, logical_symbol="EURUSD", settings=cfg, profile=profile, now=clock.now()
            )
        changed = calendar_document(ANCHOR, origin="owner_reviewed")
        changed["events"][0]["title"] = "Owner update"
        path.write_text(canonical_json(changed))
        with db.session() as session:
            assert not verify_window(
                session, w, logical_symbol="EURUSD", settings=cfg, profile=profile, now=clock.now()
            )
    finally:
        db.close()


async def test_snapshot_files_bound_and_failed_publication_does_not_commit_new_safe(runtime):
    m, _ = runtime
    before = await m.window("EURUSD")
    claim, prev = m.cache.begin()
    from core.settings import Settings

    restricted = Settings(
        _env_file=None,
        project_root=m.settings.project_root,
        **{**m.settings.model_dump(mode="python"), "news_snapshot_max_files": 100},
    )
    folder = _path(m.settings, before.snapshot_hash).parent
    for i in range(100):
        (folder / f"fake-{i}.json").write_text("{}")
    cache = NewsCache(m.database, restricted, m.clock, m.profile)
    # Storage control is itself policy-bound, so its empty scope cannot reuse another scope's green.
    with pytest.raises(NewsUnavailable):
        cache.invalidate("storage_cap")
    assert not verify(m, before)
    with m.database.session() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) > 0
    assert m.cache.fail(claim, "cap_test")
