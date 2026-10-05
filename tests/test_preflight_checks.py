"""Preflight unit checks: fully mocked/offline. No network, broker, terminal launch or order."""

import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from core.settings import Settings
from readiness.preflight_checks import (
    POWER_ARGV,
    REQUIRED_KEYS,
    inspect_clock,
    inspect_env_keys,
    inspect_imports,
    inspect_power,
    inspect_runtime,
    inspect_telegram_format,
    inspect_terminal,
    inspect_writability,
    probe_ai_provider,
)
from tests.preflight_helpers import (
    BOT_TOKEN,
    OWNER_ID,
    FakePowerRunner,
    assert_no_secret,
    codes,
    dumped,
    fake_http,
    forbid_transports,
)
from tests.readiness_helpers import inventory, source_tree


def settings(tmp_path, **changes):
    return Settings(_env_file=None, project_root=tmp_path, **changes)


def write_env(root, text, *, name=".env"):
    path = root / name
    path.write_text(text)
    os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------- runtime/imports


def test_runtime_observed_without_touching_native_or_environment(monkeypatch):
    forbid_transports(monkeypatch)
    findings, observed = inspect_runtime()
    assert codes({"findings": list(findings)})["python_runtime_observed"] == "passed"
    assert observed["interpreter_bits"] == 64 and observed["platform"] == sys.platform
    assert observed["windows"] is False and "MetaTrader5" not in sys.modules


@pytest.mark.parametrize("version", [(3, 10, 11), (3, 9, 0)])
def test_old_interpreter_is_blocked_not_warned(monkeypatch, version):
    monkeypatch.setattr(sys, "version_info", version)
    findings, _ = inspect_runtime()
    assert codes({"findings": list(findings)})["python_target_not_met"] == "blocked"


def test_native_sdk_import_is_opt_in_windows_only_and_never_connects(tmp_path):
    root, _, _ = source_tree(tmp_path)
    (root / "requirements.txt").write_text(
        'pydantic==2.13.5\nMetaTrader5==5.0.6231; sys_platform == "win32"\n'
    )
    loaded = []

    def loader(name):
        loaded.append(name)
        if name == "MetaTrader5":
            raise AssertionError("native SDK must not be imported without the explicit opt-in")
        return object()

    findings, observed = inspect_imports(
        root, windows=True, allow_native=False, reader=lambda _: "1", loader=loader
    )
    assert "MetaTrader5" not in loaded and not observed["native_import_attempted"]
    assert codes({"findings": list(findings)})["native_sdk_import_not_attempted"] == "not_checked"

    loaded.clear()
    findings, observed = inspect_imports(
        root, windows=True, allow_native=True, reader=lambda _: "1", loader=lambda name: loaded.append(name)
    )
    assert "MetaTrader5" in loaded and observed["native_import_attempted"]
    assert codes({"findings": list(findings)})["native_sdk_import_requested"] == "warning"

    loaded.clear()
    _, observed = inspect_imports(
        root, windows=False, allow_native=True, reader=lambda _: "1", loader=lambda name: loaded.append(name)
    )
    assert "MetaTrader5" not in loaded and not observed["native_import_attempted"]


def test_unimportable_package_is_blocked_with_names_not_exception_text(tmp_path):
    root, _, _ = source_tree(tmp_path)
    findings, observed = inspect_imports(
        root,
        windows=False,
        reader=lambda name: "2.13.5",
        loader=lambda name: (_ for _ in ()).throw(RuntimeError("SENTINEL_PRIVATE_PATH_ERROR")),
    )
    document = {"findings": list(findings)}
    assert codes(document)["required_packages_not_importable"] == "blocked"
    assert observed["failed"] and "SENTINEL" not in dumped(document)


def test_metadata_only_pins_are_not_imported(tmp_path):
    root, _, _ = source_tree(tmp_path)
    (root / "requirements.txt").write_text("pydantic==2.13.5\ntzdata==2026.4\n")
    loaded = []
    findings, observed = inspect_imports(
        root, windows=False, reader=lambda _: "1", loader=lambda name: loaded.append(name)
    )
    assert loaded == ["pydantic"] and observed["metadata_or_native_skipped"] == 1
    assert codes({"findings": list(findings)})["required_packages_importable"] == "passed"


# ---------------------------------------------------------------- terminal file


def test_terminal_file_is_observed_read_only_and_never_launched(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ-TEST-ONLY-NOT-A-REAL-EXECUTABLE")
    findings, observed = inspect_terminal(terminal, windows=True)
    assert codes({"findings": list(findings)})["terminal_file_present"] == "passed"
    assert observed["terminal_launched"] is False and observed["terminal_file_observed"] is True
    assert observed["terminal_bytes"] == terminal.stat().st_size
    assert terminal.read_bytes() == b"MZ-TEST-ONLY-NOT-A-REAL-EXECUTABLE"


@pytest.mark.parametrize("fault", ["missing", "wrong_name", "directory", "symlink", "hardlink"])
def test_invalid_terminal_targets_are_blocked_without_creation(tmp_path, fault):
    terminal = tmp_path / "terminal64.exe"
    if fault == "wrong_name":
        terminal = tmp_path / "terminal.exe"
        terminal.write_bytes(b"MZ")
    elif fault == "directory":
        terminal.mkdir()
    elif fault in {"symlink", "hardlink"}:
        other = tmp_path / "other.exe"
        other.write_bytes(b"MZ")
        if fault == "symlink":
            terminal.symlink_to(other)
        else:
            os.link(other, terminal)
    findings, observed = inspect_terminal(terminal, windows=True)
    assert codes({"findings": list(findings)})["terminal_file_not_verified"] == "blocked"
    assert observed["terminal_launched"] is False
    assert not (tmp_path / "created.exe").exists()


def test_windows_terminal_path_is_not_evaluated_off_windows(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    findings, observed = inspect_terminal("C:/Program Files/MetaTrader 5/terminal64.exe", windows=False)
    assert codes({"findings": list(findings)})["terminal_path_not_evaluated_off_windows"] == "not_checked"
    assert observed == {"terminal_file_observed": False, "terminal_launched": False}
    assert "MetaTrader5" not in sys.modules


# ---------------------------------------------------------------- env keys/secrets


def safe_values(**changes):
    values = {name: "SENTINEL_VALUE" for name in REQUIRED_KEYS}
    values.update(
        {
            "LIVE_TRADING": "false",
            "PAPER_TRADING": "true",
            "START_PAUSED": "true",
            "DEMO_MODE": "true",
            "BACKTEST_MODE": "false",
            "MT5_BACKEND": "mock",
        }
    )
    values.update(changes)
    return values


def test_required_keys_present_and_values_never_printed(tmp_path):
    write_env(tmp_path, "\n".join(f"{key}={item}" for key, item in safe_values().items()))
    findings, observed = inspect_env_keys(tmp_path, env_name=".env")
    assert codes({"findings": list(findings)})["required_configuration_keys_present"] == "passed"
    assert observed["required_missing"] == () and observed["required_empty"] == ()
    assert "SENTINEL_VALUE" not in dumped({"findings": list(findings), "observations": observed})


def test_missing_or_empty_required_keys_block_a_private_env_but_not_the_template(tmp_path):
    values = safe_values()
    values.pop("SYMBOLS")
    write_env(tmp_path, "\n".join(f"{key}={item}" for key, item in values.items()))
    findings, observed = inspect_env_keys(tmp_path, env_name=".env")
    assert codes({"findings": list(findings)})["required_configuration_keys_incomplete"] == "blocked"
    assert observed["required_missing"] == ("SYMBOLS",)

    write_env(tmp_path, "TELEGRAM_BOT_TOKEN=\nTELEGRAM_OWNER_ID=\n", name=".env.example")
    findings, observed = inspect_env_keys(tmp_path, env_name=".env.example")
    assert codes({"findings": list(findings)})["required_configuration_keys_incomplete"] == "not_checked"
    assert set(observed["required_empty"]) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"}


@pytest.mark.parametrize(
    "key,value",
    [("LIVE_TRADING", "true"), ("PAPER_TRADING", "false"), ("START_PAUSED", "false"), ("DEMO_MODE", "false")],
)
def test_changed_safe_defaults_are_observed_and_blocked_never_rewritten(tmp_path, key, value):
    values = safe_values(**{key: value})
    path = write_env(tmp_path, "\n".join(f"{name}={item}" for name, item in values.items()))
    before = path.read_bytes()
    findings, _ = inspect_env_keys(tmp_path, env_name=".env")
    assert codes({"findings": list(findings)})["safe_default_changed_in_file"] == "blocked"
    assert path.read_bytes() == before  # Preflight never edits configuration.


def test_blank_broker_login_triplet_is_a_valid_deliberate_configuration(tmp_path):
    values = safe_values(MT5_LOGIN="", MT5_PASSWORD="", MT5_SERVER="")
    write_env(tmp_path, "\n".join(f"{name}={item}" for name, item in values.items()))
    findings, observed = inspect_env_keys(tmp_path, env_name=".env")
    assert "safe_default_changed_in_file" not in codes({"findings": list(findings)})
    assert codes({"findings": list(findings)})["required_configuration_keys_present"] == "passed"
    assert "MT5_LOGIN" in observed["blank_allowed_keys_observed"]


@pytest.mark.parametrize("text", ["SYMBOLS=a\nSYMBOLS=b\n", "SYMBOLS=${OTHER}\n", "SYMBOLS=\x00\n"])
def test_duplicate_interpolated_or_binary_environment_is_refused(tmp_path, text):
    write_env(tmp_path, text)
    findings, _ = inspect_env_keys(tmp_path, env_name=".env")
    assert codes({"findings": list(findings)})["environment_document_refused"] == "blocked"


def test_secret_values_are_masked_and_never_hashed_or_partially_echoed(tmp_path):
    write_env(tmp_path, f"TELEGRAM_BOT_TOKEN={BOT_TOKEN}\nTELEGRAM_OWNER_ID={OWNER_ID}\nSYMBOLS=EURUSD\n")
    findings, observed = inspect_env_keys(tmp_path, env_name=".env")
    document = {"findings": list(findings), "observations": observed}
    assert_no_secret(document | {"secret_values_printed": False})
    assert BOT_TOKEN not in dumped(document) and OWNER_ID not in dumped(document)


# ---------------------------------------------------------------- telegram format


def test_valid_formats_pass_without_any_api_call(monkeypatch):
    forbid_transports(monkeypatch)
    findings, observed = inspect_telegram_format(
        {"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "TELEGRAM_OWNER_ID": OWNER_ID}
    )
    assert codes({"findings": list(findings)})["telegram_format_valid"] == "passed"
    assert observed["telegram_api_calls"] == 0 and observed["owner_authenticated"] is False
    assert_no_secret({"findings": list(findings), "observations": observed, "secret_values_printed": False})


@pytest.mark.parametrize(
    "token,owner,code",
    [
        ("not-a-token", OWNER_ID, "telegram_token_format_invalid"),
        (BOT_TOKEN, "0", "telegram_owner_id_format_invalid"),
        (BOT_TOKEN, "-42", "telegram_owner_id_format_invalid"),
        (BOT_TOKEN, "owner-name", "telegram_owner_id_format_invalid"),
        (BOT_TOKEN, "", "telegram_pair_incomplete"),
        ("", OWNER_ID, "telegram_pair_incomplete"),
    ],
)
def test_invalid_or_partial_owner_credentials_block_without_a_request(monkeypatch, token, owner, code):
    forbid_transports(monkeypatch)
    findings, observed = inspect_telegram_format({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_ID": owner})
    assert codes({"findings": list(findings)})[code] == "blocked"
    assert observed["telegram_api_calls"] == 0


def test_absent_credentials_are_not_checked_and_remain_the_safe_default():
    findings, observed = inspect_telegram_format({})
    assert codes({"findings": list(findings)})["telegram_credentials_absent"] == "not_checked"
    assert observed["token_format_valid"] is False and observed["owner_authenticated"] is False


# ---------------------------------------------------------------- AI provider probe


def test_disabled_provider_sends_no_request(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    findings, observed = probe_ai_provider(settings(tmp_path, ai_provider="disabled"))
    assert codes({"findings": list(findings)})["ai_provider_disabled"] == "not_checked"
    assert observed["requests"] == 0


def test_single_read_only_ollama_listing_get_is_bounded(tmp_path):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.headers)))
        return httpx.Response(
            200,
            json={"models": [{"name": "a"}, {"name": "b"}]},
            headers={"date": format_datetime(datetime.now(timezone.utc), usegmt=True)},
        )

    findings, observed = probe_ai_provider(settings(tmp_path), transport=fake_http(handler))
    assert codes({"findings": list(findings)})["ai_provider_reachable"] == "passed"
    assert len(seen) == 1 and seen[0][0] == "GET" and seen[0][1] == "/api/tags"
    assert "authorization" not in {key.lower() for key in seen[0][2]}
    assert observed["requests"] == 1 and observed["model_count"] == 2 and observed["origin_printed"] is False


def test_openai_probe_sends_the_key_to_the_provider_but_never_to_the_report(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"data": [{"id": "gpt"}]})

    cfg = settings(tmp_path, ai_provider="openai", openai_api_key=SecretStr("sk-SENTINEL-OPENAI-KEY-VALUE"))
    findings, observed = probe_ai_provider(cfg, transport=fake_http(handler))
    assert seen == ["Bearer sk-SENTINEL-OPENAI-KEY-VALUE"]
    assert codes({"findings": list(findings)})["ai_provider_reachable"] == "passed"
    assert_no_secret({"findings": list(findings), "observations": observed, "secret_values_printed": False})
    assert observed["model_count"] == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 404])
def test_unexpected_provider_status_is_a_warning_never_a_green_or_block(tmp_path, status):
    findings, observed = probe_ai_provider(
        settings(tmp_path), transport=fake_http(lambda request: httpx.Response(status))
    )
    assert codes({"findings": list(findings)})["ai_provider_unexpected_status"] == "warning"
    assert observed["status_code"] == status and observed["requests"] == 1


def test_timeout_or_transport_error_is_a_warning_without_retry_or_secret_echo(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise httpx.ConnectTimeout("SENTINEL_TIMEOUT_DETAIL")

    findings, observed = probe_ai_provider(settings(tmp_path), timeout=1, transport=fake_http(handler))
    assert codes({"findings": list(findings)})["ai_provider_unreachable"] == "warning"
    assert calls == ["/api/tags"] and observed["requests"] == 1
    assert "SENTINEL" not in dumped({"findings": list(findings), "observations": observed})


def test_oversized_provider_body_is_not_buffered(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b'{"models":[' + b"1," * 200000 + b"1]}")

    findings, observed = probe_ai_provider(settings(tmp_path), transport=fake_http(handler))
    assert codes({"findings": list(findings)})["ai_provider_reachable"] == "passed"
    assert observed["model_count"] is None  # Bounded read stopped before a complete parse.


# ---------------------------------------------------------------- writability


def test_default_read_only_observation_creates_and_writes_nothing(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    cfg = settings(tmp_path)
    for name in ("data", "data/logs", "data/backups"):
        (tmp_path / name).mkdir(parents=True)
    before = inventory(tmp_path)
    findings, observed = inspect_writability(cfg, tmp_path)
    assert codes({"findings": list(findings)})["write_probes_not_performed"] == "not_checked"
    assert codes({"findings": list(findings)})["database_not_initialized"] == "not_checked"
    assert observed["write_probes_performed"] == 0 and inventory(tmp_path) == before


def test_opt_in_probes_use_only_their_own_temporary_files_and_never_open_the_db(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    for name in ("data", "data/logs", "data/backups"):
        (tmp_path / name).mkdir(parents=True)
    database = tmp_path / "data/reflexbot.db"
    database.write_bytes(b"SQLite format 3\x00" + b"PRIVATE-LEDGER-BYTES")
    stamp = (database.stat().st_size, database.stat().st_mtime_ns, database.stat().st_ino)
    import builtins

    real_open = builtins.open

    def open_spy(file, *args, **kwargs):
        if str(file).endswith("reflexbot.db"):
            raise AssertionError("preflight must never open the existing database file")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_spy)
    findings, observed = inspect_writability(cfg, tmp_path, probe=True)
    assert codes({"findings": list(findings)})["write_probes_completed"] == "passed"
    assert observed["write_probes_performed"] == 3
    assert observed["database_file_modified"] is False
    assert (database.stat().st_size, database.stat().st_mtime_ns, database.stat().st_ino) == stamp
    assert database.read_bytes() == b"SQLite format 3\x00PRIVATE-LEDGER-BYTES"
    assert not [path for path in tmp_path.rglob(".preflight-*")]  # Probes remove themselves.


def test_unwritable_state_directory_is_blocked_without_permission_change(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits are the exercised mechanism")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses POSIX permission bits, so an unwritable directory cannot be simulated")
    cfg = settings(tmp_path)
    for name in ("data", "data/logs", "data/backups"):
        (tmp_path / name).mkdir(parents=True)
    os.chmod(tmp_path / "data/backups", 0o500)
    try:
        findings, observed = inspect_writability(cfg, tmp_path, probe=True)
        assert codes({"findings": list(findings)})["state_path_not_writable"] == "blocked"
        assert observed["write_probes_performed"] < 3
        assert stat.S_IMODE((tmp_path / "data/backups").stat().st_mode) == 0o500
    finally:
        os.chmod(tmp_path / "data/backups", 0o700)


def test_state_path_escaping_the_source_root_is_blocked(tmp_path):
    cfg = settings(tmp_path).model_copy(update={"data_dir": Path("../outside")})
    findings, _ = inspect_writability(cfg, tmp_path, probe=True)
    assert codes({"findings": list(findings)})["state_path_outside_source_root"] == "blocked"
    assert not (tmp_path.parent / "outside").exists()


def test_database_changed_by_another_process_during_probe_is_blocked(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    for name in ("data", "data/logs", "data/backups"):
        (tmp_path / name).mkdir(parents=True)
    database = tmp_path / "data/reflexbot.db"
    database.write_bytes(b"SQLite format 3\x00ORIGINAL")
    import builtins
    import tempfile

    real, real_open = tempfile.mkstemp, builtins.open

    def hostile(**kwargs):
        with real_open(database, "ab") as handle:
            handle.write(b"X")
        return real(**kwargs)

    monkeypatch.setattr("readiness.preflight_checks.tempfile.mkstemp", hostile)
    findings, observed = inspect_writability(cfg, tmp_path, probe=True)
    assert codes({"findings": list(findings)})["database_file_changed_during_probe"] == "blocked"
    assert observed["database_file_modified"] is True


def test_non_sqlite_database_url_is_not_probed_or_contacted(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    cfg = settings(tmp_path).model_copy(
        update={"database_url": SecretStr("postgresql+psycopg://user:SENTINEL@db.invalid/reflex")}
    )
    findings, observed = inspect_writability(cfg, tmp_path, probe=True)
    assert codes({"findings": list(findings)})["database_url_not_local_sqlite"] == "warning"
    assert observed["write_probes_performed"] >= 0 and "SENTINEL" not in dumped({"findings": list(findings)})


# ---------------------------------------------------------------- clock


def test_local_clock_sanity_without_any_external_reference(tmp_path, monkeypatch):
    forbid_transports(monkeypatch)
    findings, observed = inspect_clock(settings(tmp_path))
    assert codes({"findings": list(findings)})["external_clock_drift_not_measured"] == "not_checked"
    assert observed["trading_day_timezone_resolved"] is True
    assert observed["external_clock_drift_seconds"] is None


def test_unresolvable_trading_timezone_is_blocked(tmp_path):
    cfg = settings(tmp_path).model_copy(update={"trading_day_timezone": "Mars/Olympus_Mons"})
    findings, observed = inspect_clock(cfg)
    assert codes({"findings": list(findings)})["trading_day_timezone_unresolved"] == "blocked"
    assert observed["trading_day_timezone_resolved"] is False


def test_provider_date_header_drift_is_a_warning_and_never_changes_the_clock(tmp_path):
    stale = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=12), usegmt=True)
    findings, observed = inspect_clock(settings(tmp_path), http_date=stale)
    assert codes({"findings": list(findings)})["system_clock_drift_warning"] == "warning"
    assert observed["external_clock_drift_seconds"] > 60

    fresh = format_datetime(datetime.now(timezone.utc), usegmt=True)
    findings, observed = inspect_clock(settings(tmp_path), http_date=fresh)
    assert codes({"findings": list(findings)})["system_clock_within_tolerance"] == "passed"
    assert abs(observed["external_clock_drift_seconds"]) <= 5.0


@pytest.mark.parametrize("value", ["not-a-date", "", 1704067200, None])
def test_unusable_date_header_is_not_treated_as_a_measurement(tmp_path, value):
    findings, observed = inspect_clock(settings(tmp_path), http_date=value)
    if value is None:
        assert codes({"findings": list(findings)})["external_clock_drift_not_measured"] == "not_checked"
    else:
        assert codes({"findings": list(findings)})["external_clock_reference_unusable"] == "not_checked"
    assert observed["external_clock_drift_seconds"] is None


# ---------------------------------------------------------------- power settings


def test_power_query_is_opt_in_and_never_runs_off_windows(monkeypatch):
    runner = FakePowerRunner()
    findings, observed = inspect_power(windows=False, enabled=False, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_not_checked"] == "not_checked"
    assert runner.calls == []

    findings, observed = inspect_power(windows=False, enabled=True, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_not_applicable"] == "not_checked"
    assert runner.calls == [] and observed["power_settings_queried"] is False


def test_windows_power_query_is_informational_bounded_and_unmodified():
    runner = FakePowerRunner(
        stdout="Current AC Power Setting Index: 0x00000708\nCurrent DC Power Setting Index: 0x0000012C\n"
    )
    findings, observed = inspect_power(windows=True, enabled=True, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_observed"] == "warning"
    assert observed["standby_idle_seconds"] == (1800, 300)
    assert observed["power_settings_modified"] is False and runner.safe_invocation


def test_sleep_disabled_plan_is_reported_without_changing_it():
    runner = FakePowerRunner(stdout="Current AC Power Setting Index: 0x00000000\n")
    findings, observed = inspect_power(windows=True, enabled=True, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_observed"] == "passed"
    assert observed["standby_idle_seconds"] == (0,)


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(POWER_ARGV, 15), OSError("SENTINEL"), subprocess.SubprocessError("SENTINEL")],
)
def test_power_query_failure_never_echoes_raw_output(error):
    runner = FakePowerRunner(error=error)
    findings, observed = inspect_power(windows=True, enabled=True, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_query_refused"] == "warning"
    assert observed["power_settings_queried"] is False
    assert "SENTINEL" not in dumped({"findings": list(findings), "observations": observed})


def test_unparsable_power_output_is_refused_not_guessed():
    runner = FakePowerRunner(stdout="SENTINEL_UNEXPECTED_POWERCFG_TEXT", returncode=1)
    findings, observed = inspect_power(windows=True, enabled=True, runner=runner)
    assert codes({"findings": list(findings)})["power_settings_query_refused"] == "warning"
    assert "SENTINEL" not in dumped({"findings": list(findings), "observations": observed})


# ---------------------------------------------------------------- CLI exit code (Part 15)


@pytest.mark.parametrize(("blocked", "expected"), [(False, 0), (True, 2)])
def test_preflight_cli_exit_code_matches_the_reported_overall(monkeypatch, capsys, blocked, expected):
    """Regression: the CLI compared against a status the report never emits, so it always exited 2."""
    import json

    import scripts.preflight as cli
    from readiness.contracts import ReportBuilder

    class VerifiedIntegrity:
        integrity_verified = True

        def to_dict(self):
            return {"integrity_verified": True}

    builder = ReportBuilder("development")
    builder.add("test_only_fixture", "blocked" if blocked else "passed", "fixture")
    document = builder.document(VerifiedIntegrity())
    monkeypatch.setattr(cli, "deployment_preflight", lambda *a, **k: document)
    assert cli.main([]) == expected
    data = json.loads(capsys.readouterr().out)
    assert data["overall"] == ("blocked" if blocked else "offline_checks_passed")
    assert data["trading_authorized"] is False
