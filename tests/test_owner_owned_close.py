"""Synthetic MockMT5 owner closes; NEVER real broker orders or stage evidence."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_identity import OwnerInterfaceError
from core.models import BrokerDeal, OrderIntent
from tests.owner_helpers import actor, owner_runtime
from tests.risk_helpers import OWNER, D, open_one
from trading.risk_types import position_review_hash
from trading.types import Operation, ResultStatus, TradingDisabled, UncertainExecution


@pytest.fixture
async def services(tmp_path):
    value = await owner_runtime(tmp_path)
    yield value
    await value.execution.shutdown()
    value.database.close()


async def prepare_close(services):
    await open_one(services.execution)
    owned = services.execution.logger.owned(services.execution.account_key)[0]
    params = {"ticket": owned.ticket, "position_identifier": owned.identifier}
    who = actor(services)
    key = str(uuid4())
    response = await services.action(who, "close_position", params, key)
    return who, params, key, response["confirmation_token"]


async def test_fresh_owned_close_once_under_concurrent_http_equivalent_callers(services):
    who, params, key, nonce = await prepare_close(services)
    outputs = await asyncio.gather(
        *(services.action(who, "close_position", params, key, nonce) for _ in range(8))
    )
    assert all(out["status"] == "completed" for out in outputs)
    assert sum(not out["replayed"] for out in outputs) == 1
    assert await services.execution.broker.get_positions() == ()
    with services.database.session() as session:
        intents = session.scalars(
            select(OrderIntent).where(OrderIntent.request["command"]["operation"].as_string() == "close")
        ).all()
        assert len(intents) == 1 and intents[0].request["expected_position_hash"] is not None
        assert len(session.scalars(select(BrokerDeal)).all()) == 2


@pytest.mark.parametrize("control", ["pause", "kill"])
async def test_ownership_close_remains_protective_under_pause_or_kill(services, control):
    who, params, key, nonce = await prepare_close(services)
    getattr(services.control, control)(OWNER)
    result = await services.action(who, "close_position", params, key, nonce)
    assert result["status"] == "completed"
    assert services.database.status()["state"] == ("killed" if control == "kill" else "paused")


@pytest.mark.parametrize("change", ["volume", "tp", "ticket", "magic", "direction"])
async def test_changed_captured_position_cannot_be_closed_by_old_confirmation(services, change):
    who, params, key, nonce = await prepare_close(services)
    broker = services.execution.broker
    position = broker._positions[params["position_identifier"]]
    from trading.types import Side

    changed = {
        "volume": position.volume / 2,
        "tp": position.tp + D(".00010"),
        "ticket": position.ticket + 1,
        "magic": position.magic + 1,
        "side": Side.SELL,
    }["side" if change == "direction" else change]
    modifications = {"side" if change == "direction" else change: changed}
    if change == "direction":
        # Feasible opposite-side protections: invalid BUY-side TP on a SELL would
        # trigger an independent paper server-style exit during reconcile.
        modifications.update(sl=position.entry_price + D(".002"), tp=position.entry_price - D(".004"))
    broker._positions[position.identifier] = replace(position, **modifications)
    result = await services.action(who, "close_position", params, key, nonce)
    assert result["status"] in {"uncertain", "rejected"}
    assert position.identifier in broker._positions
    assert broker._positions[position.identifier].volume > 0


async def test_improved_sl_does_not_invalidate_owner_close_hash(services):
    who, params, key, nonce = await prepare_close(services)
    await services.execution.protect_sl(params["ticket"], params["position_identifier"], D("1.09900"))
    result = await services.action(who, "close_position", params, key, nonce)
    assert result["status"] == "completed"


async def test_expected_hash_rechecked_in_final_authority_before_send(services, monkeypatch):
    who, params, key, nonce = await prepare_close(services)
    original = services.execution.authority.before_send

    def changed(command, snapshot, grant):
        if command.operation == Operation.CLOSE:
            snapshot = replace(
                snapshot, positions=tuple(replace(p, tp=p.tp + D(".00010")) for p in snapshot.positions)
            )
        return original(command, snapshot, grant)

    monkeypatch.setattr(services.execution.authority, "before_send", changed)
    result = await services.action(who, "close_position", params, key, nonce)
    assert result["status"] == "uncertain"
    assert params["position_identifier"] in services.execution.broker._positions


async def test_foreign_ticket_identifier_has_no_owner_capture(services):
    await open_one(services.execution)
    with pytest.raises(OwnerInterfaceError, match="no_matching_owned"):
        await services.action(
            actor(services), "close_position", {"ticket": 999, "position_identifier": 999}, str(uuid4())
        )
    assert len(await services.execution.broker.get_positions()) == 1


async def test_close_all_only_captures_existing_owned_and_never_claims_flat(services):
    await open_one(services.execution)
    who = actor(services)
    key = str(uuid4())
    prepared = await services.action(who, "close_all", {}, key)
    assert services.database.status()["state"] == "running"  # preparation does not pause/close
    result = await services.action(who, "close_all", {}, key, prepared["confirmation_token"])
    assert result["status"] == "completed" and len(result["results"]) == 1
    assert result["remaining_owned_ledger"] == [] and not result["broker_account_flat_claimed"]
    assert services.database.status()["state"] == "paused"


async def test_empty_close_all_requires_no_fabricated_capture(services):
    with pytest.raises(OwnerInterfaceError):
        await services.action(actor(services), "close_all", {}, str(uuid4()))


async def test_uncertain_close_is_cached_and_not_blindly_retried(services, monkeypatch):
    who, params, key, nonce = await prepare_close(services)
    calls = 0

    async def uncertain(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise UncertainExecution("TEST_ONLY SECRET_MARKER remote acknowledgement lost")

    monkeypatch.setattr(services.execution, "close_owned", uncertain)
    first = await services.action(who, "close_position", params, key, nonce)
    second = await services.action(who, "close_position", params, key, nonce)
    assert calls == 1 and first["status"] == second["status"] == "uncertain"
    assert "SECRET_MARKER" not in str(first)
    assert services.database.status()["state"] == "paused"


async def test_durable_close_key_cannot_change_expected_capture(services):
    await open_one(services.execution)
    pos = (await services.execution.capture_owned_positions())[0]
    key = "d" * 64
    result = await services.execution.close_owned(
        pos.ticket, pos.identifier, idempotency_key=key, expected_position_hash=position_review_hash(pos)
    )
    assert result.status == ResultStatus.FILLED
    with pytest.raises(TradingDisabled, match="payload"):
        await services.execution.close_owned(
            pos.ticket, pos.identifier, idempotency_key=key, expected_position_hash="a" * 64
        )
