"""Explicit, backed-up SQLite migration. Never invoked by runtime/init-db."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from core.database import SCHEMA_VERSION, Database
from core.models import Base, BotState


def migrate_v1_to_v2(database: Database) -> Path:
    if database.engine.dialect.name != "sqlite" or database.engine.url.database == ":memory:":
        raise RuntimeError(
            "automatic migration supports file SQLite only; use the reviewed PostgreSQL SQL guide"
        )
    path = Path(database.engine.url.database)
    if not path.is_file():
        raise RuntimeError("existing schema-1 database is required")
    inspector = inspect(database.engine)
    if not set(Base.metadata.tables).issubset(inspector.get_table_names()):
        raise RuntimeError("unknown/incomplete schema; refuse migration")
    for name, table in Base.metadata.tables.items():
        expected = set(table.columns.keys()) - (
            {"metadata_json"} if name in {"risk_state", "account_snapshots"} else set()
        )
        if {row["name"] for row in inspector.get_columns(name)} != expected:
            raise RuntimeError("schema-1 columns do not match; refuse migration")
    # BEGIN IMMEDIATE also excludes an overlapping lease/intent reservation writer.
    with database.locked_session() as session:
        version = session.scalar(text("SELECT version FROM schema_version WHERE id=1"))
        state = session.get(BotState, 1)
        now = datetime.now(timezone.utc)
        if version != 1 or state is None:
            raise RuntimeError("only a complete schema-1 database may be migrated")
        if state.desired_state == "running" or (
            state.heartbeat
            and (now - state.heartbeat).total_seconds() < database.settings.runtime_lease_seconds
        ):
            raise RuntimeError("stop all runtimes and wait for the lease before migration")
        unresolved = session.scalar(
            text("SELECT COUNT(*) FROM order_intents WHERE state IN ('submitting','acknowledged','unknown')")
        )
        if unresolved:
            raise RuntimeError("unresolved executions require broker reconciliation before migration")
        # Separate read connection sees committed WAL state. No ORM financial SUM.
        backup_dir = database.settings.resolve_path(database.settings.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (
            "schema1-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".sqlite"
        )
        with sqlite3.connect(str(path)) as source, sqlite3.connect(str(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity failed; migration aborted")
        session.execute(text("ALTER TABLE risk_state ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
        session.execute(
            text("ALTER TABLE account_snapshots ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'")
        )
        session.execute(
            text("UPDATE risk_state SET metadata_json=:metadata"),
            {"metadata": json.dumps({"baseline_verified": False, "migration_review_required": True})},
        )
        session.execute(
            text("UPDATE schema_version SET version=:version WHERE id=1"), {"version": SCHEMA_VERSION}
        )
        database.add_audit(
            session,
            "database.migrated",
            "operator",
            {
                "from": 1,
                "to": SCHEMA_VERSION,
                "backup": str(backup.relative_to(database.settings.project_root)),
                "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "risk_latches_preserved": True,
            },
        )
    database.verify_schema()
    return backup
