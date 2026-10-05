"""Synthetic shadow execution only: latest news veto before send, protection/close continue."""

import pytest
from sqlalchemy import select

from core.models import OrderIntent, RiskState
from news.http_client import NewsHTTP
from news.news_manager import NewsManager
from scripts.synthetic_news_fixtures import ScriptedNewsHTTP, news_fixture_settings
from tests.risk_helpers import OWNER, D
from tests.signal_helpers import TestReviewer, make_signal_runtime, review
from trading.risk_types import PositionReview
from trading.types import ResultStatus, SourceKind, TradingDisabled


@pytest.fixture
async def runtime(tmp_path):
    signals, execution = await make_signal_runtime(
        tmp_path, allow_tp_extension=True, **news_fixture_settings(("EURUSD",))
    )
    fixture = ScriptedNewsHTTP(execution.clock)
    http = NewsHTTP(execution.settings, transport=fixture.transport)
    news = NewsManager(execution.database, execution.settings, execution.clock, execution.profile, http=http)
    await news.initialize()
    await news.refresh()
    yield signals, execution, news, fixture
    await news.close()
    await execution.shutdown()
    execution.database.close()


async def approve(signals, news):
    return await signals.evaluate("EURUSD", reviewer=TestReviewer(signals), news=await news.window("EURUSD"))


async def test_managed_news_review_binds_persisted_signal_and_allows_synthetic_shadow_fill(runtime):
    s, e, n, _ = runtime
    ready = await approve(s, n)
    assert ready.approved
    assert ready.context.news.managed and ready.context.news.logical_symbol == "EURUSD"
    e.control.resume(OWNER, account_key=e.account_key)
    filled = await e.execute_signal(ready.signal_id)
    assert filled.status == ResultStatus.FILLED and len(await e.broker.get_positions()) == 1
    assert e.broker.source_kind == SourceKind.SYNTHETIC


async def test_news_epoch_revoked_during_ai_review_denies_finalization(runtime):
    s, _, n, _ = runtime
    window = await n.window("EURUSD")
    proposal = await s.analyze("EURUSD")
    advice = review(s, proposal, news_hash=window.evidence_hash)
    n.cache.invalidate("new_news_update")
    result = await s.finalize(proposal.signal_id, review=advice, news=window)
    assert not result.approved and "unbound_revoked_or_fixture_news" in result.reasons


async def test_approved_signal_old_news_is_not_rebound_after_new_publication(runtime):
    s, e, n, _ = runtime
    ready = await approve(s, n)
    assert ready.approved
    n.cache.invalidate("headline_changed")
    e.control.resume(OWNER, account_key=e.account_key)
    with pytest.raises(TradingDisabled):
        await e.execute_signal(ready.signal_id)
    assert await e.broker.get_positions() == ()
    with e.database.session() as session:
        row = session.scalar(select(RiskState))
        assert row.reserved_risk_usd == 0 and row.accepted_entries_today == 0


async def test_news_epoch_changes_after_grant_veto_before_any_shadow_mutation(runtime, monkeypatch):
    s, e, n, _ = runtime
    ready = await approve(s, n)
    e.control.resume(OWNER, account_key=e.account_key)
    original = e.authority.before_send

    def revoke_then_validate(command, snapshot, grant):
        n.cache.invalidate("breaking_after_grant")
        return original(command, snapshot, grant)

    monkeypatch.setattr(e.authority, "before_send", revoke_then_validate)
    with pytest.raises(TradingDisabled, match="changed before send"):
        await e.execute_signal(ready.signal_id)
    assert await e.broker.get_positions() == ()
    with e.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "rejected"
        row = session.scalar(select(RiskState))
        assert row.reserved_risk_usd == 0 and row.accepted_entries_today == 0


async def test_unknown_news_and_kill_never_prevent_improved_stop_or_owner_close(runtime):
    s, e, n, _ = runtime
    ready = await approve(s, n)
    e.control.resume(OWNER, account_key=e.account_key)
    await e.execute_signal(ready.signal_id)
    position = (await e.broker.get_positions())[0]
    n.cache.invalidate("calendar_lost")
    e.control.kill(OWNER)
    improved = position.sl + D("0.00010")
    await e.protect_sl(position.ticket, position.identifier, improved)
    assert (await e.broker.get_positions())[0].sl == improved
    await e.close_owned(position.ticket, position.identifier)
    assert await e.broker.get_positions() == ()


async def test_managed_tp_review_new_epoch_denies_extension_preserves_protection(runtime):
    s, e, n, _ = runtime
    ready = await approve(s, n)
    e.control.resume(OWNER, account_key=e.account_key)
    await e.execute_signal(ready.signal_id)
    owned = e.logger.owned(e.account_key)[0]
    await e.broker.market.set_tick("EURUSD", D("1.10280"), D("1.10292"))
    await e.reconcile()
    position = (await e.broker.get_positions())[0]
    advice = PositionReview(e.clock.now(), SourceKind.SYNTHETIC, 90, True, True, await n.window("EURUSD"))
    tp = await e.trailing.extension(position, owned, advice)
    assert tp and tp > position.tp
    n.cache.invalidate("news_revoked")
    before_sl = position.sl
    before_tp = position.tp
    with pytest.raises(TradingDisabled):
        await e.extend_tp(position.ticket, position.identifier, tp, advice)
    after = (await e.broker.get_positions())[0]
    assert after.sl == before_sl and after.tp == before_tp


async def test_advisory_ai_sentiment_never_changes_rule_blocks_or_coverage_epoch(runtime):
    _, _, n, f = runtime
    f.title = "USD emergency rate decision"
    from datetime import timedelta

    n.clock.advance(timedelta(seconds=30))
    await n.refresh()
    before = await n.window("EURUSD")
    assert not before.safe

    class Advice:
        async def analyze_news(self, items):
            assert items
            return {"decision": "approve", "confidence": 100, "sentiment": 1}

    response = await n.advisory_sentiment(Advice())
    assert response["confidence"] == 100 and (await n.window("EURUSD")) == before
