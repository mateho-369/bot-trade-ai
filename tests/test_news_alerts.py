"""Durable news report claims: dedup, sanitization, TTL, local-first outbound delivery."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import AuditLog, BotState
from tests.news_helpers import make_news_runtime


@pytest.fixture
async def runtime(tmp_path):
    m, f = await make_news_runtime(tmp_path)
    yield m, f
    await m.close()
    m.database.close()


async def test_high_headline_is_deduplicated_across_refresh_and_restart_not_sent(runtime):
    m, f = runtime
    f.title = "USD emergency rate decision"
    await m.refresh()
    first = m.alerts.pending()
    assert len(first) == 1 and not first[0]["delivered"] and first[0]["fixture_only"]
    m.clock.advance(timedelta(seconds=30))
    await m.refresh()
    assert len(m.alerts.pending()) == 1
    await m.close()
    await m.initialize()
    await m.refresh()
    assert len(m.alerts.pending()) == 1
    with m.database.session() as session:
        assert session.get(BotState, 1).desired_state == "paused"
        assert not session.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivered"))


async def test_news_alerts_are_outbound_only_and_reported_once_to_local_sink(runtime):
    from app.reporter import Reporter

    m, f = runtime
    f.title = "USD emergency news"
    await m.refresh()
    pending = m.alerts.pending()[0]
    assert pending["title"] == "USD emergency news"
    reporter = Reporter(m.settings, secrets=m.database.secrets, clock=m.clock, console=lambda _: None)
    result = await m.alerts.drain(reporter)
    assert result["disabled"] == 1 and result["reported"] == 0
    assert not m.alerts.pending()  # Attempt is claimed before optional outbound send.
    reports = m.settings.resolve_path(m.settings.data_dir) / "reports"
    assert (reports / "actions.log").is_file()
    with m.database.session() as session:
        assert not session.scalars(select(AuditLog).where(AuditLog.action == "news.alert_delivered")).all()
        assert (
            len(
                session.scalars(
                    select(AuditLog).where(AuditLog.action == "news.alert_report_attempted")
                ).all()
            )
            == 1
        )


async def test_old_pending_notifications_expire_not_delivered(runtime):
    m, f = runtime
    f.title = "USD emergency"
    await m.refresh()
    assert m.alerts.pending()
    m.clock.advance(timedelta(minutes=15))
    assert not m.alerts.pending()


async def test_provider_failure_queues_scope_unknown_without_auto_pause_or_resume(runtime):
    m, f = runtime
    f.calendar_status = 503
    await m.refresh()
    rows = m.alerts.pending()
    assert len(rows) == 2 and all(r["kind"] == "coverage_unknown" for r in rows)
    with m.database.session() as session:
        assert session.get(BotState, 1).desired_state == "paused"
    await m.refresh()
    assert len(m.alerts.pending()) == 2


async def test_high_impact_report_text_is_plain_and_sanitized(runtime):
    m, f = runtime
    f.title = "<b>USD emergency</b> <script>secret()</script>"
    await m.refresh()
    title = m.alerts.pending()[0]["title"]
    assert "<" not in title and "secret()" not in title and title == "USD emergency"
