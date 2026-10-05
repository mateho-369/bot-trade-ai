"""Explicit schema initialization; no runtime auto-migration or global sessions."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import AuditLog, Base, BotState, SchemaVersion
from core.security import canonical_json, sanitize_data, secret_values
from core.settings import OperatingMode, Settings

SCHEMA_VERSION = 2
AUDIT_TRIGGERS = {
    "audit_logs_no_update": "UPDATE",
    "audit_logs_no_delete": "DELETE",
}


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.secrets = secret_values(settings)
        url = make_url(settings.database_url.get_secret_value())
        options: dict[str, Any] = {"pool_pre_ping": True, "echo": False}
        if url.get_backend_name() == "sqlite":
            if url.query:
                raise ValueError("SQLite URI/query overrides are forbidden")
            if url.database == ":memory:":
                if settings.mode in {OperatingMode.DEMO, OperatingMode.LIVE}:
                    raise ValueError("broker execution requires persistent risk state")
                options["poolclass"] = StaticPool
            else:
                if not url.database:
                    raise ValueError("SQLite database path is required")
                path = settings.resolve_path(url.database)
                path.parent.mkdir(parents=True, exist_ok=True)
                url = url.set(database=str(path))
            options["connect_args"] = {
                "check_same_thread": False,
                "timeout": settings.database_busy_timeout_ms / 1000,
            }
        elif url.get_backend_name() == "postgresql":
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            if url.drivername != "postgresql+psycopg":
                raise ValueError("PostgreSQL support requires psycopg 3")
            options["connect_args"] = {"connect_timeout": 10}
        else:
            raise ValueError("only SQLite and PostgreSQL are supported")
        self.engine: Engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._sqlite_connect)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)

    def _sqlite_connect(self, connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={self.settings.database_busy_timeout_ms}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        # A new session per operation/thread; callers can atomically write a
        # decision, order intent, reserved risk and its audit entry together.
        with self.session_factory.begin() as session:
            yield session

    @contextmanager
    def locked_session(self) -> Iterator[Session]:
        """Serialize risk/intent/control writes ACROSS processes, never only an RLock."""
        with self.session_factory() as session:
            try:
                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    session.execute(select(BotState).where(BotState.id == 1).with_for_update())
                yield session
                session.flush()
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def initialize(self) -> None:
        """Operator command only. Existing schemas are checked, never altered."""
        existing = set(inspect(self.engine).get_table_names())
        if existing:
            self.verify_schema()
            return
        with self.engine.begin() as connection:
            Base.metadata.create_all(connection)
            self._install_audit_triggers(connection)
        with self.session() as session:
            session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            session.add(BotState(id=1, desired_state="paused", kill_switch_active=False))
            self.add_audit(session, "database.initialized", "operator", {"schema_version": SCHEMA_VERSION})
        self.verify_schema()

    def _install_audit_triggers(self, connection: Connection) -> None:
        if connection.dialect.name != "sqlite":
            return
        for name, operation in AUDIT_TRIGGERS.items():
            # Both identifiers are fixed constants, never user input.
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE {operation} ON audit_logs "
                "BEGIN SELECT RAISE(ABORT, 'audit logs are append-only'); END"
            )

    def verify_schema(self) -> None:
        inspector = inspect(self.engine)
        actual = set(inspector.get_table_names())
        expected = set(Base.metadata.tables)
        if not expected.issubset(actual):
            raise RuntimeError(
                "database is uninitialized or incomplete; run init-db on an empty dedicated database"
            )
        with self.session() as session:
            versions = session.scalars(select(SchemaVersion)).all()
            if len(versions) != 1 or versions[0].id != 1 or versions[0].version != SCHEMA_VERSION:
                raise RuntimeError("database schema version mismatch; apply a reviewed migration")
            if session.get(BotState, 1) is None:
                raise RuntimeError("persistent bot state is missing; refuse to trade")
        for name, table in Base.metadata.tables.items():
            if {column["name"] for column in inspector.get_columns(name)} != set(table.columns.keys()):
                raise RuntimeError("database schema drift detected; refuse to trade")
        if self.engine.dialect.name == "sqlite":
            with self.engine.connect() as connection:
                triggers = set(
                    connection.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
                )
            if not set(AUDIT_TRIGGERS).issubset(triggers):
                raise RuntimeError("append-only audit protection is missing")

    def add_audit(self, session: Session, action: str, source: str, details: dict[str, Any]) -> AuditLog:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", action) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,64}", source
        ):
            raise ValueError("invalid audit action/source")
        clean = sanitize_data(details, self.secrets)
        if len(canonical_json(clean).encode("utf-8")) > 16384:
            raise ValueError("audit payload exceeds 16 KiB")
        row = AuditLog(action=action, source=source, details=clean)
        session.add(row)
        return row

    def audit(self, action: str, source: str, details: dict[str, Any]) -> None:
        with self.session() as session:
            self.add_audit(session, action, source, details)

    def status(self) -> dict[str, Any]:
        self.verify_schema()
        with self.session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise RuntimeError("persistent bot state disappeared; refuse to trade")
            return {
                "database": "ok",
                "schema_version": SCHEMA_VERSION,
                "mode": self.settings.mode.value,
                "state": state.desired_state,
                "kill_switch": state.kill_switch_active,
                "revision": state.revision,
                "heartbeat": state.heartbeat.isoformat() if state.heartbeat else None,
            }

    def close(self) -> None:
        self.engine.dispose()
