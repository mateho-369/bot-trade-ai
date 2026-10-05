from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, StatementError

from core.database import Database
from core.models import AccountSnapshot, AuditLog, BotState, SchemaVersion
from core.settings import Settings


@pytest.fixture
def database(tmp_path):
    settings = Settings(_env_file=None, project_root=tmp_path)
    db = Database(settings)
    db.initialize()
    yield db
    db.close()


def test_new_database_paused_and_idempotent(database):
    assert database.status()["state"] == "paused"
    assert database.status()["heartbeat"] is None
    database.initialize()
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_persistent_kill_switch_survives_reopen(database):
    with database.session() as session:
        state = session.get(BotState, 1)
        state.desired_state = "killed"
        state.kill_switch_active = True
    second = Database(database.settings)
    try:
        second.initialize()
        assert second.status()["kill_switch"]
        assert second.status()["state"] == "killed"
    finally:
        second.close()


def test_exact_decimals_and_utc_round_trip(database):
    moment = datetime(2026, 1, 1, 12, 34, 56, tzinfo=timezone.utc)
    money = Decimal("123456789012345.12345678")
    with database.session() as session:
        session.add(
            AccountSnapshot(
                time=moment,
                account_key="paper:test",
                mode="paper",
                currency="USD",
                balance=money,
                equity=money,
                margin=Decimal("0"),
                free_margin=money,
            )
        )
    with database.session() as session:
        row = session.scalar(select(AccountSnapshot))
        assert row.balance == money
        assert row.time == moment
        assert row.time.tzinfo is not None


def test_naive_timestamp_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(AuditLog(time=datetime(2026, 1, 1), action="invalid", source="test", details={}))


def test_financial_float_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(
                AccountSnapshot(
                    account_key="paper:test",
                    mode="paper",
                    currency="USD",
                    balance=1.23,
                    equity=Decimal("1.23"),
                    margin=Decimal("0"),
                    free_margin=Decimal("1.23"),
                )
            )


def test_audit_redacted(database):
    database.audit("test.event", "test", {"MT5_PASSWORD": "secret", "safe": "value"})
    with database.session() as session:
        row = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert row.details == {"MT5_PASSWORD": "[REDACTED]", "safe": "value"}


@pytest.mark.parametrize("statement", ["UPDATE audit_logs SET action='changed'", "DELETE FROM audit_logs"])
def test_sqlite_audit_append_only(database, statement):
    with pytest.raises(DBAPIError):
        with database.engine.begin() as connection:
            connection.execute(text(statement))


def test_schema_version_mismatch_refuses_startup(database):
    with database.session() as session:
        session.execute(update(SchemaVersion).values(version=999))
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        database.verify_schema()


def test_missing_state_refuses_startup(database):
    with database.session() as session:
        session.delete(session.get(BotState, 1))
    with pytest.raises(RuntimeError, match="persistent bot state"):
        database.verify_schema()


def test_transaction_rollback(database):
    with pytest.raises(RuntimeError):
        with database.session() as session:
            database.add_audit(session, "test.rollback", "test", {})
            raise RuntimeError("rollback")
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_unknown_schema_is_not_silently_modified(tmp_path):
    db = Database(Settings(_env_file=None, project_root=tmp_path))
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated (id INTEGER)"))
        with pytest.raises(RuntimeError):
            db.initialize()
    finally:
        db.close()
