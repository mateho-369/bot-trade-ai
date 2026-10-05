"""Engineering smoke builds TEMPORARY fake source/SQL, then exercises read-only diagnostics.

The smoke itself writes its disposable fixtures; the readiness/verifier operations do not.
NO genuine release attestation, platform/feed/owner/stage qualification, transport or order.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from core.database import Database
from core.models import BotState
from readiness.integrity import verify_release
from readiness.runner import preflight
from readiness.settings_inspection import inspect_settings
from readiness.sqlite_snapshot import inspect_sqlite


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(root):
    return {
        item.relative_to(root).as_posix(): (item.read_bytes(), item.stat().st_mode)
        for item in root.rglob("*")
        if item.is_file()
    }


def run():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="reflex-readiness-TEST_ONLY-") as name:
        root = Path(name)
        (root / "docs").mkdir()
        (root / "tests").mkdir()
        (root / "main.py").write_text('"""TEST ONLY miniature source."""\n')
        (root / "tests/test_telegram_initdata.py").write_text('"""TEST ONLY source, never a bearer."""\n')
        (root / "requirements.txt").write_text("pydantic==2.13.5\n")
        records = {
            item.relative_to(root).as_posix(): {
                "sha256": sha(item.read_bytes()),
                "bytes": item.stat().st_size,
            }
            for item in root.rglob("*")
            if item.is_file()
        }
        manifest = {
            "release": "0.10.0",
            "database_schema": 2,
            "files": records,
            "hashed_files": len(records),
            "packaged_files": len(records) + 1,
            "python_sources": 2,
            "manifest_self_hash_excluded": True,
        }
        path = root / "docs/RELEASE_15_MANIFEST.json"
        path.write_text(json.dumps(manifest))
        anchor = sha(path.read_bytes())
        result = verify_release(root, trusted_manifest_sha256=anchor)
        assert result.integrity_verified and result.trusted_anchor_supplied
        checks += 1
        assert not result.to_dict()["is_signature"] and not result.to_dict()["trading_permission"]
        checks += 1
        assert "tests/test_telegram_initdata.py" in records and result.declared_python_sources == 2
        checks += 1
        cfg, _, observations = inspect_settings(root)
        assert cfg is not None and not cfg.live_trading and cfg.mt5_backend == "mock" and cfg.start_paused
        checks += 1
        assert (
            not observations["telegram_pair_configured"] and not observations["mt5_login_triplet_configured"]
        )
        checks += 1
        # Explicit TEST_ONLY empty database creation, entirely in disposable fixture scope.
        database = Database(cfg)
        database.initialize()
        with database.session() as session:
            session.get(BotState, 1).kill_switch_active = True
        database.close()
        dbpath = root / "data/reflexbot.db"
        before = inventory(root)
        findings, data = inspect_sqlite(dbpath, root=root)
        assert data["schema_version_observed"] == 2 and not data["original_db_opened_by_sqlite"]
        checks += 1
        assert data["kill_switch_latched"] and any(
            item.code == "sqlite_snapshot_kill_latch_preserved" for item in findings
        )
        checks += 1
        assert inventory(root) == before
        checks += 1
        wal = Path(str(dbpath) + "-wal")
        wal.write_bytes(b"TEST_ONLY_PENDING_WAL_DO_NOT_DELETE")
        before = inventory(root)
        findings, _ = inspect_sqlite(dbpath, root=root)
        assert (
            any(item.code == "sqlite_pending_journal_or_wal" for item in findings)
            and inventory(root) == before
        )
        checks += 1
        assert not verify_release(root, trusted_manifest_sha256="0" * 64).integrity_verified
        checks += 1
        foreign = preflight(root, env_name=".env", inspect_sqlite_snapshot=True)
        assert foreign["overall"] == "blocked" and foreign["observations"] == {}
        checks += 1
        original = (root / "main.py").read_bytes()
        (root / "main.py").write_bytes(b"x" * len(original))
        assert not verify_release(root).integrity_verified
        checks += 1
        (root / "main.py").write_bytes(
            original
        )  # TEST ONLY source fixture, never financial or production state.
        (root / "unexpected.pyw").write_text("TEST_ONLY_UNTRACKED")
        assert not verify_release(root).integrity_verified
        checks += 1
        assert "MetaTrader5" not in sys.modules
        checks += 1
        return {
            "fixture_only": True,
            "checks_passed": checks,
            "native_sdk_imported": False,
            "credentials_used": False,
            "genuine_source_attestation": False,
            "eligible_stage_evidence": False,
            "actual_native_broker_calls": 0,
            "actual_provider_network_calls": 0,
            "actual_telegram_network_calls": 0,
            "actual_child_processes_spawned": 0,
            "real_orders": 0,
            "production_state_writes": 0,
            "readiness_operations_state_writes": 0,
            "smoke_creates_temporary_fixtures": True,
            "automatic_resume": False,
            "financial_history_reset": False,
        }


def main():
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
