import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_signals import run


async def test_diagnostic_ignores_host_modes_credentials_and_cleans_temp_state(tmp_path, monkeypatch):
    for name, value in {
        "LIVE_TRADING": "true",
        "DEMO_MODE": "false",
        "PAPER_TRADING": "false",
        "MT5_BACKEND": "real",
        "MT5_PASSWORD": "TEST_HOST_SECRET",
        "TELEGRAM_BOT_TOKEN": "TEST_HOST_TOKEN",
        "TELEGRAM_OWNER_ID": "not-a-number",
        "PROJECT_ROOT": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    result = await run()
    assert (
        result["source"] == "synthetic"
        and not result["eligible_stage_evidence"]
        and not result["native_sdk_imported"]
    )
    assert result["real_orders_sent"] == 0 and result["entry_intents"] == 1 and result["positions"] == 0
    assert result["missing_reviews_vetoed"] and result["approved_signal_did_not_auto_resume"]
    assert result["duplicate_entry_sent_once"] and result["closed_signal_did_not_reopen"]
    assert not list(tmp_path.rglob("*.db")) and not list(tmp_path.rglob("state.json"))
    assert "TEST_HOST_SECRET" not in json.dumps(result)


def test_cli_is_synthetic_only_with_explicit_warning():
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/home/user")}
    if sys.platform == "win32":
        # Winsock cannot initialize without these; none of them carry trading flags.
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    command = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_signals"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert command.returncode == 0, command.stderr
    result = json.loads(command.stdout)
    assert result["simulated_only"] and result["real_orders_sent"] == 0
    assert result["ai_confidence_is_scripted"] and not result["aggregation_consistent_historical_data"]
    assert "NOT strategy profitability" in result["warning"]
