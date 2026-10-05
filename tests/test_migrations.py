import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select, text

from core.database import SCHEMA_VERSION, Database
from core.migrations import migrate_v1_to_v2
from core.models import BotState, RiskState
from tests.risk_helpers import MOMENT, D, config


def legacy(tmp_path, *, running=False, fresh_heartbeat=False):
    db = Database(config(tmp_path))
    db.initialize()
    with db.session() as session:
        state = session.get(BotState, 1)
        state.kill_switch_active, state.desired_state = not running, "running" if running else "killed"
        if fresh_heartbeat:
            state.heartbeat = datetime.now(timezone.utc)
        session.add(
            RiskState(
                account_key="paper:legacy",
                mode="paper",
                day=MOMENT.date(),
                day_start_equity=D("1000"),
                equity_high_water=D("1200"),
                daily_loss_latched=True,
                drawdown_latched=True,
                reserved_risk_usd=D("0"),
            )
        )
    with db.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE risk_state DROP COLUMN metadata_json")
        connection.exec_driver_sql("ALTER TABLE account_snapshots DROP COLUMN metadata_json")
        connection.exec_driver_sql("UPDATE schema_version SET version=1 WHERE id=1")
    return db


def test_schema1_requires_explicit_backed_up_migration(tmp_path):
    db = legacy(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            db.initialize()
        backup = migrate_v1_to_v2(db)
        assert backup.is_file() and backup.parent.name == "backups"
        assert len(hashlib.sha256(backup.read_bytes()).hexdigest()) == 64
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
            assert "metadata_json" not in {
                row[1] for row in connection.execute("PRAGMA table_info(risk_state)")
            }
        assert db.status()["schema_version"] == SCHEMA_VERSION == 2
        assert db.status()["kill_switch"] and db.status()["state"] == "killed"
        with db.session() as session:
            row = session.scalar(select(RiskState))
            assert row.daily_loss_latched and row.drawdown_latched and row.equity_high_water == 1200
            assert row.metadata_json["baseline_verified"] is False
            assert row.metadata_json["migration_review_required"] is True
    finally:
        db.close()


@pytest.mark.parametrize("case", ["running", "heartbeat", "unresolved", "drift"])
def test_migration_refuses_active_unknown_or_drifted_state(tmp_path, case):
    db = legacy(tmp_path, running=case == "running", fresh_heartbeat=case == "heartbeat")
    try:
        if case == "unresolved":
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO order_intents (id,idempotency_key,time,updated_at,expires_at,"
                        "mode,account_key,symbol,direction,state,request,config_hash) VALUES "
                        "('legacy',:key,:time,:time,:time,'paper','paper:legacy','EURUSD','buy','unknown','{}',:key)"
                    ),
                    {"key": "a" * 64, "time": MOMENT.replace(tzinfo=None).isoformat(sep=" ")},
                )
        if case == "drift":
            with db.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE risk_state ADD COLUMN unexpected INTEGER")
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT version FROM schema_version")) == 1
        assert "metadata_json" not in {row["name"] for row in inspect(db.engine).get_columns("risk_state")}
    finally:
        db.close()


def test_migration_is_not_silently_repeated(tmp_path):
    db = legacy(tmp_path)
    try:
        migrate_v1_to_v2(db)
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
    finally:
        db.close()


def test_inmemory_migration_is_forbidden(tmp_path):
    db = Database(config(tmp_path, database_url="sqlite:///:memory:"))
    try:
        db.initialize()
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
    finally:
        db.close()


def test_operator_migration_prints_one_valid_json_response_without_broker(tmp_path, capsys):
    import json

    from main import main

    db = legacy(tmp_path)
    db.close()
    env = tmp_path / ".env"
    env.write_text(f'PROJECT_ROOT="{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["migrate-db", "--env-file", str(env)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["migration"] == "1_to_2" and result["schema_version"] == 2
    assert result["state"] == "killed" and result["kill_switch"]
    assert (tmp_path / result["backup"]).is_file()
