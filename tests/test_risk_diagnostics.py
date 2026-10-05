import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_risk import run

ROOT = Path(__file__).resolve().parents[1]


async def test_risk_smoke_is_isolated_synthetic_and_not_promotion(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("MT5_BACKEND", "real")
    monkeypatch.setenv("MT5_PASSWORD", "NEVER_READ_THIS")
    report = await run()
    assert report["source"] == "synthetic" and not report["eligible_stage_evidence"]
    assert report["real_orders_sent"] == 0 and not report["native_sdk_imported"]
    assert report["entries_today"] == 1 and report["reserved_risk_usd"] == "0E-8"
    assert report["startup_pause_veto_verified"] and report["restart_started_paused"]
    assert report["verified_historical_lock_level"] == 30 and report["positions"] == 0


def test_smoke_cli_ignores_env_and_leaves_no_local_files(tmp_path):
    before = set(tmp_path.iterdir())
    process = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_risk"],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT), LIVE_TRADING="true", MT5_BACKEND="real"),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    report = json.loads(process.stdout)
    assert report["simulated_only"] and report["real_orders_sent"] == 0
    assert report["duplicate_entry_sent_once"] and set(tmp_path.iterdir()) == before
