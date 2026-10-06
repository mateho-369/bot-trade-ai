"""Explicit foreground lifecycle. No migration, automatic resume or force kill."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket
import time
from uuid import uuid4

import uvicorn

from app.async_tools import durable_call
from app.dependencies import attach_telegram, compose, require_existing_database
from app.health import RuntimeHealth, pending_native_calls
from app.process_guard import ProcessLock, operator_stop_requested, require_interactive_native, stop_requested
from app.scheduler import RuntimeScheduler
from core.database import Database
from miniapp.server import create_app

LOG = logging.getLogger("reflexbot.lifecycle")


class OwnerAPIServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # Runtime owns signals. Uvicorn must not cancel guarded writes on SIGINT.
        yield


class RuntimeLifecycle:
    def __init__(self, settings, *, identity=None, factory=compose):
        self.settings = settings
        self.health = RuntimeHealth(settings, identity or str(uuid4()))
        self.factory = factory
        self.lock = ProcessLock(settings.resolve_path(settings.runtime_lock_file))
        self.database = self.resources = self.scheduler = self.api = self.api_socket = None
        self.alert_handler = None  # app.alerts.AlertLogHandler while the runtime is up.
        self.api_task = self.poll_task = self.monitor_task = None
        self.stop_event = asyncio.Event()
        self._closed = False
        self._signals = {}

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
        await r.engine.initialize()  # Claims PAUSED, restores ledger and reconciles.
        await r.signals.initialize()
        await r.supervisor.initialize()
        await r.news.initialize()  # Unknown until a scheduled explicit refresh.
        self.scheduler = RuntimeScheduler(r, self.health)
        await durable_call(r.notices.enqueue, "started", dedup=self.health.identity)
        if self.stop_event.is_set():
            return
        attach_telegram(r)
        if self.settings.runtime_api_enabled:
            application = create_app(
                self.settings,
                r.owner,
                telegram_transport=r.telegram if self.settings.telegram_use_webhook else None,
            )
            self.api = OwnerAPIServer(
                uvicorn.Config(
                    application,
                    host=self.settings.api_host,
                    port=self.settings.api_port,
                    workers=1,
                    access_log=False,
                    proxy_headers=False,
                    log_config=None,
                    timeout_keep_alive=5,
                    timeout_graceful_shutdown=None,
                )
            )
            # Bind before spawning serve(): bind failures never raise SystemExit inside a task.
            self.api_socket = socket.socket(
                socket.AF_INET6 if ":" in self.settings.api_host else socket.AF_INET
            )
            self.api_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.api_socket.bind((self.settings.api_host, self.settings.api_port))
            self.api_socket.listen(64)
            self.api_socket.setblocking(False)
            self.api_task = asyncio.create_task(self.api.serve(sockets=[self.api_socket]), name="owner-api")
            while not self.api.started:
                if self.api_task.done():
                    await self.api_task
                    raise RuntimeError("owner API exited before readiness")
                await asyncio.sleep(0.05)
        if r.telegram is not None:
            if self.settings.runtime_register_menu:
                await r.telegram.set_owner_menu()
            if self.settings.telegram_use_webhook:
                await r.telegram.register_webhook()  # Explicit reviewed .env chooses webhook mode.
            else:
                self.poll_task = asyncio.create_task(r.telegram.poll(), name="owner-polling")
        self.scheduler.start()
        self.health.state = "ready"
        await durable_call(self.health.write)

    async def monitor(self):
        last_renewal = time.monotonic()
        while not self._closed:
            try:
                if stop_requested(self.settings, self.health.identity) or operator_stop_requested(
                    self.settings
                ):
                    self.stop_event.set()
                if any(task is not None and task.done() for task in (self.api_task, self.poll_task)):
                    if not self.stop_event.is_set():
                        LOG.error("Owner transport exited; runtime will stop paused")
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
                    "Runtime health/storage unavailable; graceful stop requested (%s: %s)",
                    type(error).__name__,
                    str(error)[:200],
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
        if r is not None:
            r.owner.closing = True  # Fence preparation/resume/close; downward controls remain available.
        # Fence in memory BEFORE attempting SQL: transient pause persistence
        # failure must not leave the entry scheduler accepting work until retry.
        if self.scheduler is not None:
            self.scheduler.stop_accepting()
        if r is not None and r.engine.control.session_id is not None:
            await durable_call(r.engine.control.pause_for_shutdown)
        if self.api is not None:
            self.api.should_exit = True
        if r is not None and r.telegram is not None and r.telegram._polling:
            await r.telegram.dispatcher.stop_polling()
        if self.scheduler is not None:
            while not await self.scheduler.drain(self.settings.runtime_shutdown_seconds):
                LOG.error("Active job remains; no force cancellation/replacement is permitted")
        # Servers finish accepted requests before resources disappear. No wait_for cancellation.
        for task in (self.api_task, self.poll_task):
            if task is not None:
                try:
                    await task
                except Exception:
                    LOG.error("Owner transport exited with a suppressed error")
        if r is not None and r.telegram is not None:
            pending = set(r.telegram.dispatcher._handle_update_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if r is not None:
            while r.owner.active_actions:
                await asyncio.wait(
                    set(r.owner.active_actions), timeout=self.settings.runtime_shutdown_seconds
                )
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
                    await asyncio.wait_for(r.alerts.flush(getattr(r.telegram, "bot", None)), timeout=10)
                except Exception:
                    LOG.warning("Final alert flush incomplete")
            if r.telegram is not None:
                await r.telegram.close()
            await durable_call(r.notices.enqueue, "stopped", dedup=self.health.identity)
        self.health.state = "stopped"
        await durable_call(self.health.write)
        self._closed = True
        if self.monitor_task is not None:
            await self.monitor_task
        if self.api_socket is not None:
            self.api_socket.close()
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
