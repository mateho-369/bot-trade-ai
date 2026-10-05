"""Local OS/file controls only; no native SDK or deployed task."""

import json
import os
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app import process_guard as guard
from core.settings import Settings


def test_os_lock_excludes_another_handle_and_preserves_inode(tmp_path):
    path = tmp_path / "runtime.lock"
    first, second = guard.ProcessLock(path), guard.ProcessLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
        with pytest.raises(RuntimeError):
            first.acquire()
        assert path.exists()
    finally:
        first.release()
    with second:
        assert path.exists()
    assert path.exists() and second.handle is None


@pytest.mark.parametrize(
    "raw", ["[]", "null", "1", "true", '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "{", ""]
)
def test_strict_bounded_json_rejects_invalid_objects(tmp_path, raw):
    path = tmp_path / "health.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        guard.read_json(path)


def test_read_and_atomic_write_round_trip_private(tmp_path):
    path = tmp_path / "runtime" / "health.json"
    guard.atomic_json(path, {"a": "test", "b": True})
    assert guard.read_json(path) == {"a": "test", "b": True}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    guard.atomic_json(path, {"new": 2})
    assert guard.read_json(path) == {"new": 2}
    assert not list(path.parent.glob(".runtime-*"))


def test_json_byte_limit(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"a": "x" * 20}))
    with pytest.raises(ValueError):
        guard.read_json(path, max_bytes=10)


@pytest.mark.parametrize("operation", ["read", "write", "lock"])
def test_symlink_files_refused(tmp_path, operation):
    actual = tmp_path / "actual.json"
    actual.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(actual)
    with pytest.raises((ValueError, RuntimeError)):
        if operation == "read":
            guard.read_json(link)
        elif operation == "write":
            guard.atomic_json(link, {})
        else:
            guard.ProcessLock(link).acquire()
    assert actual.read_text() == "{}"


@pytest.mark.parametrize("identity", ["", "../x", "x" * 36, str(uuid4()).upper(), "not-uuid", 123])
def test_managed_identity_rejects_noncanonical(identity):
    with pytest.raises((ValueError, TypeError)):
        guard.managed_id(identity)


def test_stop_is_generation_bound_and_never_cleared_implicitly(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    a, b = str(uuid4()), str(uuid4())
    assert not guard.stop_requested(cfg, a)
    guard.request_stop(cfg, a)
    assert guard.stop_requested(cfg, a) and not guard.stop_requested(cfg, b)
    assert guard.stop_path(cfg, a).is_file()


@pytest.mark.parametrize("raw", ["{}", '{"stop":false}', "[]", "{", '{"managed_id":"wrong","stop":true}'])
def test_malformed_generation_stop_fails_downward(tmp_path, raw):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    identity = str(uuid4())
    path = guard.stop_path(cfg, identity)
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    assert guard.stop_requested(cfg, identity)


@pytest.mark.parametrize("raw", ["{}", "[]", "{", '{"stop":true}', '{"anything":"unknown"}'])
def test_global_stop_unknown_shapes_fail_downward(tmp_path, raw):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    path = guard.operator_stop_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    assert guard.operator_stop_requested(cfg)


def test_global_stop_persists_across_fresh_settings(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    assert not guard.operator_stop_requested(cfg)
    guard.request_operator_stop(cfg)
    assert guard.operator_stop_requested(Settings(_env_file=None, project_root=tmp_path))


@pytest.mark.parametrize("backend", ["mock", "real"])
def test_non_windows_native_is_refused_before_any_factory(monkeypatch, backend):
    monkeypatch.setattr(guard, "os", NS(name="posix"))
    cfg = NS(mt5_backend=backend)
    if backend == "mock":
        guard.require_interactive_native(cfg)
    else:
        with pytest.raises(RuntimeError, match="Windows"):
            guard.require_interactive_native(cfg)


@pytest.mark.parametrize("session,success", [(0, True), (1, False), (5, True)])
def test_windows_interactive_session_predicate_only(monkeypatch, session, success):
    def probe(pid, result):
        result._obj.value = session
        return success

    monkeypatch.setattr(guard, "os", NS(name="nt", getpid=lambda: 123))
    monkeypatch.setattr(
        guard,
        "ctypes",
        NS(
            c_uint32=lambda: NS(value=0),
            byref=lambda v: NS(_obj=v),
            windll=NS(kernel32=NS(ProcessIdToSessionId=probe)),
        ),
    )
    if session == 0 or not success:
        with pytest.raises(RuntimeError, match="Session 0"):
            guard.require_interactive_native(NS(mt5_backend="real"))
    else:
        guard.require_interactive_native(NS(mt5_backend="real"))
