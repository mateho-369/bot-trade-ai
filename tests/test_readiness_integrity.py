import json

import pytest

from readiness.integrity import runnable_files, verify_release
from tests.readiness_helpers import digest, inventory, seal, source_tree


@pytest.fixture
def source(tmp_path):
    return source_tree(tmp_path)


def codes(result):
    return {item.code for item in result.findings}


def test_complete_bound_source_verification_is_not_signature_or_trading_permission(source):
    root, doc, trusted = source
    before = inventory(root)
    result = verify_release(root, trusted_manifest_sha256=trusted)
    assert result.integrity_verified and result.verified_files == len(doc["files"])
    assert result.trusted_anchor_supplied and result.release == "0.10.0"
    assert not result.to_dict()["is_signature"] and not result.to_dict()["trading_permission"]
    assert inventory(root) == before


def test_unanchored_content_comparison_explicitly_warns_not_authenticity(source):
    root, _, _ = source
    result = verify_release(root)
    assert result.integrity_verified and "trusted_anchor_not_supplied" in codes(result)


@pytest.mark.parametrize("anchor", ["0" * 64, "A" * 64, "not a digest", "x" * 65, 42, ""])
def test_wrong_or_nonliteral_trusted_digest_refuses_before_configuration(source, anchor):
    root, _, _ = source
    result = verify_release(root, trusted_manifest_sha256=anchor)
    assert not result.integrity_verified and not result.manifest_valid


@pytest.mark.parametrize("change", ["append", "same_size", "delete", "symlink", "hardlink", "world_write"])
def test_all_raw_declared_changes_refuse(source, change, tmp_path):
    root, _, _ = source
    path = root / "main.py"
    original = path.read_bytes()
    if change == "append":
        path.write_bytes(original + b"\n")
    elif change == "same_size":
        path.write_bytes(b"x" * len(original))
    elif change == "delete":
        path.unlink()
    elif change in {"symlink", "hardlink"}:
        target = tmp_path / "external"
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target) if change == "symlink" else path.hardlink_to(target)
    else:
        path.chmod(0o666)
    assert not verify_release(root).integrity_verified


@pytest.mark.parametrize(
    "name", ["sitecustomize.py", "helper.pyw", "core/new.pyi", "core/new.js", "core/new.ps1", "core/new.vbs"]
)
def test_unlisted_runnables_cannot_hide_by_keeping_all_original_hashes(source, name):
    root, _, _ = source
    (root / name).write_text("TEST_ONLY_UNTRACKED")
    result = verify_release(root)
    assert not result.integrity_verified and "runnable_set_mismatch" in codes(result)


def test_credential_name_exclusion_never_drops_legitimate_ops_test(source):
    root, _, _ = source
    (root / "tests").mkdir()
    path = root / "tests/test_ops_cli.py"
    path.write_text('"""TEST ONLY local-operator source, not credential data."""\n')
    _, anchor = seal(root)
    assert "tests/test_ops_cli.py" in runnable_files(root)
    assert verify_release(root, trusted_manifest_sha256=anchor).integrity_verified


def test_private_runtime_and_dependency_dirs_are_not_executed_or_written(source):
    root, _, _ = source
    for name in ["data", ".venv", "__pycache__"]:
        folder = root / name
        folder.mkdir()
        (folder / "private.py").write_text("TEST_ONLY_NOT_SOURCE")
    (root / ".env").write_text("PRIVATE_TEST_VALUE")
    before = inventory(root)
    assert verify_release(root).integrity_verified
    assert inventory(root) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("release", "<script>"),
        ("database_schema", True),
        ("database_schema", 3),
        ("hashed_files", True),
        ("packaged_files", 1),
        ("python_sources", 0),
        ("manifest_self_hash_excluded", False),
    ],
)
def test_manifest_metadata_cannot_claim_counts_schema_or_arbitrary_version(source, field, value):
    root, _, _ = source
    seal(root, changes=lambda doc: doc.update({field: value}))
    assert not verify_release(root).integrity_verified


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 63),
        ("sha256", "A" * 64),
        ("sha256", None),
        ("bytes", True),
        ("bytes", -1),
        ("bytes", 16 * 1024 * 1024 + 1),
        ("extra", "TEST_PRIVATE"),
    ],
)
def test_manifest_record_digest_types_bounds_and_shape_are_strict(source, field, value):
    root, _, _ = source
    seal(root, changes=lambda doc: doc["files"]["main.py"].update({field: value}))
    assert not verify_release(root).integrity_verified


@pytest.mark.parametrize(
    "name",
    [".env", "secret.key", "owner.initdata", "data/trades.db", "../outside", "C:/file", "CON.txt", "MAIN.PY"],
)
def test_manifest_traversal_secret_and_case_collision_refused(source, name):
    root, _, _ = source

    def change(doc):
        doc["files"][name] = {"sha256": "0" * 64, "bytes": 0}
        doc["hashed_files"] += 1
        doc["packaged_files"] += 1

    seal(root, changes=change)
    assert not verify_release(root).integrity_verified


def test_duplicate_json_keys_rejected_even_when_last_manifest_would_pass(source):
    root, doc, _ = source
    path = root / "docs/RELEASE_16_MANIFEST.json"
    body = json.dumps(doc)
    path.write_text('{"files": {},' + body[1:])
    result = verify_release(root)
    assert not result.integrity_verified and "invalid_json_document" in codes(result)


def test_trusted_anchor_binds_metadata_not_only_declared_bytes(source):
    root, _, anchor = source
    path = root / "docs/RELEASE_16_MANIFEST.json"
    path.write_text(path.read_text() + "\n")
    assert verify_release(root).integrity_verified
    assert not verify_release(root, trusted_manifest_sha256=anchor).integrity_verified
    assert digest(path.read_bytes()) != anchor


@pytest.mark.parametrize("relative", ["", "core"])
def test_group_or_world_writable_source_directories_are_not_protected_by_file_hashes(source, relative):
    root, _, _ = source
    (root / relative).chmod(0o777)
    result = verify_release(root)
    assert not result.integrity_verified and "source_directory_writable_by_others" in codes(result)
