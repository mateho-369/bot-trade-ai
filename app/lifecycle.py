"""Foreground lifecycle. Reconciliation precedes any gated autonomous DEMO resume."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from uuid import uuid4

from app.async_tools import durable_call
from app.dependencies import compose, require_existing_database
from app.health import RuntimeHealth, pending_native_calls
from app.process_guard import ProcessLock, operator_stop_requested, require_interactive_native, stop_requested
from app.scheduler import RuntimeScheduler
from core.database import Database

LOG = logging.getLogger("reflexbot.lifecycle")


class RuntimeLifecycle:
    def __init__(self, settings, *, identity=None, factory=compose):
        self.settings = settings
        self.health = RuntimeHealth(settings, identity or str(uuid4()))
        self.factory = factory
        self.lock = ProcessLock(settings.resolve_path(settings.runtime_lock_file))
        self.database = self.resources = self.scheduler = None
        self.alert_handler = None  # app.alerts.AlertLogHandler while the runtime is up.
        self.monitor_task = None
        self.stop_event = asyncio.Event()
        self._closed = False
        self._signals = {}
        self._ready_components: set[str] = set()
        self._ai_health_ok = False

    def install_signals(self):
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            kind = getattr(signal, name, None)
            if kind is None:
                continue
            try:
                previous = signal.getsignal(kind)
                signal.signal(kind, lambda *_: loop.call_soon_threadsafe(self.stop_event.set))
                self._signals[kind] = previous
            except ValueError:
                pass  # Explicit test/non-main thread; no implicit signal ownership.

    async def start(self):
        require_interactive_native(self.settings)
        self.lock.acquire()
        if operator_stop_requested(self.settings):
            self.stop_event.set()
            raise RuntimeError("persistent local stop request must be explicitly reviewed/cleared")
        # Existing DB only; no init_db, migration, capital reset or provider fetch.
        require_existing_database(self.settings)
        self.database = Database(self.settings)
        await durable_call(self.database.verify_schema)
        await durable_call(self.health.write)
        self.resources = await durable_call(self.factory, self.settings, self.database)
        r = self.resources
        self.health.resources = r
        if getattr(r, "alerts", None) is not None:
            from app.alerts import attach_log_handler

            self.alert_handler = attach_log_handler(r.alerts)
        self.monitor_task = asyncio.create_task(self.monitor(), name="runtime-health")
        await r.engine.initialize()  # Claims PAUSED, restores ledger and reconciles first.
        self.health.reconciled = True
        self._ready_components.add("execution")
        await self._try_auto_resume("startup")
        await r.signals.initialize()
        self._ready_components.add("signals")
        await self._try_auto_resume("signals_ready")
        await r.supervisor.initialize()
        self._ready_components.add("ai")
        await self._try_auto_resume("ai_ready")
        await r.news.initialize()  # Unknown until a scheduled explicit refresh.
        self._ready_components.add("news")
        self.health.components_ready = {"execution", "signals", "ai", "news"}.issubset(self._ready_components)
        await self._try_auto_resume("news_ready")
        self._ai_health_ok = await r.supervisor.health_check()
        self.health.ai_healthy = self._ai_health_ok
        if self._ai_health_ok:
            await self._try_auto_resume("ai_ready")
        self.scheduler = RuntimeScheduler(r, self.health, auto_resume=self._try_auto_resume)
        await durable_call(r.notices.enqueue, "started", dedup=self.health.identity)
        if self.stop_event.is_set():
            return
        self.scheduler.start()
        self.health.state = "ready"
        await durable_call(self.health.write)

    async def _try_auto_resume(self, trigger: str, *, ai_healthy: bool | None = None) -> bool:
        if ai_healthy is not None:
            self._ai_health_ok = ai_healthy
            self.health.ai_healthy = ai_healthy
        r = self.resources
        if not self.settings.autonomous_demo or r is None or not r.engine._initialized:
            return False
        components_ready = {"execution", "signals", "ai", "news"}.issubset(self._ready_components)
        try:
            resumed = await durable_call(
                r.engine.control.auto_resume,
                account_key=r.engine.account_key,
                components_ready=components_ready,
                ai_healthy=self._ai_health_ok,
                trigger=trigger,
            )
        except Exception:
            LOG.warning("AUTONOMOUS_DEMO resume gates could not be verified; entries remain paused")
            return False
        if resumed:
            LOG.warning(
                "AUTONOMOUS_DEMO entries resumed after reconciliation and all shared safety gates passed"
            )
            await durable_call(
                r.notices.enqueue,
                "auto_resumed",
                dedup=self.health.identity + ":" + r.broker.clock.now().isoformat(),
            )
            if getattr(r, "alerts", None) is not None:
                r.alerts.emit(
                    "WARNING",
                    "Runtime",
                    "AUTONOMOUS_DEMO entries resumed after safety gates passed",
                    action="Local operator may pause or stop with python -m scripts.ops",
                )
        return resumed

    async def monitor(self):
        last_renewal = time.monotonic()
        while not self._closed:
            try:
                if stop_requested(self.settings, self.health.identity) or operator_stop_requested(
                    self.settings
                ):
                    self.stop_event.set()
                r = self.resources
                if self.stop_event.is_set() and r is not None and r.engine._initialized:
                    if time.monotonic() - last_renewal >= self.settings.heartbeat_interval_seconds:
                        await durable_call(r.engine.control.heartbeat)
                        last_renewal = time.monotonic()
                await durable_call(self.health.write)
            except Exception as error:
                # Surface the failure kind for diagnosis; storage errors carry no
                # credentials, but never dump tracebacks or raw broker payloads.
                LOG.error(
                    "Runtime health/storage unavailable; graceful stop requested (%s)",
                    type(error).__name__,
                )
                self.stop_event.set()
            await asyncio.sleep(1)

    async def stop(self):
        if self._closed:
            return
        if self.lock.handle is None:
            self._closed = True
            for kind, previous in self._signals.items():
                signal.signal(kind, previous)
            return  # Never overwrite the health of another lock-owning runtime.
        self.stop_event.set()
        self.health.state = "stopping"
        r = self.resources
        # Fence in memory BEFORE attempting SQL: transient pause persistence
        # failure must not leave the entry scheduler accepting work until retry.
        if self.scheduler is not None:
            self.scheduler.stop_accepting()
        if r is not None and r.engine.control.session_id is not None:
            await durable_call(r.engine.control.pause_for_shutdown)
        if self.scheduler is not None:
            while not await self.scheduler.drain(self.settings.runtime_shutdown_seconds):
                LOG.error("Active job remains; no force cancellation/replacement is permitted")
        if self.scheduler is not None:
            self.scheduler.finish()
        if r is not None:
            try:
                await r.engine.shutdown()
            except Exception:
                LOG.error("Broker shutdown uncertain; hold DB/OS lock until native calls actually finish")
            while pending_native_calls(r.broker):
                # A timed-out native Future is still real work. Leave DB/lease evidence intact.
                await asyncio.sleep(1)
            await r.news.close()
            await r.supervisor.close()
            if getattr(r, "ai_first", None) is not None:
                await r.ai_first.close()
            if getattr(r, "alerts", None) is not None:
                from app.alerts import detach_log_handler

                detach_log_handler(getattr(self, "alert_handler", None))
                self.alert_handler = None
                try:  # Last delivery of pending alerts (bounded; never blocks shutdown on errors).
                    await asyncio.wait_for(r.alerts.flush(getattr(r, "reporter", None)), timeout=10)
                except Exception:
                    LOG.warning("Final alert flush incomplete")
            await durable_call(r.notices.enqueue, "stopped", dedup=self.health.identity)
            try:
                await asyncio.wait_for(r.notices.drain(limit=10), timeout=6)
            except Exception:
                LOG.warning("Final local/outbound report drain incomplete")
            reporter = getattr(r, "reporter", None)
            if reporter is not None:
                await reporter.close()
        self.health.state = "stopped"
        await durable_call(self.health.write)
        self._closed = True
        if self.monitor_task is not None:
            await self.monitor_task
        if self.database is not None:
            self.database.close()
        self.lock.release()
        for kind, previous in self._signals.items():
            signal.signal(kind, previous)

    async def run(self):
        self.install_signals()
        try:
            await self.start()
            await self.stop_event.wait()
        finally:
            # Keep a strong reference and OS lock during storage/native recovery.
            # Exiting early could let another runtime start while a native callback
            # still uses this database. No effect is resubmitted by these retries.
            while not self._closed:
                try:
                    await self.stop()
                except Exception:
                    LOG.error("Shutdown incomplete; resources/OS lock retained, no force kill")
                    await asyncio.sleep(5)
