"""Real SQLite/synthetic lifecycle; no deployment or native/Telegram/provider I/O."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.dependencies import compose
from app.lifecycle import RuntimeLifecycle
from app.process_guard import ProcessLock, operator_stop_requested, read_json, request_operator_stop
from core.database import Database
from core.models import BotState, RiskState
from tests.risk_helpers import MOMENT, OWNER, config, open_one
from trading.mock_mt5 import MockMT5Client
from trading.types import ManualClock, TradingDisabled


def prepare(tmp_path, **values):
    settings = config(
        tmp_path,
        runtime_backup_enabled=False,
        ai_provider="disabled",
        ai_fallback_provider="disabled",
        **values,
    )
    db = Database(settings)
    db.initialize()
    db.close()

    def factory(s, d):
        return compose(s, d, broker=MockMT5Client(s, clock=ManualClock(MOMENT)))

    return RuntimeLifecycle(settings, factory=factory)


async def test_start_stop_is_paused_durable_and_no_transport(tmp_path):
    service = prepare(tmp_path)
    await service.start()
    r = service.resources
    assert r.database.status()["state"] == "paused" and service.health.state == "ready"
    assert r.reporter.enabled is False and not hasattr(service, "api")
    before = r.engine.control.session_id
    r.engine.control.resume(OWNER, account_key=r.engine.account_key)
    await service.stop()
    assert service._closed and service.lock.handle is None
    data = read_json(service.settings.resolve_path(service.settings.runtime_health_file))
    assert data["status"] == "stopped" and data["control"] == "paused" and data["session_id"] is None
    restarted = RuntimeLifecycle(service.settings, factory=service.factory)
    await restarted.start()
    try:
        assert restarted.resources.database.status()["state"] == "paused"
        assert restarted.resources.engine.control.session_id != before
    finally:
        await restarted.stop()


async def test_shutdown_preserves_kill_loss_history_and_positions(tmp_path):
    service = prepare(tmp_path)
    await service.start()
    r = service.resources
    await open_one(r.engine)
    r.engine.control.kill(OWNER)
    with r.database.locked_session() as db:
        row = db.scalar(select(RiskState))
        row.drawdown_latched = True
    await service.stop()
    restarted = RuntimeLifecycle(service.settings, factory=service.factory)
    await restarted.start()
    try:
        with restarted.database.session() as db:
            assert db.get(BotState, 1).kill_switch_active
            assert db.scalar(select(RiskState)).drawdown_latched
        assert len(await restarted.resources.broker.get_positions()) == 1
        assert restarted.database.status()["state"] == "killed"
    finally:
        await restarted.stop()


async def test_second_instance_cannot_replace_health_or_create_broker(tmp_path):
    first = prepare(tmp_path)
    await first.start()
    try:
        path = first.settings.resolve_path(first.settings.runtime_health_file)
        identity = read_json(path)["managed_id"]
        called = []

        def forbidden(*args):
            called.append(True)
            raise AssertionError("factory must not run without runtime lock")

        second = RuntimeLifecycle(first.settings, factory=forbidden)
        with pytest.raises(RuntimeError):
            await second.start()
        await second.stop()
        assert not called and read_json(path)["managed_id"] == identity
    finally:
        await first.stop()


async def test_factory_failure_does_not_migrate_and_lock_releases(tmp_path):
    service = prepare(tmp_path)

    def fail(*args):
        raise RuntimeError("PRIVATE_INPUT_DO_NOT_LOG")

    service.factory = fail
    with pytest.raises(RuntimeError):
        await service.start()
    await service.stop()
    assert service._closed
    with ProcessLock(service.settings.resolve_path(service.settings.runtime_lock_file)):
        pass
    db = Database(service.settings)
    try:
        db.verify_schema()
        assert db.status()["state"] == "paused"
    finally:
        db.close()


async def test_persistent_operator_stop_is_never_implicitly_cleared(tmp_path):
    service = prepare(tmp_path)
    request_operator_stop(service.settings)
    with pytest.raises(RuntimeError, match="stop request"):
        await service.start()
    await service.stop()
    assert operator_stop_requested(service.settings)


async def test_native_pending_evidence_holds_database_and_os_lock(tmp_path):
    service = prepare(tmp_path)
    await service.start()
    r = service.resources
    pending = [1]
    original = r.broker.health
    r.broker.health = lambda: dict(original(), pending_calls=pending[0])
    stopping = asyncio.create_task(service.stop())
    try:
        await asyncio.sleep(0.1)
        assert not stopping.done()
        with pytest.raises(RuntimeError):
            ProcessLock(service.settings.resolve_path(service.settings.runtime_lock_file)).acquire()
        r.database.verify_schema()
    finally:
        pending[0] = 0
        await stopping
    assert service._closed


async def test_system_shutdown_pause_is_session_fenced_not_a_latch_reset(tmp_path):
    service = prepare(tmp_path)
    await service.start()
    r = service.resources
    try:
        r.engine.control.resume(OWNER, account_key=r.engine.account_key)
        r.engine.control.pause_for_shutdown()
        assert r.database.status()["state"] == "paused"
        with r.database.locked_session() as db:
            state = db.get(BotState, 1)
            state.kill_switch_active, state.last_error, state.desired_state = (
                True,
                "unknown_execution",
                "killed",
            )
        r.engine.control.pause_for_shutdown()
        with r.database.session() as db:
            state = db.get(BotState, 1)
            assert state.desired_state == "killed" and state.last_error == "unknown_execution"
        old = r.engine.control.session_id
        r.engine.control.session_id = str(uuid4())
        with pytest.raises(TradingDisabled):
            r.engine.control.pause_for_shutdown()
        r.engine.control.session_id = old
    finally:
        await service.stop()


async def test_pause_persistence_failure_still_fences_jobs_before_shutdown_retry(tmp_path):
    service = prepare(tmp_path)
    await service.start()
    r = service.resources
    original = r.engine.control.pause_for_shutdown

    def failed_pause():
        raise OSError("TEST_ONLY transient pause persistence unavailable")

    r.engine.control.pause_for_shutdown = failed_pause
    try:
        with pytest.raises(OSError):
            await service.stop()
        assert not service.scheduler.accepting
        assert (await service.scheduler.run_job("signals"))["state"] == "skipped"
        assert service.lock.handle is not None
    finally:
        r.engine.control.pause_for_shutdown = original
        await service.stop()
