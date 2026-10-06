"""Real synthetic resource jobs; no real provider/native SDK/network."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.health import RuntimeHealth
from app.process_guard import request_operator_stop
from app.scheduler import RuntimeScheduler
from core.models import AuditLog, BotState, OrderIntent
from tests.risk_helpers import OWNER, open_one
from tests.runtime_helpers import close_runtime, runtime
from tests.signal_helpers import approved, make_signal_runtime


@pytest.fixture
async def system(tmp_path):
    r, h, jobs = await runtime(tmp_path)
    yield r, h, jobs
    await close_runtime(r, jobs)


async def test_composed_runtime_shares_actual_guarded_services(system):
    r, h, jobs = system
    assert r.engine.broker is r.broker and r.positions.engine is r.engine
    assert r.supervisor.suggestions.database is r.database
    assert r.engine.profile == r.signals.profile == r.supervisor.profile == r.news.profile
    assert r.broker.clock is r.engine.clock
    assert r.database.status()["state"] == "paused"
    assert (await jobs.run_job("signals"))["executed"] == 0
    assert r.reporter is not None and not r.reporter.enabled


@pytest.mark.parametrize("control", ["paused", "killed"])
async def test_protective_monitoring_runs_while_entries_denied(system, control):
    r, h, jobs = system
    await open_one(r.engine)
    getattr(r.engine.control, "kill" if control == "killed" else "pause")(OWNER)
    r.signals.evaluate = AsyncMock()
    assert (await jobs.run_job("signals"))["state"] == "paused"
    assert not r.signals.evaluate.called
    result = await jobs.run_job("positions")
    assert result["managed_positions"] == 1
    assert r.database.status()["state"] == control


async def test_unknown_news_and_disabled_ai_never_become_orders(system):
    r, h, jobs = system
    r.engine.control.resume(OWNER, account_key=r.engine.account_key)
    await jobs.run_job("news")
    result = await jobs.run_job("signals")
    assert result["state"] == "evaluated"
    with r.database.session() as session:
        assert session.scalar(select(OrderIntent.id)) is None


async def test_persistent_operator_stop_blocks_entry_job_even_before_monitor(system):
    r, h, jobs = system
    r.engine.control.resume(OWNER, account_key=r.engine.account_key)
    request_operator_stop(r.settings)
    r.signals.evaluate = AsyncMock()
    assert not await jobs.entries_permitted()
    assert (await jobs.run_job("signals"))["executed"] == 0
    r.signals.evaluate.assert_not_awaited()


async def test_job_nonoverlap_and_shutdown_drain_never_cancel_effect(system):
    r, h, jobs = system
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()
        return {"completed": True}

    jobs.jobs["positions"] = blocked
    task = asyncio.create_task(jobs.run_job("positions"))
    await entered.wait()
    assert (await jobs.run_job("positions"))["state"] == "skipped"
    jobs.stop_accepting()
    assert not await jobs.drain(0.01)
    assert not task.done() and not task.cancelled()
    with pytest.raises(RuntimeError, match="drain"):
        jobs.finish()
    release.set()
    assert (await task)["completed"]
    assert await jobs.drain(0.01)
    assert (await jobs.run_job("positions"))["state"] == "skipped"


@pytest.mark.parametrize("name", ["heartbeat", "positions", "signals", "position_reviews"])
async def test_critical_failure_halts_entries_with_fixed_reason(system, name):
    r, h, jobs = system
    r.engine.control.resume(OWNER, account_key=r.engine.account_key)

    async def fail():
        raise RuntimeError("DO_NOT_LOG_PROVIDER_SECRET")

    jobs.jobs[name] = fail
    assert (await jobs.run_job(name))["state"] == "failed"
    with r.database.session() as session:
        state = session.get(BotState, 1)
        assert state.desired_state == "paused" and state.last_error == "broker_unstable"
    assert h.jobs[name]["success"] is False
    with r.database.session() as session:
        assert "DO_NOT_LOG_PROVIDER_SECRET" not in str(
            [row.details for row in session.scalars(select(AuditLog)).all()]
        )


@pytest.mark.parametrize(
    "name", ["news", "news_advisory", "notifications", "daily_report", "learning", "backup"]
)
async def test_advisory_failure_is_not_risk_reset_or_auto_resume(system, name):
    r, h, jobs = system

    async def fail():
        raise ValueError("DO_NOT_LOG_SECRET")

    jobs.jobs[name] = fail
    assert (await jobs.run_job(name))["state"] == "failed"
    assert r.database.status()["state"] == "paused"
    assert h.jobs[name]["success"] is False


async def test_unexpected_cancellation_persists_unknown_without_retry(system):
    r, h, jobs = system
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    jobs.jobs["positions"] = blocked
    task = asyncio.create_task(jobs.run_job("positions"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with r.database.session() as session:
        assert session.get(BotState, 1).last_error == "unknown_execution"
    assert not jobs.active and not h.jobs["positions"]["active"]


async def test_overdue_monotonic_job_halts_but_does_not_cancel(system):
    r, h, jobs = system
    now = [0.0]
    h.monotonic = lambda: now[0]
    h.job_started("positions")
    now[0] = 121.0
    await jobs.run_job("heartbeat")
    with r.database.session() as session:
        assert session.get(BotState, 1).last_error == "risk_observation_gap"
    assert h.jobs["positions"]["active"]
    h.job_finished("positions", success=True)


async def test_daily_report_is_bounded_and_read_advisory_only(system):
    r, h, jobs = system
    r.supervisor.daily_report = AsyncMock(return_value={"closed": 0})
    result = await jobs.run_job("daily_report")
    assert result == {"closed": 0}
    r.supervisor.daily_report.assert_awaited_once_with(
        r.engine.account_key, start=r.broker.clock.now() - timedelta(days=1)
    )
    assert r.database.status()["state"] == "paused"


async def test_disabled_reporter_still_persists_local_reports_without_delivery(system):
    r, h, jobs = system
    r.notices.enqueue("started", dedup="test")
    result = await jobs.run_job("notifications")
    assert not r.reporter.enabled and result["runtime"]["disabled"] == 1
    assert (r.reporter.reports_dir / "actions.log").is_file()
    with r.database.session() as session:
        assert (
            session.scalar(select(AuditLog.id).where(AuditLog.action == "runtime.notice_attempted")) is None
        )


async def test_disabled_optional_jobs_and_unknown_name(system):
    r, h, jobs = system
    for name in ("learning", "backup", "position_reviews"):
        assert (await jobs.run_job(name))["state"] == "disabled"
    with pytest.raises(ValueError):
        await jobs.run_job("resume")


@pytest.mark.parametrize("state", ["paused", "running", "killed"])
async def test_learning_requires_paused_flat_and_never_promotes(tmp_path, state):
    r, h, jobs = await runtime(tmp_path, runtime_learning_enabled=True)
    try:
        if state == "running":
            r.engine.control.resume(OWNER, account_key=r.engine.account_key)
        elif state == "killed":
            r.engine.control.kill(OWNER)
        r.supervisor.learning_cycle = AsyncMock(
            return_value={"state": "candidate", "model_id": 10, "active": False}
        )
        result = await jobs.run_job("learning")
        if state == "paused":
            assert result["active"] is False
            r.supervisor.learning_cycle.assert_awaited_once()
        else:
            assert result["state"] == "skipped"
            r.supervisor.learning_cycle.assert_not_awaited()
        assert r.database.status()["state"] == state
    finally:
        await close_runtime(r, jobs)


async def test_learning_skips_owned_exposure(tmp_path):
    r, h, jobs = await runtime(tmp_path, runtime_learning_enabled=True)
    try:
        await open_one(r.engine)
        r.engine.control.pause(OWNER)
        r.supervisor.learning_cycle = AsyncMock()
        assert (await jobs.run_job("learning"))["state"] == "skipped"
        r.supervisor.learning_cycle.assert_not_awaited()
    finally:
        await close_runtime(r, jobs)


@pytest.mark.parametrize("sign", [1, -1])
async def test_scheduler_uses_real_approved_signal_bridge_idempotently(tmp_path, sign):
    signals, engine = await make_signal_runtime(tmp_path, sign=sign)
    try:
        ready = await approved(signals)
        resources = NS(
            settings=engine.settings,
            engine=engine,
            database=engine.database,
            signals=signals,
            news=NS(window=AsyncMock()),
            supervisor=object(),
        )
        health = RuntimeHealth(engine.settings, str(uuid4()))
        jobs = RuntimeScheduler(resources, health)
        signals.evaluate = AsyncMock(return_value=ready)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await jobs.run_job("signals")
        await jobs.run_job("signals")
        with engine.database.session() as session:
            intents = session.scalars(select(OrderIntent)).all()
            assert len(intents) == 1 and intents[0].state == "reconciled"
        assert len(await engine.broker.get_positions()) == 1
        assert signals.evaluate.await_count == 2
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_coalescing_and_max_instances_on_all_registered_jobs(system):
    r, h, jobs = system
    jobs.start()
    try:
        scheduled = jobs.scheduler.get_jobs()
        assert {job.id for job in scheduled} == {
            "heartbeat",
            "positions",
            "signals",
            "news",
            "notifications",
            "news_advisory",
            "position_reviews",
            "daily_report",
            "ai_learning",
            "ai_config_review",
            "ai_nightly_review",
            "ai_status",
            "ai_attribution",
            "trade_audit",
            "alerts",
        }
        assert all(job.max_instances == 1 and job.coalesce for job in scheduled)
        assert str(jobs.scheduler.timezone) == "UTC"
        assert str(jobs.scheduler.get_job("position_reviews").trigger.interval) == "0:05:00"
        assert str(jobs.scheduler.get_job("ai_learning").trigger.interval) == "0:05:00"
        assert str(jobs.scheduler.get_job("ai_config_review").trigger.interval) == "0:30:00"
        assert str(jobs.scheduler.get_job("ai_status").trigger.interval) == "0:01:00"
        assert str(jobs.scheduler.get_job("alerts").trigger.interval) == "0:00:05"
        assert str(jobs.scheduler.get_job("ai_attribution").trigger.interval) == "0:01:00"
    finally:
        jobs.stop_accepting()
        jobs.finish()
        await asyncio.sleep(0)
