"""SQLite/WAL real offline backups; no broker or automated restore."""

import hashlib
import json
import sqlite3
import zipfile

import pytest
from sqlalchemy import select

from app.backups import create_backup, prune_db_snapshots
from app.process_guard import ProcessLock
from core.database import Database
from core.models import AuditLog
from core.settings import Settings


@pytest.fixture
def system(tmp_path):
    s = Settings(_env_file=None, project_root=tmp_path)
    d = Database(s)
    d.initialize()
    yield s, d
    d.close()


def inspect_backup(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        m = json.loads(z.read("BACKUP_MANIFEST.json"))
        for name, data in m["files"].items():
            raw = z.read(name)
            assert len(raw) == data["bytes"] and hashlib.sha256(raw).hexdigest() == data["sha256"]
        return m, set(z.namelist()), z.read("database.sqlite3")


@pytest.mark.parametrize("coherent", [False, True])
def test_snapshot_includes_committed_wal_but_not_secrets_or_logs(system, tmp_path, coherent):
    s, d = system
    d.audit("test.backup_committed_wal", "test", {"marker": "TEST_ONLY"})
    (tmp_path / ".env").write_text("TEST_ONLY_SECRET_NEVER_INCLUDE")
    s.resolve_path(s.log_file).parent.mkdir(parents=True, exist_ok=True)
    s.resolve_path(s.log_file).write_text("TEST_ONLY_LOG_PRIVATE")
    path = create_backup(s, d, coherent=coherent)
    manifest, names, raw = inspect_backup(path)
    assert manifest["complete_recovery_bundle"] is coherent
    assert manifest["credentials_included"] is False
    assert not any(".env" in n or "logs/" in n for n in names)
    target = tmp_path / "inspect.sqlite3"
    target.write_bytes(raw)
    with sqlite3.connect(target) as db:
        assert (
            db.execute("SELECT count(*) FROM audit_logs WHERE action='test.backup_committed_wal'").fetchone()[
                0
            ]
            == 1
        )
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_stopped_bundle_includes_paper_news_and_immutable_models(system):
    s, d = system
    for relative in ("data/paper/shadow.json", "data/models/model.json", "data/news/calendar.json"):
        path = s.resolve_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"test_only":true}')
    manifest, names, _ = inspect_backup(create_backup(s, d, coherent=True))
    assert {"data/paper/shadow.json", "data/models/model.json", "data/news/calendar.json"}.issubset(names)
    assert manifest["kind"] == "stopped-recovery" and manifest["never_resume_or_reset_on_restore"]


def test_running_os_lock_refuses_coherent_but_allows_labeled_online(system):
    s, d = system
    with ProcessLock(s.resolve_path(s.runtime_lock_file)):
        with pytest.raises(RuntimeError):
            create_backup(s, d, coherent=True)
        m, names, _ = inspect_backup(create_backup(s, d))
        assert m["kind"] == "online-db-only" and not m["complete_recovery_bundle"]
        assert names == {"database.sqlite3", "BACKUP_MANIFEST.json"}


@pytest.mark.parametrize("session_id,state", [("expired-child", "paused"), (None, "running")])
def test_stale_or_running_session_is_not_clean_shutdown(system, session_id, state):
    from core.models import BotState

    s, d = system
    with d.locked_session() as db:
        row = db.get(BotState, 1)
        row.session_id, row.desired_state = session_id, state
    with pytest.raises(ValueError, match="STOP"):
        create_backup(s, d, coherent=True)
    assert not list(s.resolve_path(s.backup_dir).glob("*.zip"))


@pytest.mark.parametrize("name", ["bad.exe", "private.env", "untrusted.pkl", "secret.key"])
def test_unexpected_artifacts_fail_closed_without_partial_publication(system, name):
    s, d = system
    path = s.resolve_path(s.data_dir) / "models" / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b"TEST_ONLY")
    with pytest.raises(ValueError):
        create_backup(s, d, coherent=True)
    assert not list(s.resolve_path(s.backup_dir).glob("*.zip"))


def test_symlink_artifacts_refused(system, tmp_path):
    s, d = system
    folder = s.resolve_path(s.data_dir) / "models"
    folder.mkdir(parents=True)
    outside = tmp_path / "fixture.json"
    outside.write_text("{}")
    (folder / "link.json").symlink_to(outside)
    with pytest.raises(ValueError):
        create_backup(s, d, coherent=True)


def test_retention_never_deletes_coherent_or_arbitrary_files(system):
    s, d = system
    recovery = create_backup(s, d, coherent=True)
    snapshots = [create_backup(s, d) for _ in range(9)]
    arbitrary = s.resolve_path(s.backup_dir) / "reflexbot-db-only-not-really-a-backup.zip"
    arbitrary.write_bytes(b"DO NOT DELETE")
    prune_db_snapshots(s)
    assert recovery.is_file() and arbitrary.is_file()
    assert sum(p.is_file() for p in snapshots) == 7
    with d.session() as db:
        assert len(db.scalars(select(AuditLog).where(AuditLog.action == "backup.created")).all()) == 10


def test_no_schema_initialization_or_postgres_fallback(tmp_path):
    s = Settings(_env_file=None, project_root=tmp_path)
    d = Database(s)
    try:
        with pytest.raises(RuntimeError, match="uninitialized"):
            create_backup(s, d)
        with sqlite3.connect(s.resolve_path("data/reflexbot.db")) as db:
            assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        d.close()
