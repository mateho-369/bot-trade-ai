"""Offline tests for gated AUTONOMOUS_DEMO recovery and critical-pause throttling."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.models import AuditLog, OrderIntent, RiskState
from tests.risk_helpers import OWNER, make_engine
from trading.runtime_state import RuntimeControl


def _resume(engine, *, trigger="retry", components=True, ai=True):
    return engine.control.auto_resume(
        account_key=engine.account_key,
        components_ready=components,
        ai_healthy=ai,
        trigger=trigger,
    )


async def _refresh_risk_after_clock_advance(engine):
    account = await engine.broker.get_account_info()
    positions = await engine.broker.get_positions()
    with engine.database.locked_session() as session:
        engine.authority.risk.observe(session, account, positions)


async def _cooldown_with_heartbeats(engine):
    for _ in range(45):
        engine.clock.advance(timedelta(seconds=20))
        engine.control.heartbeat()
    await _refresh_risk_after_clock_advance(engine)


async def test_startup_auto_resume_waits_for_all_components_and_successful_ai_health(tmp_path):
    engine = await make_engine(tmp_path, autonomous_demo=True)
    try:
        assert engine.database.status()["state"] == "paused"
        assert not _resume(engine, trigger="startup", components=False, ai=True)
        assert not _resume(engine, trigger="signals_ready", components=True, ai=False)
        assert engine.database.status()["state"] == "paused"
        assert _resume(engine, trigger="ai_ready", components=True, ai=True)
        assert engine.database.status()["state"] == "running"
        with engine.database.session() as session:
            rows = session.scalars(select(AuditLog).where(AuditLog.action == "runtime.auto_resumed")).all()
            assert len(rows) == 1 and rows[0].details["trigger"] == "ai_ready"
    finally:
        await engine.shutdown()
        engine.database.close()


@pytest.mark.parametrize(
    "gate",
    ["kill", "halt", "baseline", "loss_latch", "drawdown_latch", "unsettled", "non_demo_account"],
)
async def test_auto_resume_never_overrides_safety_latches_or_unverified_state(tmp_path, gate):
    engine = await make_engine(tmp_path, autonomous_demo=True)
    try:
        if gate == "kill":
            engine.control.kill(OWNER)
        elif gate == "halt":
            engine.control.halt("broker_unstable", account_key=engine.account_key)
        elif gate == "unsettled":
            with engine.database.locked_session() as session:
                session.add(
                    OrderIntent(
                        id=str(uuid4()),
                        idempotency_key="a" * 64,
                        time=engine.clock.now(),
                        updated_at=engine.clock.now(),
                        expires_at=engine.clock.now() + timedelta(minutes=5),
                        mode=engine.settings.mode.value,
                        account_key=engine.account_key,
                        symbol="EURUSD",
                        direction="buy",
                        state="unknown",
                        request={"operation": "open"},
                        config_hash=engine.settings.safety_fingerprint(),
                    )
                )
        else:
            with engine.database.locked_session() as session:
                risk = session.scalar(select(RiskState).where(RiskState.account_key == engine.account_key))
                if gate == "baseline":
                    risk.metadata_json = {**risk.metadata_json, "baseline_verified": False}
                elif gate == "loss_latch":
                    risk.daily_loss_latched = True
                elif gate == "drawdown_latch":
                    risk.drawdown_latched = True
                elif gate == "non_demo_account":
                    risk.metadata_json = {**risk.metadata_json, "account_kind": "real"}

        assert not _resume(engine, trigger="retry")
        status = engine.database.status()
        assert status["state"] in {"paused", "killed"}
        with engine.database.session() as session:
            assert not session.scalars(
                select(AuditLog).where(AuditLog.action == "runtime.auto_resumed")
            ).all()
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_local_manual_pause_cannot_be_reversed_by_autonomous_retry(tmp_path):
    engine = await make_engine(tmp_path, autonomous_demo=True)
    try:
        assert _resume(engine, trigger="startup")
        engine.control.pause(OWNER)
        # Unrelated/high-volume audit traffic cannot mask the latest local pause.
        for index in range(40):
            engine.database.audit("test.unrelated", "test", {"index": index})
        assert not _resume(engine, trigger="retry")
        assert engine.database.status()["state"] == "paused"
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_critical_pause_has_cooldown_and_three_resume_daily_cap(tmp_path):
    engine = await make_engine(tmp_path, autonomous_demo=True, auto_resume_max_per_day=3)
    try:
        for index in range(3):
            assert _resume(engine, trigger="startup" if index == 0 else "retry")
            assert engine.control.auto_pause("critical_alert", account_key=engine.account_key)
            assert not _resume(engine, trigger="retry")  # 15-minute critical-alert cooldown.
            if index < 2:
                await _cooldown_with_heartbeats(engine)
        # The cap is durable across pauses/retries for this UTC runtime day.
        assert not _resume(engine, trigger="retry")
        assert engine.database.status()["state"] == "paused"
        with engine.database.session() as session:
            resumes = session.scalars(select(AuditLog).where(AuditLog.action == "runtime.auto_resumed")).all()
            assert len(resumes) == 3
            assert all(
                row.details["reason"] in {"startup_or_retry", "critical_alert_recovery"} for row in resumes
            )

        # A fresh process on the next runtime day clears only the daily counter; entries still start paused.
        engine.control.release()
        engine.clock.advance(timedelta(days=1))
        next_runtime = RuntimeControl(engine.database, engine.settings, engine.clock)
        next_runtime.claim()
        await _refresh_risk_after_clock_advance(engine)
        assert next_runtime.auto_resume(
            account_key=engine.account_key,
            components_ready=True,
            ai_healthy=True,
            trigger="retry",
        )
        assert engine.database.status()["state"] == "running"
        next_runtime.release()
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_invalid_autonomous_trigger_is_not_silently_accepted(tmp_path):
    engine = await make_engine(tmp_path, autonomous_demo=True)
    try:
        with pytest.raises(ValueError, match="unknown autonomous resume trigger"):
            _resume(engine, trigger="telegram_resume")
    finally:
        await engine.shutdown()
        engine.database.close()
