"""Query ONLY a captured private replay.db byte image; never connect to any original SQL file."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from backtesting.audit.contracts import MAX_DATABASE_BYTES, MAX_ROWS, money, object_json
from readiness.contracts import InspectionError
from readiness.sqlite_snapshot import TABLES


def sql_time(value):
    # SQLAlchemy's SQLite UTCDateTime removes the timezone in stored text, then restores UTC on read.
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise InspectionError("bundle_sql_time_invalid")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            raise ValueError
        return result.replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise InspectionError("bundle_sql_time_invalid") from None


def sql_json(value):
    if not isinstance(value, str):
        raise InspectionError("bundle_sql_json_invalid")
    return object_json(value.encode())


def inspect_image(data):
    if (
        not isinstance(data, bytes)
        or not 100 <= len(data) <= MAX_DATABASE_BYTES
        or data[:16] != b"SQLite format 3\x00"
        or data[18] not in {1, 2}
        or data[19] not in {1, 2}
    ):
        raise InspectionError("bundle_sqlite_header_invalid")
    # WAL is forbidden by closure. A fully closed WAL-mode file still has journal-version 2.
    # Only this PRIVATE MEMORY COPY'S header is normalized for deserialize, never the source bytes.
    image = bytearray(data)
    image[18:20] = b"\x01\x01"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        if not hasattr(connection, "deserialize"):
            raise InspectionError("bundle_memory_deserialize_unavailable")
        connection.deserialize(bytes(image))
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        steps = 0

        def budget():
            nonlocal steps
            steps += 1
            return int(steps > 20000)  # At most 20 million VDBE operations over bounded captured bytes.

        def authorize(action, first, second, database, trigger):
            ok = action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
            if action == sqlite3.SQLITE_FUNCTION:
                ok = second == "count"
            return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY

        connection.set_progress_handler(budget, 1000)
        connection.set_authorizer(authorize)
        tables = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not TABLES.issubset(tables):
            raise InspectionError("bundle_ledger_schema_missing")
        versions = [
            tuple(row) for row in connection.execute("SELECT id, version FROM schema_version LIMIT 3")
        ]
        if versions != [(1, 2)]:
            raise InspectionError("bundle_ledger_schema_version")

        def select(query, *, maximum=MAX_ROWS):
            rows = [dict(row) for row in connection.execute(query + f" LIMIT {maximum + 1}")]
            if len(rows) > maximum:
                raise InspectionError("bundle_ledger_row_bound")
            return rows

        state = select(
            "SELECT id, desired_state, kill_switch_active, session_id, heartbeat, settings_overrides, "
            "last_config_hash FROM bot_state",
            maximum=1,
        )
        if (
            len(state) != 1
            or state[0]["id"] != 1
            or state[0]["desired_state"] not in {"paused", "killed"}
            or state[0]["kill_switch_active"] not in {0, 1}
            or state[0]["session_id"] is not None
            or state[0]["heartbeat"] is not None
            or sql_json(state[0]["settings_overrides"]) != {}
        ):
            raise InspectionError("bundle_ledger_runtime_not_released")
        # These research ledgers must not contain live-stage grants or genuine owner approvals.
        for table in ("deployment_evidence", "owner_approvals"):
            if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:
                raise InspectionError("bundle_research_ledger_contains_authorizations")
        signals = select("SELECT * FROM signals ORDER BY id")
        trades = select("SELECT * FROM trades ORDER BY open_time, id")
        intents = select("SELECT * FROM order_intents ORDER BY time, id")
        deals = select("SELECT * FROM broker_deals ORDER BY time, id")
        models = select("SELECT * FROM model_versions ORDER BY id", maximum=1)
        for rows in (signals, trades, intents, deals):
            if any(row["mode"] != "backtest" for row in rows):
                raise InspectionError("bundle_ledger_nonresearch_mode")
        for row in trades:
            for name in (
                "profit",
                "volume",
                "entry_price",
                "sl",
                "tp",
                "commission",
                "swap",
                "initial_risk_usd",
                "target_profit_usd",
            ):
                money(row[name])
        return {
            "control": state[0],
            "signals": signals,
            "trades": trades,
            "intents": intents,
            "deals": deals,
            "models": models,
        }
    except sqlite3.Error:
        raise InspectionError("bundle_memory_ledger_query_refused") from None
    finally:
        connection.close()
