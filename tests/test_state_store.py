import json
import os

import pytest

from trading.state_store import AtomicSnapshotStore
from trading.types import RiskViolation


def test_missing_atomic_store_is_not_an_implicit_reset(tmp_path):
    assert AtomicSnapshotStore(tmp_path / "state.json").load() is None


def test_checksummed_roundtrip_and_atomic_replacement(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "nested/state.json")
    store.save({"balance": "1000", "positions": []})
    first = store.path.read_bytes()
    store.save({"balance": "999.93", "positions": [{"identifier": 1}]})
    assert store.path.read_bytes() != first
    assert store.load()["balance"] == "999.93"
    assert list(store.path.parent.glob(".paper-*.tmp")) == []


def test_modified_finances_do_not_pass_digest(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "state.json")
    store.save({"balance": "1000"})
    value = json.loads(store.path.read_text())
    value["state"]["balance"] = "1000000"
    store.path.write_text(json.dumps(value))
    with pytest.raises(RiskViolation, match="corrupt"):
        store.load()


@pytest.mark.parametrize(
    "contents", ["", "garbage", "{}", "[]", '{"version":1,"version":1,"state":{},"sha256":"wrong"}']
)
def test_malformed_and_duplicate_checkpoint_json_is_rejected(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents)
    with pytest.raises(RiskViolation):
        AtomicSnapshotStore(path).load()


def test_checkpoint_size_is_bounded_on_read_and_write(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "state.json", max_bytes=150)
    with pytest.raises(RiskViolation):
        store.save({"oversize": "x" * 200})
    store.path.write_bytes(b"x" * 151)
    with pytest.raises(RiskViolation):
        store.load()


def test_failed_replace_preserves_last_valid_snapshot_and_removes_temp(tmp_path, monkeypatch):
    store = AtomicSnapshotStore(tmp_path / "state.json")
    store.save({"balance": "1000"})

    def failure(*args):
        raise OSError("TEST disk failure")

    monkeypatch.setattr(os, "replace", failure)
    with pytest.raises(OSError):
        store.save({"balance": "999"})
    assert store.load() == {"balance": "1000"}
    assert not list(tmp_path.glob(".paper-*.tmp"))


def test_symlink_checkpoints_are_not_followed(tmp_path):
    target = tmp_path / "other.json"
    target.write_text("{}")
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege not available on this host")
    store = AtomicSnapshotStore(link)
    with pytest.raises(RiskViolation):
        store.load()
    with pytest.raises(RiskViolation):
        store.save({})
    assert target.read_text() == "{}"
