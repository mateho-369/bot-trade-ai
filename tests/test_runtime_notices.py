"""Claim-before-send runtime notices with artificial transport; never Telegram."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.notifications import TEXT, RuntimeNotices
from core.database import Database
from core.models import AuditLog
from tests.risk_helpers import MOMENT, config
from trading.types import ManualClock


@pytest.fixture
def system(tmp_path):
    s = config(tmp_path)
    d = Database(s)
    d.initialize()
    c = ManualClock(MOMENT)
    services = NS(settings=s, database=d, clock=c, preview_only=False)
    transport = NS(services=services, bot=NS(send_message=AsyncMock(return_value=NS(message_id=1))))
    yield RuntimeNotices(d, s, c), transport
    d.close()


@pytest.mark.parametrize("kind", list(TEXT))
async def test_fixed_kind_notifications_are_owner_only_and_never_permission(system, kind):
    notices, transport = system
    key = notices.enqueue(kind, dedup="TEST_ONLY")
    assert notices.enqueue(kind, dedup="TEST_ONLY") == key
    result = await notices.drain(transport)
    assert result == {"delivered": 1, "uncertain": 0}
    transport.bot.send_message.assert_awaited_once()
    kwargs = transport.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == notices.settings.telegram_owner_id and kwargs["parse_mode"] is None
    assert not {"token", "password", "account"}.intersection(kwargs)
    assert (await notices.drain(transport))["delivered"] == 0


async def test_attempt_exists_before_network_callback(system):
    notices, transport = system
    notices.enqueue("restart", dedup="TEST_ONLY")

    async def send(**kwargs):
        with notices.database.session() as db:
            assert db.scalar(select(AuditLog.id).where(AuditLog.action == "runtime.notice_attempted"))
            assert db.scalar(select(AuditLog.id).where(AuditLog.action == "runtime.notice_delivered")) is None

    transport.bot.send_message.side_effect = send
    assert (await notices.drain(transport))["delivered"] == 1


@pytest.mark.parametrize("error", [TimeoutError, RuntimeError])
async def test_possible_delivery_is_never_retried_after_restart(system, error):
    notices, transport = system
    notices.enqueue("stalled", dedup="TEST_ONLY")
    transport.bot.send_message.side_effect = error("PRIVATE_TOKEN_DO_NOT_LOG")
    assert (await notices.drain(transport))["uncertain"] == 1
    restarted = RuntimeNotices(notices.database, notices.settings, notices.clock)
    assert (await restarted.drain(transport))["uncertain"] == 0
    assert transport.bot.send_message.await_count == 1
    with notices.database.session() as db:
        assert "PRIVATE_TOKEN_DO_NOT_LOG" not in str(
            [row.details for row in db.scalars(select(AuditLog)).all()]
        )
        assert db.scalar(select(AuditLog.id).where(AuditLog.action == "runtime.notice_delivered")) is None


async def test_cancellation_is_uncertain_and_not_resubmittable(system):
    notices, transport = system
    notices.enqueue("restart", dedup="TEST_ONLY")
    transport.bot.send_message.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await notices.drain(transport)
    assert not notices.claim_batch()


async def test_simultaneous_drains_have_one_durable_claim(system):
    notices, transport = system
    notices.enqueue("started", dedup="TEST_ONLY")
    a, b = await asyncio.gather(notices.drain(transport), notices.drain(transport))
    assert a["delivered"] + b["delivered"] == 1 and transport.bot.send_message.await_count == 1


async def test_old_claimed_rows_do_not_starve_new_notices(system):
    notices, transport = system
    for i in range(20):
        notices.enqueue("job_failed", dedup=str(i))
        assert notices.claim_batch(limit=1)
    notices.enqueue("restart", dedup="NEW_TEST_ONLY")
    assert (await notices.drain(transport, limit=1))["delivered"] == 1


async def test_expired_and_wrong_scope_do_not_send(system, tmp_path):
    notices, transport = system
    notices.enqueue("started", dedup="EXPIRED_TEST_ONLY")
    notices.clock.advance(timedelta(hours=25))
    assert (await notices.drain(transport))["delivered"] == 0
    different = config(tmp_path, max_daily_trades=11)
    other = RuntimeNotices(notices.database, different, notices.clock)
    other.enqueue("restart", dedup="OTHER_SCOPE_TEST_ONLY")
    assert (await notices.drain(transport))["delivered"] == 0
    transport.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("preview_only", True), ("database", object()), ("clock", object())])
async def test_mismatched_transport_refused_before_claim(system, field, value):
    notices, transport = system
    setattr(transport.services, field, value)
    notices.enqueue("restart", dedup="TEST_ONLY")
    with pytest.raises(ValueError):
        await notices.drain(transport)
    transport.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "kind,dedup", [("resume", "x"), ("started", ""), ("started", "x" * 129), ("started", 1)]
)
def test_notice_input_is_fixed_bounded_local_seam(system, kind, dedup):
    notices, transport = system
    with pytest.raises(ValueError):
        notices.enqueue(kind, dedup=dedup)


@pytest.mark.parametrize("limit", [0, 11, True, 1.5])
def test_claim_bound_enforced(system, limit):
    notices, transport = system
    with pytest.raises(ValueError):
        notices.claim_batch(limit)
