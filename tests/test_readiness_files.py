import json

import pytest

from readiness.contracts import Finding, InspectionError
from readiness.files import checked_path, read_bytes, relative_name, strict_json


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/abs",
        "../secret",
        "a/../b",
        "a//b",
        "a/./b",
        "./b",
        "a\\b",
        "C:/x",
        "https://x",
        "a:",
        "a\nb",
        "a\x00b",
        "a/ ",
        "a/CON",
        "nul.txt",
        "LPT9.txt",
        "a/COM1.txt",
        "a/thing.",
        1,
        None,
    ],
)
def test_portable_relative_names_reject_traversal_drive_control_and_windows_aliases(name):
    with pytest.raises(InspectionError):
        relative_name(name)


@pytest.mark.parametrize(
    "name",
    ["main.py", ".env.example", "docs/PART_12.md", "tests/test_telegram_initdata.py", "core/__init__.py"],
)
def test_real_source_names_are_not_confused_with_bearers(name):
    assert relative_name(name) == name


def test_read_bytes_bounded_stable_and_never_creates_missing_files(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"EXACT")
    assert read_bytes(path, root=tmp_path, limit=5) == b"EXACT"
    with pytest.raises(InspectionError, match="file_too_large"):
        read_bytes(path, root=tmp_path, limit=4)
    with pytest.raises(InspectionError):
        read_bytes(tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
    with pytest.raises(InspectionError):
        read_bytes(tmp_path, limit=10)


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "hardlink", "outside", "traversal"])
def test_reads_refuse_link_aliases_or_outside_root(tmp_path, kind):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "external"
    target.write_text("PRIVATE_TEST_ONLY")
    if kind == "file_symlink":
        path = root / "link"
        path.symlink_to(target)
    elif kind == "parent_symlink":
        (root / "parent").symlink_to(tmp_path, target_is_directory=True)
        path = root / "parent/external"
    elif kind == "hardlink":
        path = root / "link"
        path.hardlink_to(target)
    elif kind == "outside":
        path = target
    else:
        path = root / "../external"
    with pytest.raises(InspectionError):
        read_bytes(path, root=root)
    assert target.read_text() == "PRIVATE_TEST_ONLY"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"\xef\xbb\xbf{}",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1e999}',
        b'{"a":',
        b'{"a":' + b"1" * 13 + b"}",
        b"[" * 17 + b"0" + b"]" * 17,
    ],
)
def test_json_refuses_duplicate_nonfinite_deep_coerced_and_unbounded_inputs(raw):
    with pytest.raises(InspectionError):
        strict_json(raw)


def test_json_quotes_do_not_forge_depth_and_raw_text_never_appears_in_errors():
    assert strict_json(json.dumps({"value": "[" * 30}).encode()) == {"value": "[" * 30}
    with pytest.raises(InspectionError) as caught:
        strict_json(b'{"TEST_SECRET_NEVER_PRINT":NaN}')
    assert "TEST_SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "code,status,message",
    [
        ("Bad\ncode", "passed", "x"),
        ("bad_code", "ready", "x"),
        ("bad_code", "passed", ""),
        ("bad_code", "passed", "x\ny"),
        ("bad_code", "passed", "x" * 361),
    ],
)
def test_findings_are_bounded_not_injectable_permission_documents(code, status, message):
    with pytest.raises(ValueError):
        Finding(code, status, message)


def test_missing_parent_does_not_get_created_when_scoping_state_path(tmp_path):
    result = checked_path(tmp_path / "data/missing/future.db", root=tmp_path, missing=True)
    assert not result.exists() and not (tmp_path / "data").exists()
