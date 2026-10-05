"""Durable staging, cancellation/uncertain completion, prompt safe-stop priority."""

import asyncio
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_identity import OwnerInterfaceError
from core.models import AuditLog, OwnerApproval
from tests.owner_helpers import actor, owner_runtime, owner_services
from tests.risk_helpers import open_one


@pytest.fixture
async def services(tmp_path):
    value = await owner_runtime(tmp_path)
    yield value
    await value.execution.shutdown()
    value.database.close()


async def close_request(services):
    await open_one(services.execution)
    owned = services.execution.logger.owned(services.execution.account_key)[0]
    who, key = actor(services), str(uuid4())
    params = {"ticket": owned.ticket, "position_identifier": owned.identifier}
    q = await services.action(who, "close_position", params, key)
    return who, params, key, q["confirmation_token"]


async def test_effect_sees_committed_stage_before_remote_call(services, monkeypatch):
    who, params, key, token = await close_request(services)
    original = services.execution.close_owned

    async def checked(*args, **kwargs):
        with services.database.session() as session:
            assert session.scalar(select(AuditLog.id).where(AuditLog.action == "owner.action_staged"))
            rows = session.scalars(select(OwnerApproval)).all()
            assert all(r.status == "consumed" for r in rows)
        return await original(*args, **kwargs)

    monkeypatch.setattr(services.execution, "close_owned", checked)
    assert (await services.action(who, "close_position", params, key, token))["status"] == "completed"


@pytest.mark.parametrize("action,state", [("pause", "paused"), ("kill", "killed")])
async def test_downward_control_not_queued_behind_long_async_close(services, monkeypatch, action, state):
    who, params, key, token = await close_request(services)
    started, release = asyncio.Event(), asyncio.Event()
    original = services.execution.close_owned

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(services.execution, "close_owned", blocked)
    slow = asyncio.create_task(services.action(who, "close_position", params, key, token))
    await asyncio.wait_for(started.wait(), 2)
    try:
        result = await asyncio.wait_for(services.action(who, action, {}, str(uuid4())), 2)
        assert result["status"] == "completed" and services.database.status()["state"] == state
    finally:
        release.set()
    assert (await slow)["status"] == "completed"
    assert services.database.status()["state"] == state


async def test_cancel_after_durable_stage_before_effect_waits_commit_and_records_uncertain(
    services, monkeypatch
):
    who, params, key, token = await close_request(services)
    staged_event, release = Event(), Event()
    original_stage = services.actions.stage

    def held_stage(*args, **kwargs):
        result = original_stage(*args, **kwargs)
        staged_event.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(services.actions, "stage", held_stage)
    task = asyncio.create_task(services.action(who, "close_position", params, key, token))
    assert await asyncio.to_thread(staged_event.wait, 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert params["position_identifier"] in services.execution.broker._positions
    replay = services.actions.cached(who, services.scope(), "close_position", params, key)
    assert replay["status"] == "uncertain" and replay["replayed"]


async def test_canceled_remote_effect_never_retries(services, monkeypatch):
    who, params, key, token = await close_request(services)
    started = asyncio.Event()
    calls = 0

    async def blocked(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(services.execution, "close_owned", blocked)
    task = asyncio.create_task(services.action(who, "close_position", params, key, token))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replay = await services.action(who, "close_position", params, key, token)
    assert replay["status"] == "uncertain" and calls == 1
    assert services.database.status()["state"] == "paused"


async def test_broker_effect_then_completion_persistence_failure_is_not_resubmitted(services, monkeypatch):
    who, params, key, token = await close_request(services)

    def broken_finish(*args, **kwargs):
        raise RuntimeError("TEST_ONLY SECRET_MARKER durable outcome unavailable")

    monkeypatch.setattr(services.actions, "finish", broken_finish)
    with pytest.raises(OwnerInterfaceError, match="do_not_retry"):
        await services.action(who, "close_position", params, key, token)
    assert params["position_identifier"] not in services.execution.broker._positions
    with pytest.raises(OwnerInterfaceError, match="pending_or_uncertain"):
        await services.action(who, "close_position", params, key, token)
    assert services.database.status()["state"] == "paused"


async def test_source_change_withholds_upward_action_but_safe_pause_still_works(services, monkeypatch):
    monkeypatch.setattr("app.owner_services.source_code_hash", lambda _: "0" * 64)
    with pytest.raises(OwnerInterfaceError, match="code_changed"):
        await services.action(actor(services), "resume", {}, str(uuid4()))
    result = await services.action(actor(services), "pause", {}, str(uuid4()))
    assert result["status"] == "completed"


async def test_standalone_owner_pause_kill_do_not_initialize_engine(tmp_path):
    services = owner_services(tmp_path)
    assert services.execution is None and services.control.session_id is None
    for action in ("pause", "kill"):
        assert (await services.action(actor(services), action, {}, str(uuid4())))["status"] == "completed"
    with pytest.raises(OwnerInterfaceError, match="not_attached"):
        await services.action(actor(services), "resume", {}, str(uuid4()))
    assert services.control.session_id is None
