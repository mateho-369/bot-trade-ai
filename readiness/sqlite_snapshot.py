"""Inspect a bounded main-file COPY IN MEMORY only; NEVER open the original DB with SQLite.

Nonempty WAL/hot rollback journal => refusal. No checkpoint, WAL replay, migration,
sidecar creation, state/control write or reset. Observed snapshot is NOT a live lock.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from readiness.contracts import Finding, InspectionError
from readiness.files import checked_path, identity, read_bytes

DB_MAX_BYTES = 64 * 1024 * 1024
TABLES = frozenset(
    {
        "schema_version",
        "broker_deals",
        "order_intents",
        "trades",
        "signals",
        "news",
        "ai_suggestions",
        "model_versions",
        "risk_events",
        "audit_logs",
        "bot_state",
        "account_snapshots",
        "risk_state",
        "deployment_evidence",
        "owner_approvals",
    }
)


def sidecar_state(path, *, root):
    result = {}
    for suffix in ("-wal", "-journal", "-shm"):
        item = checked_path(Path(str(path) + suffix), root=root, missing=True)
        if item.exists():
            info = item.lstat()
            if not item.is_file() or info.st_nlink != 1:
                raise InspectionError("linked_or_nonregular_sqlite_sidecar")
            if suffix in {"-wal", "-journal"} and info.st_size:
                raise InspectionError("sqlite_pending_journal_or_wal")
            result[suffix] = identity(info)
        else:
            result[suffix] = None
    return result


def _queries(data):
    if (
        len(data) < 100
        or data[:16] != b"SQLite format 3\x00"
        or data[18] not in {1, 2}
        or data[19] not in {1, 2}
    ):
        raise InspectionError("sqlite_header_invalid")
    # deserialize refuses a WAL-mode image even when its WAL was fully checkpointed.
    # Change COPY'S journal header only; original bytes/state/capital are never altered.
    image = bytearray(data)
    image[18:20] = b"\x01\x01"
    connection = sqlite3.connect(":memory:")
    try:
        if not hasattr(connection, "deserialize"):
            raise InspectionError("sqlite_memory_deserialize_unavailable")
        connection.deserialize(bytes(image))
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        steps = 0

        def budget():
            nonlocal steps
            steps += 1
            return int(steps > 2000)  # ≤2 million VDBE operations, not unbounded count/poisoned views.

        connection.set_progress_handler(budget, 1000)

        def authorize(action, first, second, database, trigger):
            allowed = action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
            if action == sqlite3.SQLITE_FUNCTION:
                allowed = second == "count"
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        present = {row[0] for row in tables}
        if not TABLES.issubset(present):
            raise InspectionError("sqlite_required_schema_missing")
        versions = connection.execute("SELECT id, version FROM schema_version LIMIT 3").fetchall()
        if versions != [(1, 2)]:
            raise InspectionError("sqlite_schema_version_not_current")
        states = connection.execute(
            "SELECT id, desired_state, kill_switch_active, session_id FROM bot_state LIMIT 3"
        ).fetchall()
        if (
            len(states) != 1
            or states[0][0] != 1
            or states[0][1] not in {"paused", "running"}
            or states[0][2] not in {0, 1}
        ):
            raise InspectionError("sqlite_control_singleton_invalid")
        _, desired, killed, session = states[0]
        trades = connection.execute(
            "SELECT count(*) FROM trades WHERE status IN ('open', 'unknown')"
        ).fetchone()[0]
        intents = connection.execute(
            "SELECT count(*) FROM order_intents WHERE state NOT IN ('reconciled', 'rejected', 'canceled')"
        ).fetchone()[0]
        return {
            "schema_version_observed": 2,
            "required_tables_observed": len(TABLES),
            "desired_state": desired,
            "kill_switch_latched": bool(killed),
            "runtime_session_present": session is not None,
            "open_or_unknown_trade_count": trades,
            "unsettled_intent_count": intents,
            "original_db_opened_by_sqlite": False,
            "authoritative_running_state": False,
        }
    except sqlite3.Error:
        raise InspectionError("sqlite_memory_inspection_refused") from None
    finally:
        connection.close()


def inspect_sqlite(path, *, root):
    findings, observations = [], {"original_db_opened_by_sqlite": False}
    try:
        path = checked_path(path, root=root)
        before = sidecar_state(path, root=root)
        data = read_bytes(path, root=root, limit=DB_MAX_BYTES)
        observations = _queries(data)
        if sidecar_state(path, root=root) != before:
            raise InspectionError("sqlite_sidecars_changed_during_inspection")
        if read_bytes(path, root=root, limit=DB_MAX_BYTES) != data:
            raise InspectionError("sqlite_main_changed_during_inspection")
        findings.append(
            Finding(
                "sqlite_memory_snapshot_observed",
                "passed",
                "Schema/control exposure was queried only in a bounded read-only in-memory copy; no "
                "original SQL connection/checkpoint occurred.",
            )
        )
        if observations["desired_state"] != "paused" or observations["runtime_session_present"]:
            findings.append(
                Finding(
                    "sqlite_snapshot_runtime_not_stopped_paused",
                    "blocked",
                    "Snapshot does not show stopped/paused state. Use normal graceful "
                    "stop/reconciliation, never reset/cancel records to satisfy a check.",
                )
            )
        if observations["open_or_unknown_trade_count"] or observations["unsettled_intent_count"]:
            findings.append(
                Finding(
                    "sqlite_snapshot_exposure_or_uncertain_intents",
                    "blocked",
                    "Observed exposure/unsettled intents require ordinary ownership/reconciliation; this "
                    "tool never closes, retries or deletes them.",
                )
            )
        if observations["kill_switch_latched"]:
            findings.append(
                Finding(
                    "sqlite_snapshot_kill_latch_preserved",
                    "warning",
                    "Stored kill switch is latched and remains unchanged. A diagnostic never clears "
                    "loss/recovery/owner controls.",
                )
            )
    except InspectionError as exc:
        observations = {"original_db_opened_by_sqlite": False}
        findings.append(
            Finding(
                exc.code,
                "blocked",
                "SQLite snapshot could not be safely observed; keep DB/WAL/checkpoint together and "
                "stop/reconcile normally, without a reset.",
            )
        )
    except Exception:
        observations = {"original_db_opened_by_sqlite": False}
        findings.append(
            Finding(
                "sqlite_snapshot_inspection_refused",
                "blocked",
                "SQLite inspection failed without exposing original SQL/file values or writing state.",
            )
        )
    return tuple(findings), observations
