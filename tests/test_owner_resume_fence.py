"""Owner-confirmed resume can never erase a later downward control revision."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_identity import OwnerInterfaceError
from core.models import BotState, OrderIntent, OwnerApproval, RiskState
from tests.owner_helpers import actor, owner_runtime
from tests.risk_helpers import OWNER
from trading.types import TradingDisabled


@pytest.fixture
async def services(tmp_path):
    value = await owner_runtime(tmp_path)
    yield value
    await value.execution.shutdown()
    value.database.close()


async def prepared(services):
    who = actor(services)
    key = str(uuid4())
    q = await services.action(who, "resume", {}, key)
    return who, key, q["confirmation_token"]


async def test_resume_requires_fresh_confirmation_then_exact_revision(services):
    who, key, nonce = await prepared(services)
    assert services.database.status()["state"] == "paused"
    result = await services.action(who, "resume", {}, key, nonce)
    assert result["status"] == "completed" and services.database.status()["state"] == "running"
    assert (await services.action(who, "resume", {}, key, nonce))["replayed"]
    with services.database.session() as session:
        assert not session.scalar(select(OwnerApproval.id).where(OwnerApproval.purpose == "live_session"))
        assert not session.scalar(select(OrderIntent.id))


@pytest.mark.parametrize("later", ["pause", "kill", "heartbeat"])
async def test_later_control_change_rejects_before_nonce_effect_staging(services, later):
    who, key, nonce = await prepared(services)
    getattr(services.control, later)(OWNER) if later != "heartbeat" else services.control.heartbeat()
    with pytest.raises(OwnerInterfaceError, match="resume_state_changed"):
        await services.action(who, "resume", {}, key, nonce)
    assert services.database.status()["state"] == ("killed" if later == "kill" else "paused")


@pytest.mark.parametrize("later", ["pause", "kill"])
async def test_race_after_nonce_consume_fenced_inside_resume_transaction(services, monkeypatch, later):
    who, key, nonce = await prepared(services)
    original = services.control.resume

    def intervening(owner, **kwargs):
        getattr(services.control, later)(owner)
        return original(owner, **kwargs)

    monkeypatch.setattr(services.control, "resume", intervening)
    result = await services.action(who, "resume", {}, key, nonce)
    assert result["status"] == "rejected"
    assert services.database.status()["state"] == ("killed" if later == "kill" else "paused")
    assert (await services.action(who, "resume", {}, key, nonce))["replayed"]


@pytest.mark.parametrize(
    "gate", ["daily_loss", "drawdown", "kill", "last_error", "baseline", "continuity", "gap", "stale"]
)
async def test_existing_risk_recovery_latches_not_bypassed(services, gate):
    with services.database.locked_session() as session:
        risk = session.scalar(select(RiskState))
        state = session.get(BotState, 1)
        if gate == "daily_loss":
            risk.daily_loss_latched = True
        elif gate == "drawdown":
            risk.drawdown_latched = True
        elif gate == "kill":
            state.kill_switch_active = True
            state.desired_state = "killed"
        elif gate == "last_error":
            state.last_error = "unknown_execution"
        else:
            metadata = dict(risk.metadata_json)
            metadata[
                {
                    "baseline": "baseline_verified",
                    "continuity": "balance_continuity_verified",
                    "gap": "observation_gap",
                    "stale": "last_observed_at",
                }[gate]
            ] = (
                (services.clock.now() - timedelta(seconds=31)).isoformat()
                if gate == "stale"
                else gate == "gap"
            )
            risk.metadata_json = metadata
    who, key, nonce = await prepared(services)
    result = await services.action(who, "resume", {}, key, nonce)
    assert result["status"] == "rejected"
    assert services.database.status()["state"] != "running"


async def test_control_direct_revision_argument_is_strict_nonnegative(services):
    for value in [-1, True, "1", 1.0]:
        with pytest.raises(TradingDisabled):
            services.control.resume(
                OWNER, account_key=services.execution.account_key, expected_revision=value
            )


async def test_legacy_trusted_internal_resume_without_revision_is_preserved(services):
    services.control.resume(OWNER, account_key=services.execution.account_key)
    assert services.database.status()["state"] == "running"


async def test_expired_lease_requires_recomposition_not_auto_initialization(services):
    who, key, nonce = await prepared(services)
    services.clock.advance(timedelta(seconds=services.settings.runtime_lease_seconds))
    with pytest.raises(OwnerInterfaceError):
        await services.action(actor(services), "resume", {}, key, nonce)
    assert services.database.status()["state"] == "paused"
