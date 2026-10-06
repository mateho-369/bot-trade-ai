"""SQLite online DB snapshots and OS-locked STOPPED coherent recovery bundles.

An online DB-only snapshot is NOT a complete paper-ledger recovery bundle.
No restore/reset helper is exposed. Preserve original DB and checkpoints on error.
PostgreSQL operators must use pg_dump + a separately quiesced artifact bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

from app.process_guard import ProcessLock
from core.models import BotState
from core.security import canonical_json
from trading.risk_types import source_code_hash

MAX_FILES, MAX_BYTES, MAX_FILE = 1024, 64 * 1024 * 1024, 16 * 1024 * 1024


def database_path(settings):
    url = make_url(settings.database_url.get_secret_value())
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.query:
        raise ValueError("backup requires persistent SQLite; PostgreSQL uses operator-managed pg_dump")
    path = settings.resolve_path(url.database)
    if not path.is_file() or path.is_symlink():
        raise ValueError("existing regular SQLite database required")
    return path


def _snapshot(source, target):
    # SQLite backup API includes committed WAL state, unlike copying *.db alone.
    # Connections are closed explicitly: sqlite3's context manager only commits,
    # it never closes, and a leaked handle makes the temp snapshot undeletable on Windows.
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            started = time.monotonic()
            page_size = src.execute("PRAGMA page_size").fetchone()[0]

            def progress(status, remaining, total):
                if time.monotonic() - started > 30 or total * page_size > MAX_BYTES:
                    raise ValueError("SQLite snapshot exceeded bounded work budget")

            src.backup(dst, pages=128, sleep=0.05, progress=progress)
            if dst.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite snapshot integrity check failed")
        finally:
            dst.close()
    finally:
        src.close()


def _artifact_paths(settings):
    candidates = [
        settings.resolve_path(settings.paper_state_file),
        settings.resolve_path(settings.calendar_file),
    ]
    for name in ("models", "news", "candles", "paper"):
        directory = settings.resolve_path(settings.data_dir) / name
        if directory.is_symlink():
            raise ValueError("symlink artifact directory refused")
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise ValueError("symlink artifact refused")
                if path.is_file():
                    candidates.append(path)
                if len(candidates) > MAX_FILES:
                    raise ValueError("artifact count exceeds backup bound")
    allowed = {".json", ".jsonl", ".csv", ".parquet", ".npz"}
    paths = []
    for path in sorted(set(candidates)):
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in allowed:
            raise ValueError("unexpected artifact type; secrets and executable models must not be bundled")
        path.resolve().relative_to(settings.project_root.resolve())
        paths.append(path)
    return paths


def _build(settings, database, *, coherent):
    if database.settings.safety_fingerprint() != settings.safety_fingerprint():
        raise ValueError("backup database/settings policy differs")
    database.verify_schema()
    source = database_path(settings)
    if Path(database.engine.url.database).resolve() != source.resolve():
        raise ValueError("backup must use the explicitly matching database file")
    if coherent:
        with database.session() as session:
            state = session.get(BotState, 1)
            if state is None or state.session_id is not None or state.desired_state == "running":
                raise ValueError("cleanly STOP the runtime before creating a coherent recovery bundle")
    folder = settings.resolve_path(settings.backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    identity = uuid4().hex
    prefix = "recovery" if coherent else "db-only"
    filename = f"reflexbot-{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{identity}.zip"
    final = folder / filename
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=folder) as temp:
        temporary = Path(temp)
        snapshot = temporary / "database.sqlite3"
        _snapshot(source, snapshot)
        if snapshot.stat().st_size > MAX_BYTES:
            raise ValueError("database snapshot exceeds total bound")
        members = {"database.sqlite3": snapshot.read_bytes()}
        if coherent:
            for path in _artifact_paths(settings):
                if path.stat().st_size > MAX_FILE:
                    raise ValueError("artifact exceeds file bound")
                raw = path.read_bytes()
                if hashlib.sha256(raw).digest() != hashlib.sha256(path.read_bytes()).digest():
                    raise ValueError("artifact changed during stopped backup")
                members[path.relative_to(settings.project_root).as_posix()] = raw
                if len(members) >= MAX_FILES or sum(map(len, members.values())) > MAX_BYTES:
                    raise ValueError("backup exceeds total bound")
        if sum(map(len, members.values())) > MAX_BYTES:
            raise ValueError("database backup exceeds total bound")
        manifest = {
            "schema": 1,
            "kind": "stopped-recovery" if coherent else "online-db-only",
            "complete_recovery_bundle": coherent,
            "requires_manual_reconciliation": True,
            "never_resume_or_reset_on_restore": True,
            "credentials_included": False,
            "config_hash": settings.safety_fingerprint(),
            "code_hash": source_code_hash(settings.project_root),
            "files": {
                name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
                for name, raw in sorted(members.items())
            },
        }
        archive = temporary / "bundle.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
            for name, raw in sorted(members.items()):
                out.writestr(name, raw)
            out.writestr("BACKUP_MANIFEST.json", canonical_json(manifest))
        with zipfile.ZipFile(archive) as check:
            if check.testzip() is not None:
                raise ValueError("backup ZIP CRC failed")
        # Flush the archive before publication. fsync needs a writable handle on
        # Windows (_commit fails with EBADF on read-only descriptors).
        fd = os.open(archive, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(archive, 0o600)
        # Unique names, publication only after complete ZIP and CRC verification.
        os.replace(archive, final)
    database.audit(
        "backup.created",
        "backup",
        {
            "backup_id": identity,
            "complete_recovery_bundle": coherent,
            "credentials_included": False,
            "manual_restore_only": True,
        },
    )
    return final


def create_backup(settings, database, *, coherent=False):
    if coherent:
        with ProcessLock(settings.resolve_path(settings.runtime_lock_file)):
            return _build(settings, database, coherent=True)
    return _build(settings, database, coherent=False)


def prune_db_snapshots(settings):
    """Delete only verified matching DB-ONLY backups; never recovery/history files."""
    folder = settings.resolve_path(settings.backup_dir)
    valid = []
    for path in folder.glob("reflexbot-db-only-*.zip"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                if (
                    set(archive.namelist()) != {"database.sqlite3", "BACKUP_MANIFEST.json"}
                    or archive.getinfo("database.sqlite3").file_size > MAX_BYTES
                    or archive.getinfo("BACKUP_MANIFEST.json").file_size > 65536
                ):
                    continue
                raw = archive.read("BACKUP_MANIFEST.json")
                if len(raw) > 65536:
                    continue
                data = json.loads(raw)
                if (
                    data.get("kind") != "online-db-only"
                    or data.get("config_hash") != settings.safety_fingerprint()
                    or data.get("complete_recovery_bundle") is not False
                    or archive.testzip() is not None
                ):
                    continue
                valid.append(path)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    for path in sorted(valid, key=lambda p: p.name, reverse=True)[settings.runtime_backup_keep :]:
        path.unlink()
