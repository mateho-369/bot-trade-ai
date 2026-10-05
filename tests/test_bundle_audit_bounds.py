"""Resource/hostile-schema/read-only failure bounds; test setup only creates disposable files."""

import json

import pytest

from backtesting.audit import files, integrity
from backtesting.audit.contracts import json_lines, money, object_json, stamp
from backtesting.audit.ledger import inspect_image
from backtesting.audit.runner import audit_bundle
from readiness.contracts import InspectionError
from tests.bundle_audit_helpers import clone_bundle, edit_db, inventory, reseal_for_attack
from tests.test_bundle_audit_integrity import blocked


@pytest.mark.parametrize(
    "value", [True, 1, "NaN", "Infinity", "1e999", "-1e101", "1e19", "1_000", "0x10", "", "1,000"]
)
def test_currency_parser_never_accepts_floats_nonfinite_or_unbounded(value):
    with pytest.raises(InspectionError):
        money(value)


@pytest.mark.parametrize(
    "value", ["2024-01-01", "2024-01-01T00:00:00", 1704067200, True, None, "2024-99-01Z"]
)
def test_audit_requires_aware_bounded_utc_declarations(value):
    with pytest.raises(InspectionError):
        stamp(value)


@pytest.mark.parametrize("raw", [b'{"x":NaN}', b'{"x":1,"x":2}', b"{}\n\n", b"[]\n", b"\xff\n"])
def test_hostile_jsonl_row_not_dropped_or_permissively_coerced(raw):
    with pytest.raises(InspectionError):
        json_lines(raw)


def test_journal_row_and_line_limits_before_unbounded_materialization(monkeypatch):
    with pytest.raises(InspectionError):
        json_lines(b"{}\n{}\n", limit=1)
    with pytest.raises(InspectionError):
        json_lines(b'{"text":"' + b"a" * 32768 + b'"}\n')
    with pytest.raises(InspectionError):
        object_json(b'{"x":1}', limit=2)


@pytest.mark.parametrize("bound", ["file_count", "scan", "bytes", "file_bytes"])
def test_complete_capture_bounded_before_memory_sql(completed_replay, tmp_path, monkeypatch, bound):
    root = clone_bundle(completed_replay, tmp_path / "run")
    if bound == "file_count":
        monkeypatch.setattr(files, "MAX_FILES", 2)
    elif bound == "scan":
        monkeypatch.setattr(files, "MAX_SCAN_NODES", 1)
    elif bound == "bytes":
        monkeypatch.setattr(integrity, "MAX_TOTAL_BYTES", 100)
    else:
        monkeypatch.setattr(integrity, "MAX_FILE_BYTES", 10)
    import backtesting.audit.ledger as module

    monkeypatch.setattr(module, "inspect_image", lambda *args: pytest.fail("No SQL before integrity pass"))
    result = blocked(root)
    assert not result["integrity_verified"]


@pytest.mark.parametrize("data", [b"", b"x" * 100, b"SQLite format 3\x00" + b"\x00" * 84])
def test_sql_header_refused_before_original_or_memory_connection(data, monkeypatch):
    import sqlite3

    monkeypatch.setattr(sqlite3, "connect", lambda *args: pytest.fail("Invalid image must not connect"))
    with pytest.raises(InspectionError):
        inspect_image(data)


def test_poisoned_view_instead_of_fixed_table_cannot_execute_user_sql(completed_replay, tmp_path):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_db(root, "DROP TABLE trades")
    edit_db(root, "CREATE VIEW trades AS SELECT load_extension('SENTINEL_DO_NOT_EXECUTE')")
    result = blocked(root, "bundle_ledger_schema_missing")
    assert "SENTINEL" not in json.dumps(result)


def test_audit_error_never_echoes_library_sql_or_private_values(completed_replay, monkeypatch):
    import backtesting.audit.semantics as module

    def fail(*args):
        raise RuntimeError("PASSWORD_SECRET_SQL_VALUE_SENTINEL")

    monkeypatch.setattr(module, "inspect_semantics", fail)
    before = inventory(completed_replay)
    result = blocked(completed_replay, "research_bundle_semantic_inspection_refused")
    assert "SENTINEL" not in json.dumps(result) and before == inventory(completed_replay)


def test_latched_stop_not_cleared_or_reclassified_as_live_permission(completed_replay, tmp_path):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_db(root, "UPDATE bot_state SET kill_switch_active=1, desired_state='killed'")
    before = inventory(root)
    result = audit_bundle(root)
    assert result["internal_consistency_verified"] and result["observations"]["stored_kill_latch_preserved"]
    assert not result["automatic_resume"] and before == inventory(root)


def test_unexpected_private_file_is_not_allowlisted_even_if_hash_declared(completed_replay, tmp_path):
    root = clone_bundle(completed_replay, tmp_path / "run")
    (root / "owner_token.txt").write_text("SENTINEL_PRIVATE_DO_NOT_ECHO")
    reseal_for_attack(root)
    result = blocked(root, "bundle_captured_input_contract_mismatch")
    assert "SENTINEL" not in json.dumps(result)
