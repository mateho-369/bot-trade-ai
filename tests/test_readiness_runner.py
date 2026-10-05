"""Offline full seams; monkeypatched source identity is TEST ONLY, not another install's readiness."""

import json
import socket
import subprocess
import sys
from collections import namedtuple

import pytest

from readiness.runner import preflight
from scripts.readiness import main as readiness_cli
from scripts.verify_release import main as verify_cli
from tests.readiness_helpers import inventory, source_tree, sqlite_fixture


@pytest.fixture
def source(tmp_path, monkeypatch):
    root, doc, anchor = source_tree(tmp_path)
    monkeypatch.setattr("readiness.runner.INSPECTOR_ROOT", root)  # TEST ONLY inspector identity seam.
    # Positive SOFTWARE fixtures must not depend on temp-disk pressure during the full suite.
    # The real 512 MiB floor stays intact; separate host tests exercise low/unknown capacity.
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("readiness.host.shutil.disk_usage", lambda _: Usage(2**31, 0, 2**31))
    return root, doc, anchor


def test_integrity_failure_skips_config_state_and_never_transports_or_initializes(source, monkeypatch):
    root, _, _ = source
    (root / "main.py").write_text("CHANGED")

    def never(*args, **kwargs):
        raise AssertionError("must not inspect config/state after integrity failure")

    monkeypatch.setattr("readiness.settings_inspection.inspect_settings", never)
    before = inventory(root)
    report = preflight(root, env_name=".env", inspect_sqlite_snapshot=True)
    assert report["overall"] == "blocked" and not report["observations"] and inventory(root) == before
    assert any(item["code"] == "config_and_state_inspection_skipped" for item in report["findings"])


def test_other_install_root_may_be_verified_but_not_use_wrong_loaded_settings(tmp_path):
    root, _, _ = source_tree(tmp_path)
    report = preflight(root, env_name=".env", inspect_sqlite_snapshot=True)
    assert report["integrity"]["integrity_verified"] and report["overall"] == "blocked"
    assert report["observations"] == {}
    assert any(item["code"] == "inspector_source_root_mismatch" for item in report["findings"])


def test_development_pass_is_not_stage_owner_live_or_native_approval(source, monkeypatch):
    root, _, anchor = source

    def never(*args, **kwargs):
        raise AssertionError("NO network/subprocess transport in offline diagnostic")

    monkeypatch.setattr(socket, "create_connection", never)
    monkeypatch.setattr(subprocess, "Popen", never)
    before = inventory(root)
    report = preflight(root, env_name=".env.example", trusted_manifest_sha256=anchor)
    assert report["overall"] == "offline_checks_passed" and inventory(root) == before
    for key in [
        "stage_evidence",
        "owner_authenticated",
        "trading_authorized",
        "native_validation_complete",
        "automatic_resume",
        "financial_history_reset",
        "broker_connected",
    ]:
        assert not report[key]
    for key in [
        "actual_native_broker_calls",
        "actual_provider_network_calls",
        "actual_telegram_network_calls",
        "actual_child_processes_spawned",
        "real_orders",
        "application_state_writes",
    ]:
        assert report[key] == 0
    assert "MetaTrader5" not in sys.modules and not (root / "data").exists()


def test_native_profile_blocks_even_with_green_development_config_and_pins(source):
    root, _, _ = source
    report = preflight(root, profile="windows_native", env_name=".env.example")
    assert report["overall"] == "blocked" and not report["trading_authorized"]
    assert any(item["code"] == "interactive_session_and_ntfs_acl_unverified" for item in report["findings"])
    assert report["observations"]["configuration"]["backend"] == "mock"


def test_explicit_memory_snapshot_schema_observed_without_financial_write(source):
    root, _, _ = source
    sqlite_fixture(root)
    before = inventory(root)
    report = preflight(root, inspect_sqlite_snapshot=True)
    assert report["overall"] == "offline_checks_passed"
    assert report["observations"]["sqlite_snapshot"]["schema_version_observed"] == 2
    assert inventory(root) == before


def test_missing_db_when_explicit_snapshot_requested_is_not_initialized(source):
    root, _, _ = source
    report = preflight(root, inspect_sqlite_snapshot=True)
    assert report["overall"] == "blocked" and not (root / "data").exists()


@pytest.mark.parametrize("profile", ["live", "ready", None, True])
def test_no_approval_profile_can_be_injected(source, profile):
    root, _, _ = source
    with pytest.raises(ValueError):
        preflight(root, profile=profile)


def test_actual_stdout_clis_and_existing_files_unchanged(source, capsys):
    root, _, anchor = source
    before = inventory(root)
    assert verify_cli(["--root", str(root), "--trusted-manifest-sha256", anchor]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["integrity_verified"] and not data["trading_permission"]
    assert readiness_cli(["--root", str(root), "--env-file", ".env.example"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["overall"] == "offline_checks_passed" and not data["stage_evidence"]
    assert inventory(root) == before
    assert readiness_cli(["--root", str(root), "--profile", "windows_native"]) == 2
    assert json.loads(capsys.readouterr().out)["overall"] == "blocked"


def test_cli_redaction_missing_manifest_no_path_or_file_values(tmp_path, capsys):
    root = tmp_path / "TEST_SECRET_PRIVATE_PATH"
    root.mkdir()
    assert verify_cli(["--root", str(root)]) == 2
    assert "TEST_SECRET_PRIVATE_PATH" not in capsys.readouterr().out
    assert readiness_cli(["--root", str(root)]) == 2
    assert "TEST_SECRET_PRIVATE_PATH" not in capsys.readouterr().out
