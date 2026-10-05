"""SCRIPTED Telegram sends only; durable outbox does not guarantee exactly once."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import AuditLog
from news.alerts import NewsAlerts
from telegram_bot.notifications import OwnerNewsNotifier
from tests.owner_helpers import fake_transport, owner_services
from tests.risk_helpers import OWNER


def outbox(services):
    alerts = NewsAlerts(services.database, services.settings, services.clock, "c" * 64)
    with services.database.session() as session:
        row = services.database.add_audit(
            session,
            "news.alert_pending",
            "news",
            {
                "alert_id": "d" * 64,
                "scope_hash": alerts.scope,
                "kind": "coverage_unknown",
                "symbols": ["EURUSD"],
                "title": "TEST_ONLY <b>coverage unknown</b>",
                "expires_at": (services.clock.now() + timedelta(minutes=15)).isoformat(),
                "fixture_only": True,
                "delivered": False,
            },
        )
        row.time = services.clock.now()
    return alerts


async def test_outbox_staged_before_send_and_ack_only_success(tmp_path, monkeypatch):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    alerts = outbox(services)
    notifier = OwnerNewsNotifier(transport, alerts)
    original = session.make_request

    async def checked(bot, method, timeout=None):
        with services.database.session() as sql:
            assert sql.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivery_attempted"))
            assert not sql.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivered"))
        assert method.chat_id == OWNER and method.parse_mode is None
        return await original(bot, method, timeout)

    monkeypatch.setattr(session, "make_request", checked)
    result = await notifier.drain()
    assert result == {"delivered": 1, "uncertain": 0, "skipped": 0}
    assert await notifier.drain() == {"delivered": 0, "uncertain": 0, "skipped": 0}
    assert len(session.calls) == 1 and "SYNTHETIC FIXTURE" in session.calls[0][1].text
    await transport.close()


async def test_lost_delivery_never_auto_resends_or_claims_delivered(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services, fail_methods=("SendMessage",))
    alerts = outbox(services)
    notifier = OwnerNewsNotifier(transport, alerts)
    assert (await notifier.drain())["uncertain"] == 1
    assert (await notifier.drain())["skipped"] == 1
    assert len(session.calls) == 1
    with services.database.session() as sql:
        assert not sql.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivered"))
        assert sql.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivery_uncertain"))
    await transport.close()


async def test_expired_alert_not_sent(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    alerts = outbox(services)
    services.clock.advance(timedelta(minutes=15))
    assert (await OwnerNewsNotifier(transport, alerts).drain())["delivered"] == 0
    assert session.calls == []
    await transport.close()


async def test_cancel_after_network_attempt_records_uncertain_not_retry(tmp_path, monkeypatch):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    alerts = outbox(services)
    notifier = OwnerNewsNotifier(transport, alerts)
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session, "make_request", blocked)
    task = asyncio.create_task(notifier.drain())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with services.database.session() as sql:
        assert sql.scalar(select(AuditLog.id).where(AuditLog.action == "news.alert_delivery_uncertain"))
    assert (await notifier.drain())["skipped"] == 1
    await transport.close()


@pytest.mark.parametrize("limit", [0, 11, True, "1"])
async def test_notification_bound(tmp_path, limit):
    services = owner_services(tmp_path)
    transport, _ = fake_transport(services)
    with pytest.raises(ValueError):
        await OwnerNewsNotifier(transport, outbox(services)).drain(limit=limit)
    await transport.close()
