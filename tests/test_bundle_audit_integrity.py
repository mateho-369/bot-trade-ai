"""Bounded research closure, not authentic history or a tamper-proof administrator boundary."""

import json

import pytest

from backtesting.audit.contracts import MAX_FILE_BYTES, sha
from backtesting.audit.integrity import CapturedBundle
from backtesting.audit.runner import audit_bundle
from readiness.contracts import InspectionError
from tests.bundle_audit_helpers import clone_bundle, document, inventory, reseal_for_attack, write


def blocked(root, code=None):
    result = audit_bundle(root)
    assert result["overall"] == "blocked" and not result["internal_consistency_verified"]
    assert not result["trading_authorized"] and result["application_state_writes"] == 0
    if code:
        assert code in {row["code"] for row in result["findings"]}
    return result


def test_complete_capture_is_immutable_detached_and_trusted_digest_literal(completed_replay):
    before = inventory(completed_replay)
    digest = sha((completed_replay / "bundle.json").read_bytes())
    capture = CapturedBundle.capture(completed_replay, trusted_sha256=digest)
    assert capture.trusted_anchor_supplied and capture.manifest_sha256 == digest
    body = capture.manifest
    body["bindings"]["code_hash"] = "a" * 64
    assert capture.manifest["bindings"]["code_hash"] != "a" * 64
    with pytest.raises(TypeError):
        capture.files["report.json"] = b"{}"
    capture.unchanged()
    assert before == inventory(completed_replay)


@pytest.mark.parametrize("digest", ["a" * 64, "F" * 64, "x", True])
def test_incorrect_external_anchor_does_not_default_to_local_hash(completed_replay, digest):
    result = audit_bundle(completed_replay, trusted_sha256=digest)
    assert result["overall"] == "blocked" and not result["integrity_verified"]
    assert result["trusted_anchor_supplied"]


@pytest.mark.parametrize(
    "file",
    [
        "run.json",
        "report.json",
        "completion.json",
        "signals.jsonl",
        "equity.jsonl",
        "operations.jsonl",
        "trades.jsonl",
        "data/paper/state.json",
        "data/replay.db",
        "inputs/EURUSD_M1.csv",
        "inputs/EURUSD_ticks.csv",
        "report.md",
    ],
)
def test_every_output_input_and_financial_copy_is_raw_hash_bound(completed_replay, tmp_path, file):
    root = clone_bundle(completed_replay, tmp_path / "run")
    (root / file).write_bytes((root / file).read_bytes() + b" ")
    blocked(root, "bundle_file_hash_or_length_changed")


@pytest.mark.parametrize(
    "fault", ["missing", "extra", "failure", "undeclared", "empty_bundle", "empty_completion"]
)
def test_incomplete_or_unlisted_files_never_a_closed_result(completed_replay, tmp_path, fault):
    root = clone_bundle(completed_replay, tmp_path / "run")
    if fault == "missing":
        (root / "trades.jsonl").unlink()
    elif fault in {"extra", "undeclared"}:
        (root / "unexpected.py").write_text("raise RuntimeError('must never import this')")
        if fault == "undeclared":
            reseal_for_attack(root)
    elif fault == "failure":
        (root / "failure.json").write_text('{"status":"incomplete"}')
        reseal_for_attack(root)
    elif fault == "empty_bundle":
        (root / "bundle.json").write_bytes(b"")
    else:
        (root / "completion.json").write_bytes(b"")
        reseal_for_attack(root)
    blocked(root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "reflex-stage-v1"),
        ("purpose", "production"),
        ("source", "mt5"),
        ("completed", 1),
        ("completed", False),
        ("promotion_eligible", True),
        ("genuine_owner_authenticated", True),
        ("production_model_activated", True),
        ("manifest_self_hash_excluded", False),
        ("signature", "self-signed-proof"),
    ],
)
def test_manifest_cannot_upgrade_research_privileges(completed_replay, tmp_path, field, value):
    root = clone_bundle(completed_replay, tmp_path / "run")
    body = document(root / "bundle.json")
    body[field] = value
    write(root / "bundle.json", body)
    blocked(root, "bundle_manifest_contract_invalid")


@pytest.mark.parametrize(
    "fault", ["path", "collision", "bytes_bool", "bytes_negative", "bad_sha", "self_hash", "total", "sidecar"]
)
def test_file_record_paths_types_sizes_and_closure_bounds(completed_replay, tmp_path, fault):
    root = clone_bundle(completed_replay, tmp_path / "run")
    body = document(root / "bundle.json")
    if fault == "path":
        body["files"]["../OWNER_secret"] = body["files"].pop("report.md")
    elif fault == "collision":
        body["files"]["REPORT.JSON"] = body["files"]["report.json"]
    elif fault == "bytes_bool":
        body["files"]["report.json"]["bytes"] = True
    elif fault == "bytes_negative":
        body["files"]["report.json"]["bytes"] = -1
    elif fault == "bad_sha":
        body["files"]["report.json"]["sha256"] = "f" * 63
    elif fault == "self_hash":
        body["files"]["bundle.json"] = {"sha256": "a" * 64, "bytes": 100}
    elif fault == "total":
        for row in body["files"].values():
            row["bytes"] = MAX_FILE_BYTES
    else:
        body["files"]["data/replay.db-wal"] = {"sha256": sha(b"pending"), "bytes": 7}
    write(root / "bundle.json", body)
    blocked(root)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "directory_symlink", "root_symlink", "fifo", "case_alias"]
)
def test_no_link_alias_or_special_file_followed(completed_replay, tmp_path, kind):
    import os

    root = clone_bundle(completed_replay, tmp_path / "run")
    outside = tmp_path / "OWNER_SECRET"
    outside.write_text("DO_NOT_ECHO_OR_TOUCH")
    if kind in {"symlink", "hardlink", "fifo"}:
        target = root / "report.md"
        target.unlink()
        if kind == "symlink":
            target.symlink_to(outside)
        elif kind == "hardlink":
            os.link(outside, target)
        else:
            os.mkfifo(target)
    elif kind == "directory_symlink":
        (root / "alias").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "root_symlink":
        alias = tmp_path / "root_alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    else:
        (root / "REPORT.JSON").write_text("{}")
    result = blocked(root)
    assert "DO_NOT_ECHO" not in json.dumps(result) and outside.read_text() == "DO_NOT_ECHO_OR_TOUCH"


def test_missing_path_does_not_create_files_or_read_ambient_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///OWNER_FILE")
    monkeypatch.setenv("MT5_PASSWORD", "SENTINEL_SECRET")
    blocked(tmp_path / "never_created", "missing_file")
    assert not list(tmp_path.iterdir())


def test_changed_file_after_capture_remains_immutable_but_unchanged_fails(completed_replay, tmp_path):
    root = clone_bundle(completed_replay, tmp_path / "run")
    capture = CapturedBundle.capture(root)
    original = capture.files["report.json"]
    (root / "report.json").write_text("{}")
    assert capture.files["report.json"] == original
    with pytest.raises(InspectionError, match="bundle_changed_during_audit"):
        capture.unchanged()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"format":1,"format":2}',
        b'{"x":NaN}',
        b"[]",
        b"\xef\xbb\xbf{}",
        b'{"x":' + b"[" * 18 + b"1" + b"]" * 18 + b"}",
        b"\x80\x04pickle",
    ],
)
def test_bad_manifest_json_is_refused_before_semantics(completed_replay, tmp_path, raw):
    root = clone_bundle(completed_replay, tmp_path / "run")
    (root / "bundle.json").write_bytes(raw)
    blocked(root)


@pytest.mark.parametrize("file", ["signals.jsonl", "operations.jsonl", "trades.jsonl", "equity.jsonl"])
def test_incomplete_jsonl_tail_is_not_silently_dropped(completed_replay, tmp_path, file):
    root = clone_bundle(completed_replay, tmp_path / "run")
    raw = (root / file).read_bytes()
    assert raw.endswith(b"\n")
    (root / file).write_bytes(raw[:-1])
    reseal_for_attack(root)
    blocked(root, "bundle_journal_incomplete_line")
