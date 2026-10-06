"""30-second default supervisor. Only replaces its CONFIRMED EXITED direct child.

No terminate/kill, PID-file adoption, resume, latch reset or order resubmission.
If a native thread or request remains alive, stop is requested and replacement is
withheld. Restart reservations are append-only SQL audit records, not RAM counters.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psutil
from sqlalchemy import func, select

from app.dependencies import require_existing_database
from app.notifications import RuntimeNotices
from app.process_guard import (
    ProcessLock,
    atomic_json,
    operator_stop_path,
    operator_stop_requested,
    read_json,
    request_stop,
    require_interactive_native,
)
from app.reporter import Reporter
from core.database import Database
from core.logging_setup import configure_logging
from core.models import AuditLog, BotState
from core.settings import Settings, live_trading_requested
from trading.risk_types import source_code_hash
from trading.types import SystemClock

LOG = logging.getLogger("reflexbot.watchdog")


def child_command(env_file, identity):
    prefix = (
        [sys.executable, "--runtime-child"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "app.bot"]
    )
    return prefix + ["--env-file", str(env_file.resolve()), "--managed-id", identity]


class Watchdog:
    def __init__(
        self, settings, database, env_file, *, popen=subprocess.Popen, monotonic=time.monotonic, clock=None
    ):
        self.settings, self.database = settings, database
        self.env_file, self.popen, self.monotonic = Path(env_file).resolve(), popen, monotonic
        self.clock = clock or SystemClock()
        self.env_digest = hashlib.sha256(self.env_file.read_bytes()).hexdigest()
        self.code_hash = source_code_hash(settings.project_root)
        self.reporter = Reporter(settings, clock=self.clock)
        self.notices = RuntimeNotices(database, settings, self.clock, self.reporter)
        self.child = self.identity = self.created = None
        self.launched_at = None
        self.stalled = False
        self.ever_started = False
        self.stopping = threading.Event()
        self.budget_reported = False
        self.ready_seen = False

    def _drain_notices(self):
        try:
            asyncio.run(self.notices.drain(limit=10))
        except Exception:
            LOG.warning("Watchdog report delivery failed; local reports remain available")

    def unchanged(self):
        return (
            hashlib.sha256(self.env_file.read_bytes()).hexdigest() == self.env_digest
            and source_code_hash(self.settings.project_root) == self.code_hash
        )

    def reserve_restart(self, identity):
        with self.database.locked_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "watchdog.launch_reserved",
                    AuditLog.time >= self.clock.now() - timedelta(hours=1),
                )
            )
            if count >= self.settings.watchdog_max_restarts_per_hour:
                return False
            row = self.database.add_audit(
                session,
                "watchdog.launch_reserved",
                "watchdog",
                {
                    "managed_id": identity,
                    "always_start_paused": True,
                    "reason": "child_exited" if self.ever_started else "explicit_supervisor_start",
                },
            )
            row.time = self.clock.now()
            return True

    def runtime_available(self):
        # Never adopt/kill an existing runtime. An expired SQL lease alone is not
        # evidence that a stuck process/thread is gone: also probe its OS lock.
        try:
            with ProcessLock(self.settings.resolve_path(self.settings.runtime_lock_file)):
                with self.database.session() as session:
                    state = session.get(BotState, 1)
                    if state is None:
                        return False
                    if state.session_id is not None:
                        if state.heartbeat is None:
                            return False
                        age = (self.clock.now() - state.heartbeat).total_seconds()
                        if age < self.settings.runtime_lease_seconds:
                            return False
                return True
        except RuntimeError:
            return False

    def launch(self):
        identity = str(uuid4())
        if not self.reserve_restart(identity):
            if not self.budget_reported:
                self.notices.enqueue("budget", dedup="budget:" + self.clock.now().strftime("%Y%m%d%H"))
                self._drain_notices()
                self.budget_reported = True
            return "budget_exhausted"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.child = self.popen(
            child_command(self.env_file, identity),
            cwd=self.settings.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=flags,
        )
        self.identity, self.launched_at, self.stalled = identity, self.monotonic(), False
        self.ready_seen = False
        try:
            self.created = psutil.Process(self.child.pid).create_time()
        except psutil.NoSuchProcess:
            self.created = None  # A short-lived child still consumed its reservation.
        self.database.audit(
            "watchdog.child_launched",
            "watchdog",
            {
                "managed_id": identity,
                "pid": self.child.pid,
                "readiness_not_confirmed": True,
            },
        )
        if self.ever_started:
            self.notices.enqueue("restart", dedup=identity)
            self._drain_notices()
        self.ever_started = True
        return "launched_paused"

    def health_ok(self):
        data = read_json(self.settings.resolve_path(self.settings.runtime_health_file))
        pid = data.get("pid")
        pid_ok = pid == self.child.pid
        if not pid_ok and type(pid) is int:
            # Windows venv launchers spawn the real interpreter as a child of the
            # spawned process, so os.getpid() inside the runtime differs from
            # Popen.pid. Accept the health file of the interpreter this supervisor
            # actually spawned; managed_id still binds it to THIS launch.
            try:
                pid_ok = self.child.pid in (p.pid for p in psutil.Process(pid).parents())
            except psutil.Error:
                pid_ok = False
        created_ok = False
        if type(data.get("process_created")) is float and type(pid) is int:
            try:
                # Bind the file to a LIVE process with that exact creation time;
                # a stale file from a dead/replaced pid fails closed here.
                created_ok = psutil.Process(pid).create_time() == data["process_created"]
            except psutil.Error:
                created_ok = False
        if (
            not pid_ok
            or not created_ok
            or data.get("managed_id") != self.identity
            or data.get("config_hash") != self.settings.safety_fingerprint()
            or data.get("not_live_authorization") is not True
            or type(data.get("schema")) is not int
            or data.get("schema") != 1
        ):
            return False
        updated = datetime.fromisoformat(data["updated_at"])
        if updated.tzinfo is None or updated.utcoffset() != timedelta(0):
            return False
        age = (self.clock.now() - updated).total_seconds()
        if not -5 <= age <= self.settings.watchdog_stale_seconds:
            return False
        if data.get("status") == "starting":
            return (
                not self.ready_seen
                and self.monotonic() - self.launched_at <= self.settings.watchdog_startup_grace_seconds
            )
        import re

        expected_source = "synthetic" if self.settings.mt5_backend == "mock" else "mt5"
        healthy = (
            data.get("source") == expected_source
            and data.get("control") in {"paused", "running", "killed"}
            and isinstance(data.get("model_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", data["model_sha256"]) is not None
            and isinstance(data.get("session_id"), str)
            and data.get("status") in {"ready", "degraded"}
            and data.get("code_hash") == self.code_hash
            and not data.get("overdue_jobs")
            and data.get("writes_quarantined") is False
        )
        if healthy:
            self.ready_seen = True
        return healthy

    def tick(self):
        if self.child is not None and self.child.poll() is not None:
            self.database.audit(
                "watchdog.child_exited",
                "watchdog",
                {
                    "managed_id": self.identity,
                    "exit_confirmed": True,
                    "unknown_intents_must_reconcile": True,
                },
            )
            self.child = None
        if operator_stop_requested(self.settings):
            self.stopping.set()
        if not self.unchanged():
            if self.child is not None:
                self.request_child_stop()
            self.stopping.set()
            return "configuration_or_code_changed_stop_supervisor"
        if self.stopping.is_set():
            if self.child is not None:
                self.request_child_stop()
            return "stopping"
        if self.child is None:
            if not self.runtime_available():
                return "waiting_for_runtime_lock_or_lease"
            return self.launch()
        try:
            healthy = self.health_ok()
        except (OSError, ValueError, KeyError, TypeError):
            healthy = False
        if healthy:
            return "healthy" if not self.stalled else "stop_requested_replacement_withheld"
        if (
            not self.ready_seen
            and self.monotonic() - self.launched_at <= self.settings.watchdog_startup_grace_seconds
            and not self.stalled
        ):
            return "startup_grace"
        self.request_child_stop()
        return "stop_requested_replacement_withheld"

    def request_child_stop(self):
        request_stop(self.settings, self.identity)
        if not self.stalled:
            self.notices.enqueue("stalled", dedup=self.identity)
            self._drain_notices()
            self.database.audit(
                "watchdog.replacement_withheld",
                "watchdog",
                {
                    "managed_id": self.identity,
                    "child_still_alive": True,
                    "no_force_kill": True,
                },
            )
            self.stalled = True

    def run(self, *, clear_stop_request=False):
        require_interactive_native(self.settings)
        with ProcessLock(self.settings.resolve_path(self.settings.watchdog_lock_file)):
            if clear_stop_request:
                atomic_json(operator_stop_path(self.settings), {"stop": False})
            while not self.stopping.is_set():
                try:
                    self.tick()
                except Exception:
                    LOG.error("Watchdog unavailable; no blind child restart or raw error output")
                    self.stopping.set()
                self.stopping.wait(self.settings.watchdog_interval_seconds)
            if self.child is not None:
                self.request_child_stop()
                # Intentionally unbounded: cannot assume an unknown broker write is dead.
                self.child.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit 30-second ReflexBot supervisor; never auto-resume")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--clear-stop-request",
        action="store_true",
        help="explicitly clear ONLY the local supervisor stop request, never a safety latch",
    )
    args = parser.parse_args(argv)
    database = None
    previous = {}
    try:
        if not args.env_file.is_file():
            print("Reviewed .env required. Watchdog does not initialize or migrate databases.")
            return 2
        if live_trading_requested(args.env_file):
            print("LIVE_TRADING=true is refused in this build; live orders cannot be started.")
            return 2
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        if not (settings.project_root / "main.py").is_file():
            raise ValueError("environment file must be in the deployed source root")
        require_interactive_native(settings)
        configure_logging(settings)
        require_existing_database(settings)
        database = Database(settings)
        database.verify_schema()
        supervisor = Watchdog(settings, database, args.env_file)
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            kind = getattr(signal, name, None)
            if kind is not None:
                previous[kind] = signal.getsignal(kind)
                signal.signal(kind, lambda *_: supervisor.stopping.set())
        supervisor.run(clear_stop_request=args.clear_stop_request)
        return 0
    except Exception:
        LOG.error("Watchdog startup/shutdown failed; raw credentials and errors suppressed")
        return 1
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
