import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import BotState, RiskState
from tests.risk_helpers import BAD_OPERATOR, MOMENT, OWNER, config, make_engine, open_one
from trading.runtime_state import RuntimeControl
from trading.types import ManualClock, TradingDisabled


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    yield result
    await result.shutdown()
    result.database.close()


def test_new_runtime_is_paused_not_a_local_operator_approval(engine):
    assert engine.database.status()["state"] == "paused"
    assert engine.control.session_id is not None


@pytest.mark.parametrize("operator", [None, 0, 43, True, 42.0, "42", BAD_OPERATOR])
def test_resume_requires_current_local_operator(engine, operator):
    with pytest.raises(TradingDisabled):
        engine.control.resume(operator, account_key=engine.account_key)
    assert engine.database.status()["state"] == "paused"


def test_noncurrent_local_operator_is_deny_all(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    control = RuntimeControl(db, cfg, ManualClock(MOMENT))
    try:
        control.claim()
        with pytest.raises(TradingDisabled):
            control.pause(BAD_OPERATOR)
    finally:
        control.release()
        db.close()


async def test_unexpired_cross_process_lease_cannot_be_taken(engine):
    other = Database(engine.settings)
    try:
        contender = RuntimeControl(other, engine.settings, engine.clock)
        with pytest.raises(TradingDisabled, match="another runtime"):
            await asyncio.to_thread(contender.claim)
    finally:
        other.close()


async def test_expired_lease_creates_new_paused_session_and_old_cannot_renew(engine):
    first = engine.control.session_id
    engine.clock.advance(timedelta(seconds=engine.settings.runtime_lease_seconds))
    other = RuntimeControl(engine.database, engine.settings, engine.clock)
    second = other.claim()
    assert first != second and engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.heartbeat()
    other.release()


async def test_shutdown_restart_is_paused_even_if_previously_running(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    before = engine.control.session_id
    engine.control.release()
    engine.control.claim()
    assert engine.control.session_id != before
    assert engine.database.status()["state"] == "paused"


async def test_kill_persists_and_requires_separate_explicit_reset(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    engine.control.kill(OWNER)
    engine.control.release()
    engine.control.claim()
    assert engine.database.status()["kill_switch"]
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.reset_kill(
            OWNER, account_key=engine.account_key, confirm="yes", broker_writes_quarantined=False
        )
    engine.control.reset_kill(
        OWNER,
        account_key=engine.account_key,
        confirm="RESET_KILL_AND_KEEP_PAUSED",
        broker_writes_quarantined=False,
    )
    assert not engine.database.status()["kill_switch"] and engine.database.status()["state"] == "paused"


@pytest.mark.parametrize("field", ["daily_loss_latched", "drawdown_latched"])
def test_owner_resume_never_erases_loss_latches(engine, field):
    with engine.database.session() as session:
        setattr(session.scalar(select(RiskState)), field, True)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with engine.database.session() as session:
        assert getattr(session.scalar(select(RiskState)), field)


def test_unstable_broker_halt_is_durable(engine):
    engine.control.halt("broker_unstable", account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.acknowledge_recovery(
            OWNER, account_key=engine.account_key, broker_writes_quarantined=True
        )
    engine.control.acknowledge_recovery(
        OWNER, account_key=engine.account_key, broker_writes_quarantined=False
    )
    assert engine.database.status()["state"] == "paused"
    engine.control.resume(OWNER, account_key=engine.account_key)


def test_flat_baseline_review_cannot_reset_loss_flags(engine):
    with engine.database.session() as session:
        row = session.scalar(select(RiskState))
        row.metadata_json = {
            **row.metadata_json,
            "baseline_verified": False,
            "migration_review_required": True,
        }
    engine.control.review_flat_baseline(
        OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
    )
    with engine.database.session() as session:
        row = session.scalar(select(RiskState))
        assert row.metadata_json["baseline_verified"] and row.metadata_json["sampled_peak_only"]
        row.drawdown_latched = True
    with pytest.raises(TradingDisabled):
        engine.control.review_flat_baseline(
            OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
        )


async def test_baseline_review_is_denied_with_open_exposure(engine):
    await open_one(engine)
    with pytest.raises(TradingDisabled):
        engine.control.review_flat_baseline(
            OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
        )


async def test_stale_risk_observation_prevents_resume(engine):
    engine.clock.advance(timedelta(seconds=engine.settings.risk_observation_max_age_seconds + 1))
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)


def test_halt_accepts_identifiers_not_untrusted_sdk_error_text(engine):
    with pytest.raises(ValueError):
        engine.control.halt("password=secret")


async def test_pause_does_not_lower_kill(engine):
    engine.control.kill(OWNER)
    engine.control.pause(OWNER)
    with engine.database.session() as session:
        state = session.get(BotState, 1)
        assert state.kill_switch_active and state.desired_state == "killed"
