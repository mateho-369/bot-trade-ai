"""Explicit file-only validation; TEST_ONLY tokens never contact provider/Telegram/broker."""

import json
from pathlib import Path

import pytest

from readiness.contracts import InspectionError
from readiness.settings_inspection import dotenv_values, inspect_settings
from tests.readiness_helpers import inventory, source_tree


def codes(findings):
    return {item.code for item in findings}


def test_template_reuses_settings_validation_without_database_or_secrets(tmp_path):
    root, _, _ = source_tree(tmp_path)
    before = inventory(root)
    cfg, findings, data = inspect_settings(root, env_name=".env.example")
    assert cfg is not None and cfg.symbols == ("EURUSD",) and cfg.demo_mode
    assert data["mode"] == "paper" and data["backend"] == "mock" and data["start_paused"]
    assert not data["reporter_enabled"] and not data["mt5_login_triplet_configured"]
    assert "configuration_validated" in codes(findings) and not (root / "data").exists()
    assert inventory(root) == before


def test_default_inspection_ignores_private_file_and_all_inherited_credentials(tmp_path, monkeypatch):
    root, _, _ = source_tree(tmp_path)
    (root / ".env").write_text("NOT VALID CONFIG\nTEST_SECRET_FILE=TEST_ONLY_NEVER_PRINT\n")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_ONLY_SECRET_ENV_NEVER_TRANSPORT")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "42")
    cfg, findings, data = inspect_settings(root)
    assert cfg and not cfg.live_trading and not cfg.telegram_bot_token.get_secret_value()
    assert data["runtime_process_override_count"] >= 3 and not data["process_environment_values_used"]
    assert "runtime_environment_overrides_present" in codes(findings)
    rendered = json.dumps(data) + str(findings)
    assert "TEST_ONLY_SECRET" not in rendered and "LIVE_TRADING" not in rendered


def test_explicit_file_defeats_os_values_but_reports_runtime_mismatch(tmp_path, monkeypatch):
    root, _, _ = source_tree(tmp_path)
    monkeypatch.setenv("MAX_DAILY_TRADES", "50")
    cfg, findings, _ = inspect_settings(root, env_name=".env.example")
    assert cfg.max_daily_trades == 12 and "runtime_environment_overrides_present" in codes(findings)


@pytest.mark.parametrize(
    "token,chat_id,enabled",
    [
        ("123456:" + "A" * 30, "-10042", True),
        ("not-a-token", "42", False),
        ("123456:" + "A" * 30, "invalid chat", False),
    ],
)
def test_optional_report_credentials_require_valid_outbound_formats(tmp_path, token, chat_id, enabled):
    root, _, _ = source_tree(tmp_path)
    path = root / ".env"
    path.write_text(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_REPORT_CHAT_ID={chat_id}\n")
    path.chmod(0o600)
    cfg, findings, data = inspect_settings(root, env_name=".env")
    assert cfg is not None and "configuration_validated" in codes(findings)
    assert data["reporter_enabled"] is enabled


@pytest.mark.parametrize(
    "raw",
    [
        b"X=1\nx=2",
        b"export X=1\nX=2",
        b"X",
        b"X=${PRIVATE}",
        b"=value",
        b"\xff",
        b"\xef\xbb\xbfX=1",
        b"X=\x00",
        b'X="unclosed',
        b"X=" + b"a" * 65536,
    ],
)
def test_dotenv_duplicate_case_interpolation_bom_errors_and_bounds_refuse(raw):
    with pytest.raises(InspectionError):
        dotenv_values(raw)


def test_real_dotenv_grammar_preserves_literal_quoted_values_not_shell_execution():
    assert dotenv_values(b"export A=\"hello # world\"\nB='literal $HOME'\nC=\n# comment\n") == {
        "a": "hello # world",
        "b": "literal $HOME",
        "c": "",
    }


@pytest.mark.parametrize(
    "line",
    [
        "UNKNOWN_TEST_FIELD=TEST_ONLY_SECRET",
        "MAX_DAILY_TRADES=0",
        "SYMBOLS=../bad",
        "NEWS_SOURCE_COVERAGE_JSON={}",
        "MAX_RISK_PERCENT_PER_TRADE=NaN",
    ],
)
def test_invalid_or_incomplete_actual_settings_do_not_echo_input_or_values(tmp_path, line):
    root, _, _ = source_tree(tmp_path)
    path = root / ".env"
    path.write_text(line + "\n")
    path.chmod(0o600)
    cfg, findings, data = inspect_settings(root, env_name=".env")
    # A valid empty coverage declaration is never interpreted as genuine news coverage.
    if line == "NEWS_SOURCE_COVERAGE_JSON={}":
        assert cfg is not None and cfg.news_source_coverage == {}
    else:
        assert cfg is None and "configuration_validation_refused" in codes(findings)
    assert "TEST_ONLY_SECRET" not in json.dumps(data) + str(findings)
    assert not (root / "data").exists()


@pytest.mark.parametrize("name", ["../.env", "/private.env", "research.env", "https://example.invalid", ""])
def test_only_deliberate_source_root_environment_names_are_allowed(tmp_path, name):
    root, _, _ = source_tree(tmp_path)
    cfg, findings, _ = inspect_settings(root, env_name=name)
    assert cfg is None and "environment_must_be_source_root_file" in codes(findings)


def test_private_mode_and_path_scope_checked_without_permission_change(tmp_path):
    root, _, _ = source_tree(tmp_path)
    path = root / ".env"
    path.write_text("PAPER_TRADING=true\nDATA_DIR=../outside\n")
    path.chmod(0o644)
    before = inventory(root)
    cfg, findings, _ = inspect_settings(root, env_name=".env")
    assert cfg is None and "private_environment_permissions_unsafe" in codes(findings)
    assert inventory(root) == before


def test_native_template_never_auto_switches_backend_without_private_demo_inputs(tmp_path):
    root, _, _ = source_tree(tmp_path)
    cfg, findings, _ = inspect_settings(root, env_name=".env.example", profile="windows_native")
    assert cfg.mt5_backend == "mock" and not cfg.live_trading
    assert {
        "native_private_environment_not_supplied",
        "native_source_configuration_missing",
    }.issubset(codes(findings))


def test_json_alias_decoding_no_decode_rules_and_existing_cross_field_rules(tmp_path):
    root, _, _ = source_tree(tmp_path)
    path = root / ".env"
    path.write_text(
        'SYMBOLS=EURUSD\nSYMBOL_ALIASES_JSON={"EURUSD":"EURUSD.a"}\n'
        'STRATEGY_WEIGHTS_JSON={"trend":0.3,"mean_reversion":0.25,"breakout":0.2,"momentum":0.25}\n'
    )
    path.chmod(0o600)
    cfg, _, _ = inspect_settings(root, env_name=".env")
    assert cfg.symbol_aliases["EURUSD"] == "EURUSD.a" and sum(cfg.strategy_weights.values()) == 1
    path.write_text(path.read_text() + "RUNTIME_SHUTDOWN_SECONDS=30\nMT5_API_TIMEOUT_SECONDS=45\n")
    cfg, findings, _ = inspect_settings(root, env_name=".env")
    assert cfg is None and "configuration_validation_refused" in codes(findings)


def test_source_root_env_override_cannot_launder_a_different_install(tmp_path):
    root, _, _ = source_tree(tmp_path)
    path = root / ".env"
    path.write_text("PROJECT_ROOT=" + str(tmp_path) + "\n")
    path.chmod(0o600)
    cfg, findings, _ = inspect_settings(root, env_name=".env")
    assert cfg is None and "environment_root_scope_mismatch" in codes(findings)
    # An identical literal source-root override remains a normal supported operator environment.
    path.write_text("PROJECT_ROOT=" + str(root) + "\n")
    cfg, _, _ = inspect_settings(root, env_name=".env")
    assert cfg is not None and cfg.project_root == Path(root)
