"""Historical terminal outcomes are readable, never reexecuted after restart."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_actions import StagedAction
from app.owner_identity import OwnerInterfaceError
from app.owner_services import OwnerServices
from core.models import OwnerApproval
from tests.owner_helpers import actor, owner_runtime, owner_services
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client


async def test_completed_resume_replay_after_real_mock_runtime_restart_stays_paused(tmp_path):
    first = await owner_runtime(tmp_path)
    who, key = actor(first), str(uuid4())
    q = await first.action(who, "resume", {}, key)
    assert (await first.action(who, "resume", {}, key, q["confirmation_token"]))["status"] == "completed"
    await first.execution.shutdown()
    engine = ExecutionEngine(MockMT5Client(first.settings, clock=first.clock), first.database, first.settings)
    await engine.initialize()
    try:
        second = OwnerServices(first.database, first.settings, execution=engine)
        replay = await second.action(actor(second), "resume", {}, key, q["confirmation_token"])
        assert replay["status"] == "completed" and replay["replayed"] and replay["historical_runtime"]
        assert second.database.status()["state"] == "paused"  # historical outcome is not a new resume
        with pytest.raises(OwnerInterfaceError):
            await second.actions.describe(actor(second), second.scope(), q["confirmation_token"])
    finally:
        await engine.shutdown()
        first.database.close()


async def test_unique_sql_reservation_across_simultaneous_interface_instances(tmp_path):
    one = owner_services(tmp_path)
    two = OwnerServices(one.database, one.settings, clock=one.clock)
    key = str(uuid4())

    def reserve(services):
        try:
            return services.actions.stage(actor(services), services.scope(), "pause", {}, key)
        except OwnerInterfaceError as error:
            return error.code

    results = await asyncio.gather(asyncio.to_thread(reserve, one), asyncio.to_thread(reserve, two))
    assert sum(isinstance(r, StagedAction) for r in results) == 1
    assert sum(r == "action_pending_or_uncertain_do_not_retry" for r in results if isinstance(r, str)) == 1
    with one.database.session() as session:
        assert len(session.scalars(select(OwnerApproval)).all()) == 1


@pytest.mark.parametrize(
    "changed", ["account_key", "config_hash", "code_hash", "model_sha256", "data_source", "mode"]
)
def test_historical_terminal_replay_safety_identity_still_bound(tmp_path, changed):
    services = owner_services(tmp_path)
    who, key = actor(services), str(uuid4())
    run = services.actions.stage(who, services.scope(), "pause", {}, key)
    services.actions.finish(run, {"status": "completed"})
    scope = replace(services.scope(), **{changed: "CHANGED"})
    with pytest.raises(OwnerInterfaceError, match="idempotency_payload_conflict"):
        services.actions.cached(who, scope, "pause", {}, key)
