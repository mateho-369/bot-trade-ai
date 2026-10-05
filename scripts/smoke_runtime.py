"""TEST_ONLY offline runtime + scripted supervisor. Ignores dotenv/process env.

No Telegram/provider/native broker calls, child-process spawn, order or stage
eligibility. SQL bootstrap here is an isolated TEST fixture, never production.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.backups import create_backup
from app.lifecycle import RuntimeLifecycle
from app.process_guard import read_json
from core.database import Database
from core.models import BotState, OrderIntent
from core.settings import Settings
from watchdog import Watchdog


class OfflineRuntimeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # Inherited LIVE_TRADING/tokens/URLs cannot alter this smoke.


class ScriptedChild:
    """Fake Popen handle, not an actual process, native call or permission seam."""

    def __init__(self):
        self.pid, self.exit = os.getpid(), None

    def poll(self):
        return self.exit


async def run(directory):
    settings = OfflineRuntimeSettings(
        _env_file=None,
        project_root=directory,
        symbols=("EURUSD",),
        runtime_api_enabled=False,
        runtime_telegram_enabled=False,
        runtime_backup_enabled=False,
        ai_provider="disabled",
        ai_fallback_provider="disabled",
    )
    db = Database(settings)
    db.initialize()
    db.close()
    service = RuntimeLifecycle(settings)
    checks = {}
    await service.start()
    try:
        r, jobs = service.resources, service.scheduler
        checks["always_start_paused"] = r.database.status()["state"] == "paused"
        checks["same_guarded_resources"] = r.owner.execution is r.engine and r.owner.news is r.news
        checks["paused_entry_job_has_no_effect"] = (await jobs.run_job("signals"))["executed"] == 0
        checks["paused_protection_still_runs"] = (await jobs.run_job("positions"))["managed_positions"] == 0
        await jobs.run_job("news")
        checks["unconfigured_news_unknown"] = not (await r.news.window("EURUSD")).known
        checks["no_telegram_transport"] = r.telegram is None
        checks["learning_disabled"] = (await jobs.run_job("learning"))["state"] == "disabled"
        with r.database.session() as sql:
            checks["no_order_intents"] = sql.scalar(select(OrderIntent.id)) is None
    finally:
        await service.stop()
    health = read_json(settings.resolve_path(settings.runtime_health_file))
    checks["shutdown_paused_released"] = health["control"] == "paused" and health["session_id"] is None
    db = Database(settings)
    try:
        backup = create_backup(settings, db, coherent=True)
        checks["stopped_recovery_backup"] = backup.is_file()
        env_file = directory / ".env.TEST_ONLY"
        env_file.write_text("# TEST_ONLY scripted supervisor, no actual child launch\n")
        children = []

        def scripted_popen(*args, **kwargs):
            child = ScriptedChild()
            children.append(child)
            return child

        supervisor = Watchdog(settings, db, env_file, popen=scripted_popen)
        checks["scripted_supervisor_launch_paused"] = supervisor.tick() == "launched_paused"
        children[0].exit = 1
        checks["scripted_confirmed_exit_restart"] = (
            supervisor.tick() == "launched_paused" and len(children) == 2
        )
        with db.session() as sql:
            checks["scripted_restart_never_resumes"] = sql.get(BotState, 1).desired_state == "paused"
    finally:
        db.close()
    assert all(checks.values()), checks
    return {
        "fixture_only": True,
        "checks_passed": len(checks),
        "checks": checks,
        "actual_telegram_network_calls": 0,
        "actual_provider_network_calls": 0,
        "actual_native_broker_calls": 0,
        "actual_child_processes_spawned": 0,
        "real_orders": 0,
        "simulated_order_operations": 0,
        "native_sdk_imported": "MetaTrader5" in sys.modules,
        "eligible_stage_evidence": False,
        "credentials_loaded": False,
        "automatic_resume": False,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="reflex-runtime-TEST_ONLY-") as directory:
        result = asyncio.run(run(Path(directory)))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
