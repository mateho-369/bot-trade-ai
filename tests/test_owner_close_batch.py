"""Captured batch semantics with synthetic orders; no atomic account flatten."""

from uuid import uuid4

import pytest

from tests.owner_helpers import actor, owner_runtime
from tests.risk_helpers import D, open_one, safe_context
from trading.types import ExecutionResult, Operation, ResultStatus, Side, UncertainExecution


@pytest.fixture
async def services(tmp_path):
    value = await owner_runtime(tmp_path, symbols=("EURUSD", "GBPUSD"))
    yield value
    await value.execution.shutdown()
    value.database.close()


async def open_gbp(services):
    engine = services.execution
    tick = await engine.broker.get_tick("GBPUSD")
    plan = await engine.calculator.plan_market_order(
        "GBPUSD", Side.BUY, tick.bid - D(".00220"), strategy="TEST_SECOND"
    )
    return await engine.execute(plan, safe_context(engine.clock))


async def test_second_failure_reports_first_close_and_remaining_no_retry(services, monkeypatch):
    await open_one(services.execution)
    await open_gbp(services)
    who, key = actor(services), str(uuid4())
    prepared = await services.action(who, "close_all", {}, key)
    original = services.execution.close_owned
    calls = 0

    async def first_then_lost(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise UncertainExecution("TEST_ONLY lost ack / SECRET_MARKER")
        return await original(*args, **kwargs)

    monkeypatch.setattr(services.execution, "close_owned", first_then_lost)
    result = await services.action(who, "close_all", {}, key, prepared["confirmation_token"])
    assert result["status"] == "uncertain" and not result["broker_account_flat_claimed"]
    assert [r["status"] for r in result["results"]] == ["filled", "unknown_or_denied"]
    assert (
        len(result["remaining_owned_ledger"]) == 1
        and result["remaining_owned_ledger"][0]["symbol"] == "GBPUSD"
    )
    assert "SECRET_MARKER" not in str(result)
    assert (await services.action(who, "close_all", {}, key, prepared["confirmation_token"]))["replayed"]
    assert calls == 2 and services.database.status()["state"] == "paused"


async def test_new_position_after_prepare_not_included_in_old_capture(services):
    await open_one(services.execution)
    who, key = actor(services), str(uuid4())
    prepared = await services.action(who, "close_all", {}, key)
    await open_gbp(services)
    result = await services.action(who, "close_all", {}, key, prepared["confirmation_token"])
    assert result["status"] == "completed" and len(result["results"]) == 1
    assert (
        len(result["remaining_owned_ledger"]) == 1
        and result["remaining_owned_ledger"][0]["symbol"] == "GBPUSD"
    )
    assert not result["broker_account_flat_claimed"]
    assert services.database.status()["state"] == "paused"


async def test_scripted_partial_result_never_claims_closed_or_continues_batch(services, monkeypatch):
    await open_one(services.execution)
    await open_gbp(services)
    who, key = actor(services), str(uuid4())
    prepared = await services.action(who, "close_all", {}, key)
    calls = 0

    async def partial(ticket, identifier, **kwargs):
        nonlocal calls
        calls += 1
        return ExecutionResult(
            Operation.CLOSE,
            kwargs["idempotency_key"],
            services.execution.account_key,
            ResultStatus.PARTIAL,
            position_identifier=identifier,
            filled_volume=D(".01"),
        )

    monkeypatch.setattr(services.execution, "close_owned", partial)
    result = await services.action(who, "close_all", {}, key, prepared["confirmation_token"])
    assert result["status"] == "uncertain" and calls == 1
    assert len(result["remaining_owned_ledger"]) == 2
    assert result["results"][0]["status"] == "partial" and not result["broker_account_flat_claimed"]
    assert services.database.status()["state"] == "paused"
