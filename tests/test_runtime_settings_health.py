"""Defaults, code identity and bounded health; synthetic/offline only."""

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import RuntimeHealth, pending_native_calls
from core.settings import Settings
from trading import risk_types
from trading.risk_types import source_code_hash


def cfg(tmp_path, **values):
    return Settings(_env_file=None, project_root=tmp_path, **values)


def test_runtime_safest_defaults(tmp_path):
    s = cfg(tmp_path)
    assert s.demo_mode and s.paper_trading and not s.live_trading
    assert s.mt5_backend == "mock" and s.watchdog_interval_seconds == 30
    assert not s.runtime_learning_enabled and not s.autonomous_demo
    assert s.heartbeat_interval_seconds * 3 <= s.runtime_lease_seconds
    assert s.runtime_shutdown_seconds >= s.mt5_api_timeout_seconds


@pytest.mark.parametrize(
    "values",
    [
        {"runtime_learning_hour_utc": -1},
        {"runtime_learning_hour_utc": 24},
        {"runtime_report_hour_utc": 24},
        {"runtime_backup_hour_utc": 24},
        {"runtime_backup_keep": 0},
        {"runtime_backup_keep": 31},
        {"runtime_notifications_seconds": 1},
        {"runtime_job_max_seconds": 10},
        {"runtime_job_max_seconds": 301},
        {"runtime_shutdown_seconds": 601},
        {"runtime_shutdown_seconds": 30},
        {"watchdog_startup_grace_seconds": 10},
        {"heartbeat_interval_seconds": 30, "runtime_lease_seconds": 60},
        {"position_interval_seconds": 30},
    ],
)
def test_invalid_runtime_cadence_or_bounds(tmp_path, values):
    with pytest.raises(ValidationError):
        cfg(tmp_path, **values)


@pytest.mark.parametrize(
    "field,path",
    [
        ("runtime_health_file", "../outside.json"),
        ("runtime_lock_file", "../runtime.lock"),
        ("watchdog_lock_file", "../watchdog.lock"),
        ("runtime_health_file", ".env"),
        ("runtime_lock_file", "core/settings.py"),
        ("runtime_health_file", "data/runtime/x.txt"),
        ("runtime_lock_file", "data/runtime/x.json"),
        ("watchdog_lock_file", "data/runtime/x.json"),
        ("runtime_health_file", "data/health.json"),
        ("runtime_health_file", "data/runtime/operator-stop.json"),
        ("runtime_health_file", "data/runtime/stop-invalid.json"),
    ],
)
def test_control_paths_cannot_overwrite_source_secrets_or_other_dirs(tmp_path, field, path):
    with pytest.raises(ValidationError):
        cfg(tmp_path, **{field: path})


def test_os_locks_are_distinct_and_custom_paths_must_be_explicit(tmp_path):
    with pytest.raises(ValidationError):
        cfg(tmp_path, watchdog_lock_file="data/runtime/runtime.lock")
    s = cfg(
        tmp_path,
        data_dir="private",
        runtime_health_file="private/runtime/state.json",
        runtime_lock_file="private/runtime/child.lock",
        watchdog_lock_file="private/runtime/parent.lock",
    )
    assert s.resolve_path(s.runtime_health_file).parent == tmp_path / "private/runtime"


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_health_file", "data/runtime/state.json"),
        ("runtime_lock_file", "data/runtime/child.lock"),
        ("watchdog_lock_file", "data/runtime/parent.lock"),
    ],
)
def test_local_file_names_are_infrastructure_not_trading_policy(tmp_path, field, value):
    assert cfg(tmp_path).safety_fingerprint() == cfg(tmp_path, **{field: value}).safety_fingerprint()


@pytest.mark.parametrize(
    "name",
    [
        "watchdog.py",
        "launcher.py",
        "scripts/run_runtime.py",
        "app/bot.py",
        "scripts/run_bot_hidden.vbs",
        "scripts/task_scheduler_setup.ps1",
    ],
)
def test_all_runtime_launch_sources_are_code_bound(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# first\n")
    before = source_code_hash(tmp_path)
    path.write_text("# second\n")
    assert source_code_hash(tmp_path) != before


def test_frozen_executable_has_distinct_evidence_identity(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("# test only")
    exe = tmp_path / "fixture.exe"
    exe.write_bytes(b"not an actual executable")
    source = source_code_hash(tmp_path)
    monkeypatch.setattr(risk_types.sys, "frozen", True, raising=False)
    monkeypatch.setattr(risk_types.sys, "executable", str(exe))
    frozen = source_code_hash(tmp_path)
    assert frozen != source
    exe.write_bytes(b"another test-only byte string")
    assert source_code_hash(tmp_path) != frozen


def test_health_has_no_sensitive_config_and_overdue_uses_monotonic(tmp_path):
    now = [0.0]
    s = cfg(tmp_path)
    h = RuntimeHealth(s, str(uuid4()), monotonic=lambda: now[0])
    h.job_started("positions")
    now[0] = 121.0
    assert h.overdue() == ["positions"]
    data = h.write()
    assert data["overdue_jobs"] == ["positions"] and data["control"] == "paused"
    assert data["not_live_authorization"] and data["status"] == "starting"
    assert not {"account", "account_key", "token", "password", "positions"}.intersection(data)
    h.job_finished("positions", success=True)
    assert not h.overdue()
    h.state = "ready"
    h.job_started("signals")
    h.job_finished("signals", success=False)
    assert h.write()["status"] == "degraded"
    h.job_finished("signals", success=True)
    assert h.write()["status"] == "ready"


def test_pending_call_health_checks_underlying_market():
    class Market:
        def health(self):
            return {"pending_calls": 2}

    class Paper:
        market = Market()

        def health(self):
            return {"connected": True}

    assert pending_native_calls(Paper()) == 2
    assert pending_native_calls(object()) == 0


def test_runtime_modules_import_without_artifacts(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    code = (
        'import app.bot, app.lifecycle, app.scheduler, app.dependencies, watchdog, launcher; print("inert")'
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0 and result.stdout.strip() == "inert"
    assert not list(tmp_path.iterdir())
