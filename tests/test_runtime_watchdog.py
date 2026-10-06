"""Watchdog algorithm with scripted child handles: no process/network deployment."""

import os
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.process_guard import ProcessLock, atomic_json, request_operator_stop, stop_path
from core.database import Database
from core.models import AuditLog, BotState
from tests.risk_helpers import MOMENT, config
from trading.types import ManualClock
from watchdog import Watchdog, child_command


class FakeChild:
    def __init__(self):
        self.pid, self.exit = os.getpid(), None

    def poll(self):
        return self.exit

    def wait(self):
        assert self.exit is not None, "TEST_ONLY script must not wait for a real process"
        return self.exit


@pytest.fixture
def system(tmp_path):
    s = config(tmp_path, runtime_telegram_enabled=False)
    d = Database(s)
    d.initialize()
    env = tmp_path / ".env"
    env.write_text("# TEST_ONLY no credentials and never launch a real child\n")
    now, children, commands = [0.0], [], []

    def popen(command, **kwargs):
        commands.append((command, kwargs))
        child = FakeChild()
        children.append(child)
        return child

    clock = ManualClock(MOMENT)
    w = Watchdog(s, d, env, popen=popen, monotonic=lambda: now[0], clock=clock)
    yield NS(w=w, s=s, d=d, env=env, now=now, children=children, commands=commands, clock=clock)
    d.close()


def health(system, **changes):
    w = system.w
    data = {
        "schema": 1,
        "pid": w.child.pid,
        "process_created": w.created,
        "managed_id": w.identity,
        "updated_at": system.clock.now().isoformat(),
        "status": "ready",
        "control": "paused",
        "session_id": str(uuid4()),
        "config_hash": w.settings.safety_fingerprint(),
        "code_hash": w.code_hash,
        "model_sha256": "a" * 64,
        "source": "synthetic",
        "writes_quarantined": False,
        "pending_native_calls": 0,
        "overdue_jobs": [],
        "failed_jobs": [],
        "jobs": {},
        "not_live_authorization": True,
    }
    data.update(changes)
    atomic_json(system.s.resolve_path(system.s.runtime_health_file), data)
    return data


def test_launch_is_shell_free_direct_child_and_never_resume(system):
    w = system.w
    assert w.tick() == "launched_paused"
    command, kwargs = system.commands[0]
    assert command[-2] == "--managed-id" and command[-1] == w.identity
    assert kwargs["shell"] is False and kwargs["cwd"] == system.s.project_root
    assert "--env-file" in command and "--resume" not in command and "--live" not in command
    assert len(system.children) == 1 and system.d.status()["state"] == "paused"


def test_health_from_spawned_interpreter_descendant_accepted_unrelated_refused(system):
    # Windows venv launchers spawn the real interpreter as a child of the spawned
    # process: Popen.pid != os.getpid() inside the runtime. The health file then
    # carries the interpreter pid; the supervisor must still bind it to this launch.
    import subprocess
    import sys

    import psutil

    w = system.w
    w.tick()
    assert w.child is not None
    interpreter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        assert interpreter.pid != w.child.pid
        assert w.child.pid in (p.pid for p in psutil.Process(interpreter.pid).parents())
        health(system, pid=interpreter.pid, process_created=psutil.Process(interpreter.pid).create_time())
        assert w.health_ok() is True
        # An unrelated live process (not a descendant of the spawned pid) stays refused.
        unrelated = psutil.Process(os.getpid()).ppid()
        health(system, pid=unrelated, process_created=psutil.Process(unrelated).create_time())
        assert w.health_ok() is False
    finally:
        interpreter.kill()
        interpreter.wait()


def test_startup_grace_is_bounded_not_a_restart_permission(system):
    w = system.w
    w.tick()
    assert w.tick() == "startup_grace"
    system.now[0] = 241
    assert w.tick() == "stop_requested_replacement_withheld"
    assert len(system.children) == 1 and stop_path(system.s, w.identity).is_file()
    assert w.tick() == "stop_requested_replacement_withheld"


def test_healthy_child_is_never_replaced(system):
    w = system.w
    w.tick()
    health(system)
    for _ in range(5):
        assert w.tick() == "healthy"
    assert len(system.children) == 1


def test_stale_previously_ready_child_gets_no_extra_startup_grace(system):
    w = system.w
    w.tick()
    health(system)
    assert w.tick() == "healthy"
    system.now[0] = 100
    system.clock.advance(timedelta(seconds=91))
    assert w.tick() == "stop_requested_replacement_withheld"
    assert len(system.children) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"pid": 0},
        {"pid": True},
        {"managed_id": str(uuid4())},
        {"process_created": -1},
        {"config_hash": "b" * 64},
        {"code_hash": "b" * 64},
        {"not_live_authorization": False},
        {"schema": 2},
        {"updated_at": "bad"},
        {"updated_at": "2026-10-03T12:00:00"},
        {"updated_at": (MOMENT + timedelta(seconds=6)).isoformat()},
        {"status": "stopped"},
        {"status": "stopping"},
        {"writes_quarantined": True},
        {"overdue_jobs": ["positions"]},
    ],
)
def test_invalid_or_unready_evidence_never_kills_or_replaces_alive_child(system, changes):
    w = system.w
    w.tick()
    system.now[0] = 241
    health(system, **changes)
    assert w.tick() == "stop_requested_replacement_withheld"
    assert len(system.children) == 1 and w.child.poll() is None


def test_confirmed_exit_is_required_before_replacement_and_notice(system):
    w = system.w
    w.tick()
    first = w.identity
    system.children[0].exit = 1
    assert w.tick() == "launched_paused"
    assert w.identity != first and len(system.children) == 2
    with system.d.session() as db:
        queued = db.scalars(select(AuditLog).where(AuditLog.action == "runtime.notice_pending")).all()
        assert any(row.details["kind"] == "restart" for row in queued)
        exits = db.scalars(select(AuditLog).where(AuditLog.action == "watchdog.child_exited")).all()
        assert len(exits) == 1 and exits[0].details["exit_confirmed"]


def test_restart_budget_is_persisted_across_new_supervisor_objects(system):
    w = system.w
    for _ in range(3):
        assert w.tick() == "launched_paused"
        w.child.exit = 1
    assert w.tick() == "budget_exhausted"
    restarted = Watchdog(
        system.s, system.d, system.env, popen=w.popen, monotonic=w.monotonic, clock=system.clock
    )
    assert restarted.tick() == "budget_exhausted"
    assert len(system.children) == 3
    system.clock.advance(timedelta(hours=1, seconds=1))
    assert restarted.tick() == "launched_paused"


def test_other_live_runtime_os_lock_is_not_adopted_or_killed(system):
    with ProcessLock(system.s.resolve_path(system.s.runtime_lock_file)):
        assert system.w.tick() == "waiting_for_runtime_lock_or_lease"
        assert not system.children
    assert system.w.tick() == "launched_paused"


@pytest.mark.parametrize("age", [0, 89, -1])
def test_live_sql_lease_blocks_launch_even_if_no_local_handle(system, age):
    with system.d.locked_session() as db:
        row = db.get(BotState, 1)
        row.session_id = str(uuid4())
        row.heartbeat = system.clock.now() - timedelta(seconds=age)
    assert system.w.tick() == "waiting_for_runtime_lock_or_lease"
    assert not system.children


def test_expired_lease_is_only_available_when_os_lock_free(system):
    with system.d.locked_session() as db:
        row = db.get(BotState, 1)
        row.session_id, row.heartbeat = str(uuid4()), system.clock.now() - timedelta(seconds=91)
        row.kill_switch_active, row.desired_state = True, "killed"
        row.last_error = "unknown_execution"
    assert system.w.tick() == "launched_paused"
    with system.d.session() as db:
        state = db.get(BotState, 1)
        assert (
            state.kill_switch_active
            and state.last_error == "unknown_execution"
            and state.desired_state == "killed"
        )


@pytest.mark.parametrize("change", ["env", "code"])
def test_changed_files_stop_supervisor_not_silent_rebind(system, change):
    w = system.w
    w.tick()
    if change == "env":
        system.env.write_text("# changed TEST_ONLY")
    else:
        (system.s.project_root / "watchdog.py").write_text("# changed TEST_ONLY")
    assert w.tick() == "configuration_or_code_changed_stop_supervisor"
    assert w.stopping.is_set() and len(system.children) == 1
    assert stop_path(system.s, w.identity).is_file()


def test_operator_stop_is_persistent_and_no_launch_follows(system):
    request_operator_stop(system.s)
    assert system.w.tick() == "stopping"
    assert not system.children


def test_operator_stop_of_existing_child_never_restarts(system):
    system.w.tick()
    request_operator_stop(system.s)
    assert system.w.tick() == "stopping"
    system.w.child.exit = 0
    assert system.w.tick() == "stopping" and len(system.children) == 1


def test_reservation_occurs_before_spawn_even_when_spawn_fails(system):
    def fail(*args, **kwargs):
        with system.d.session() as db:
            assert db.scalar(select(AuditLog.id).where(AuditLog.action == "watchdog.launch_reserved"))
        raise OSError("TEST_ONLY spawn failed")

    system.w.popen = fail
    with pytest.raises(OSError):
        system.w.launch()
    assert not system.children


def test_frozen_child_uses_same_executable_not_a_python_shell(system, monkeypatch):
    import watchdog

    monkeypatch.setattr(watchdog.sys, "frozen", True, raising=False)
    command = child_command(system.env, str(uuid4()))
    assert command[1] == "--runtime-child" and "-m" not in command
