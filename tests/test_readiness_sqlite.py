"""TEST ONLY isolated financial/control rows. Snapshot never means live authoritative readiness."""

import sqlite3
from pathlib import Path

import pytest

from core.database import Database
from core.models import BotState
from core.settings import Settings
from readiness.sqlite_snapshot import inspect_sqlite
from tests.readiness_helpers import inventory, source_tree, sqlite_fixture


def codes(items):
    return {item.code for item in items}


def test_schema_two_in_memory_snapshot_does_not_open_original_db_or_sidecars(tmp_path, monkeypatch):
    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    before = inventory(root)
    original = sqlite3.connect
    opened = []

    def tracking(name, *args, **kwargs):
        opened.append(name)
        assert name == ":memory:"
        return original(name, *args, **kwargs)

    monkeypatch.setattr("readiness.sqlite_snapshot.sqlite3.connect", tracking)
    findings, data = inspect_sqlite(path, root=root)
    assert "sqlite_memory_snapshot_observed" in codes(findings) and data["schema_version_observed"] == 2
    assert data["desired_state"] == "paused" and not data["runtime_session_present"]
    assert opened == [":memory:"] and not data["original_db_opened_by_sqlite"]
    assert not data["authoritative_running_state"] and inventory(root) == before


@pytest.mark.parametrize(
    "kind", ["missing", "bad_header", "short", "directory", "symlink", "hardlink", "outside"]
)
def test_invalid_snapshot_never_creates_repairs_or_replaces_input(tmp_path, kind):
    root, _, _ = source_tree(tmp_path)
    path = root / "bad.db"
    if kind == "bad_header":
        path.write_bytes(b"TEST_ONLY_NOT_DB" * 20)
    elif kind == "short":
        path.write_bytes(b"SQLite format 3\x00")
    elif kind == "directory":
        path.mkdir()
    elif kind in {"symlink", "hardlink", "outside"}:
        external = tmp_path / "external.db"
        external.write_bytes(b"TEST_ONLY_NOT_DB" * 20)
        if kind == "symlink":
            path.symlink_to(external)
        elif kind == "hardlink":
            path.hardlink_to(external)
        else:
            path = external
    findings, data = inspect_sqlite(path, root=root)
    assert any(item.status == "blocked" for item in findings) and not data["original_db_opened_by_sqlite"]
    if kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_nonempty_wal_or_journal_refuses_and_never_checkpoints_or_deletes(tmp_path, suffix):
    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    sidecar = Path(str(path) + suffix)
    sidecar.write_bytes(b"TEST_ONLY_PENDING_FINANCIAL_STATE")
    before = inventory(root)
    findings, _ = inspect_sqlite(path, root=root)
    assert "sqlite_pending_journal_or_wal" in codes(findings) and inventory(root) == before


def test_zero_length_sidecar_is_not_deleted_or_rewritten(tmp_path):
    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    Path(str(path) + "-wal").write_bytes(b"")
    Path(str(path) + "-shm").write_bytes(b"TEST_ONLY_NOT_OPENED")
    before = inventory(root)
    findings, _ = inspect_sqlite(path, root=root)
    assert "sqlite_memory_snapshot_observed" in codes(findings) and inventory(root) == before


@pytest.mark.parametrize("kind", ["running", "session", "kill"])
def test_stored_control_state_is_preserved_not_auto_paused_or_unlatched(tmp_path, kind):
    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    db = Database(Settings(_env_file=None, project_root=root))
    with db.session() as session:
        state = session.get(BotState, 1)
        if kind == "running":
            state.desired_state = "running"
        elif kind == "session":
            state.session_id = "TEST_ONLY_PRIVATE_SESSION_NEVER_OUTPUT"
        else:
            state.kill_switch_active = True
    db.close()
    before = inventory(root)
    findings, data = inspect_sqlite(path, root=root)
    assert inventory(root) == before and "TEST_ONLY_PRIVATE_SESSION" not in str(data)
    if kind == "kill":
        assert data["kill_switch_latched"] and "sqlite_snapshot_kill_latch_preserved" in codes(findings)
    else:
        assert "sqlite_snapshot_runtime_not_stopped_paused" in codes(findings)


@pytest.mark.parametrize(
    "kind", ["old_schema", "missing_table", "view_instead_of_table", "bad_schema_singleton"]
)
def test_schema_shape_does_not_auto_initialize_migrate_or_execute_a_view(tmp_path, kind):
    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    con = sqlite3.connect(path)
    if kind == "old_schema":
        con.execute("UPDATE schema_version SET version=1")
    elif kind == "missing_table":
        con.execute("DROP TABLE signals")
    elif kind == "view_instead_of_table":
        con.execute("DROP TABLE signals")
        con.execute("CREATE VIEW signals AS SELECT load_extension('TEST_ONLY_NEVER_LOAD')")
    else:
        con.execute("DELETE FROM schema_version")
    con.commit()
    con.close()
    before = inventory(root)
    findings, _ = inspect_sqlite(path, root=root)
    assert any(item.status == "blocked" for item in findings) and inventory(root) == before


def test_sidecar_mutation_during_snapshot_is_not_a_green_observation(tmp_path, monkeypatch):
    from readiness import sqlite_snapshot

    root, _, _ = source_tree(tmp_path)
    path = sqlite_fixture(root)
    reader = sqlite_snapshot.read_bytes

    def mutate(*args, **kwargs):
        data = reader(*args, **kwargs)
        Path(str(path) + "-wal").write_bytes(b"TEST_ONLY_NEW_WAL")
        return data

    monkeypatch.setattr(sqlite_snapshot, "read_bytes", mutate)
    findings, data = inspect_sqlite(path, root=root)
    assert "sqlite_pending_journal_or_wal" in codes(findings)
    assert data == {"original_db_opened_by_sqlite": False}
