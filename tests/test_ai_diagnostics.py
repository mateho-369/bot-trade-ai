import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_ai import run


async def test_diagnostic_ignores_host_flags_secrets_and_cleans_temp_state(tmp_path, monkeypatch):
    for name, value in {
        "LIVE_TRADING": "true",
        "PAPER_TRADING": "false",
        "DEMO_MODE": "false",
        "MT5_BACKEND": "real",
        "MT5_PASSWORD": "HOST_SECRET",
        "OPENAI_API_KEY": "HOST_OPENAI_SECRET",
        "TELEGRAM_REPORT_CHAT_ID": "42",
        "TELEGRAM_BOT_TOKEN": "HOST_REPORT_SECRET",
        "PROJECT_ROOT": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    result = await run()
    assert (
        result["source"] == "synthetic"
        and result["actual_http_network_calls"] == 0
        and result["real_orders_sent"] == 0
    )
    assert not result["native_sdk_imported"] and not result["eligible_stage_evidence"]
    assert (
        result["candidate_was_inactive"]
        and result["synthetic_promotion_vetoed"]
        and result["running_settings_were_not_mutated"]
    )
    assert (
        result["reconciled_trade_labels"] == 1
        and result["synthetic_toy_training_rows"] == 480
        and result["purged_oos_folds"] == 5
    )
    assert not list(tmp_path.rglob("*.db")) and not list(tmp_path.rglob("*.json"))
    assert "HOST_SECRET" not in json.dumps(result) and "HOST_OPENAI_SECRET" not in json.dumps(result)


def test_standalone_cli_is_offline_only_with_clear_warning():
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/home/user")}
    if sys.platform == "win32":
        # Winsock cannot initialize without these; none of them carry trading flags.
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    p = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_ai"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    assert (
        result["confidence_is_scripted_not_an_ai_assessment"]
        and "NOT provider evaluation" in result["warning"]
    )
