import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_mock import run

ROOT = Path(__file__).resolve().parents[1]


async def test_smoke_is_synthetic_balanced_and_not_promotion_evidence(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("MT5_BACKEND", "real")
    monkeypatch.setenv("ACCOUNT_CURRENCY", "USC")
    monkeypatch.setenv("MT5_PASSWORD", "do-not-read-this-in-a-mock-smoke")
    report = await run()
    assert report["source"] == "synthetic" and report["simulated_only"]
    assert not report["eligible_stage_evidence"] and report["real_orders_sent"] == 0
    assert report["positions"] == 0 and report["deals"] == 2
    assert report["finalized_candles"] == 300


def test_smoke_module_cli_does_not_touch_db_or_native_sdk(tmp_path):
    before = {path.name for path in tmp_path.iterdir()}
    environment = dict(os.environ, PYTHONPATH=str(ROOT), LIVE_TRADING="true", MT5_BACKEND="real")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_mock"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["source"] == "synthetic" and report["duplicate_open_sent_once"]
    assert before == {path.name for path in tmp_path.iterdir()}


def test_readonly_diagnostic_cannot_connect_native_on_linux():
    if sys.platform == "win32":
        return  # No automatic native connection is allowed even in Windows tests.
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_mt5_readonly", "--env-file", ".env.example"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["error"] == "ConnectionUnavailable" and report["real_orders_sent"] == 0
