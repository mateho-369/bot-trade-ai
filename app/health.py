"""Bounded local health evidence. No account numbers, positions or secrets."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from threading import RLock

import psutil
from sqlalchemy import select

from app.process_guard import atomic_json, managed_id
from core.models import BotState


def pending_native_calls(broker):
    count = broker.health().get("pending_calls", 0) if hasattr(broker, "health") else 0
    market = getattr(broker, "market", None)
    if market is not None and market is not broker:
        count += pending_native_calls(market)
    return count


class RuntimeHealth:
    def __init__(self, settings, identity: str, *, monotonic=time.monotonic):
        self.settings, self.identity = settings, managed_id(identity)
        self.monotonic = monotonic
        self.pid = os.getpid()
        self.created = psutil.Process(self.pid).create_time()
        self.started = monotonic()
        self.state = "starting"
        self.reconciled = False
        self.components_ready = False
        self.ai_healthy = False
        self.jobs = {}
        self._lock = RLock()
        self.resources = None

    def job_started(self, name):
        with self._lock:
            row = self.jobs.setdefault(name, {})
            row.update(started=self.monotonic(), active=True)

    def job_finished(self, name, *, success):
        with self._lock:
            self.jobs[name].update(active=False, finished=self.monotonic(), success=bool(success))

    def job_snapshot(self):
        with self._lock:
            return {name: dict(row) for name, row in self.jobs.items()}

    def overdue(self):
        now = self.monotonic()
        return sorted(
            name
            for name, row in self.job_snapshot().items()
            if row.get("active") and now - row["started"] > self.settings.runtime_job_max_seconds
        )

    def write(self):
        control, session, quarantined = "paused", None, False
        profile = None
        if self.resources is not None:
            resources = self.resources
            profile = resources.engine.profile
            with resources.database.session() as db:
                row = db.scalar(select(BotState).where(BotState.id == 1))
                if row is not None:
                    control, session = row.desired_state, row.session_id
            quarantined = resources.broker.health().get("writes_quarantined") is True
        overdue = self.overdue()
        snapshot = self.job_snapshot()
        failed = sorted(name for name, row in snapshot.items() if row.get("success") is False)
        status = self.state
        if status == "ready" and (overdue or quarantined or failed):
            status = "degraded"
        payload = {
            "schema": 1,
            "pid": self.pid,
            "process_created": self.created,
            "managed_id": self.identity,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "reconciled": self.reconciled,
            "components_ready": self.components_ready,
            "ai_healthy": self.ai_healthy,
            "control": control,
            "session_id": session,
            "config_hash": self.settings.safety_fingerprint(),
            "code_hash": profile.code_hash if profile else None,
            "model_sha256": profile.model_sha256 if profile else None,
            "source": profile.data_source.value if profile else None,
            "writes_quarantined": quarantined,
            "pending_native_calls": pending_native_calls(self.resources.broker) if self.resources else 0,
            "overdue_jobs": overdue,
            "failed_jobs": failed,
            "jobs": {
                name: {"active": row.get("active", False), "success": row.get("success")}
                for name, row in sorted(snapshot.items())
            },
            "not_live_authorization": True,
        }
        atomic_json(self.settings.resolve_path(self.settings.runtime_health_file), payload)
        return payload
