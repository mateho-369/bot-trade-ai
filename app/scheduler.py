"""One-worker bounded scheduling of real services, never automatic owner resume.

Jobs coalesce missed runs; they do not catch up orders. Overdue tasks are NOT
cancelled/replaced: native writes may still be running. Shutdown drains them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.async_tools import durable_call
from app.backups import create_backup, prune_db_snapshots
from app.process_guard import operator_stop_requested
from core.models import BotState
from telegram_bot.notifications import OwnerNewsNotifier
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, TradingDisabled

LOG = logging.getLogger("reflexbot.scheduler")


class RuntimeScheduler:
    def __init__(self, resources, health, *, scheduler=None):
        self.resources, self.health = resources, health
        self.settings = resources.settings
        self.scheduler = scheduler or AsyncIOScheduler(
            timezone="UTC", job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5}
        )
        self.active = set()
        self.accepting = True
        self.reviews = {}
        self.locks = {}
        self.jobs = {
            "heartbeat": self.heartbeat,
            "positions": self.positions,
            "signals": self.signals,
            "news": self.news,
            "news_advisory": self.news_advisory,
            "position_reviews": self.position_reviews,
            "notifications": self.notifications,
            "daily_report": self.daily_report,
            "learning": self.learning,
            "backup": self.backup,
            "ai_learning": self.ai_learning,
            "ai_config_review": self.ai_config_review,
            "ai_nightly_review": self.ai_nightly_review,
        }

    def control_state(self):
        r = self.resources
        actual = RuntimeProfile.current(
            self.settings, r.engine.profile.data_source, model_sha256=r.engine.profile.model_sha256
        )
        if actual.code_hash != r.engine.profile.code_hash:
            raise TradingDisabled("runtime code changed; stop paused and review before composing again")
        with r.database.session() as session:
            row = session.get(BotState, 1)
            r.engine.control.check(row)
            return row.desired_state, row.kill_switch_active, row.last_error

    async def entries_permitted(self):
        state, killed, error = await durable_call(self.control_state)
        return (
            self.accepting
            and state == "running"
            and not killed
            and not error
            and not self.health.overdue()
            and not operator_stop_requested(self.settings)
        )

    async def run_job(self, name):
        if name not in self.jobs:
            raise ValueError("unknown runtime job")
        lock = self.locks.setdefault(name, asyncio.Lock())
        if not self.accepting or lock.locked():
            return {"state": "skipped"}
        async with lock:
            task = asyncio.current_task()
            self.active.add(task)
            self.health.job_started(name)
            success = False
            try:
                result = await self.jobs[name]()
                success = True
                return result
            except asyncio.CancelledError:
                # Unexpected external cancellation is uncertainty, not a retry instruction.
                await self.failed(name, cancelled=True)
                raise
            except Exception:
                await self.failed(name)
                return {"state": "failed", "job": name}
            finally:
                self.health.job_finished(name, success=success)
                self.active.discard(task)

    async def failed(self, name, *, cancelled=False):
        r = self.resources
        LOG.error("Runtime job unavailable: %s; raw exception suppressed", name)
        try:
            if name in {"heartbeat", "positions", "signals", "position_reviews"}:
                await durable_call(
                    r.engine.control.halt,
                    "unknown_execution" if cancelled else "broker_unstable",
                    account_key=r.engine.account_key or "unbound",
                )
            await durable_call(
                r.database.audit,
                "scheduler.job_failed",
                "scheduler",
                {
                    "job": name,
                    "cancelled": cancelled,
                    "no_automatic_retry_of_effect": True,
                },
            )
            await durable_call(r.notices.enqueue, "job_failed", dedup=self.health.identity + ":" + name)
        except Exception:
            LOG.error("Cannot persist runtime failure; no trade retry is permitted")

    async def heartbeat(self):
        r = self.resources
        await durable_call(r.engine.control.heartbeat)
        if self.health.overdue():
            await durable_call(
                r.engine.control.halt, "risk_observation_gap", account_key=r.engine.account_key
            )
        return await durable_call(self.health.write)

    async def positions(self):
        # No running/paused/kill gate here. Durable protective-only authority stays in force.
        return await self.resources.positions.cycle(dict(self.reviews))

    async def signals(self):
        r, outcomes = self.resources, []
        if not await self.entries_permitted():
            return {"state": "paused", "executed": 0}
        for symbol in self.settings.symbols:
            if not await self.entries_permitted():
                break
            window = await r.news.window(symbol)  # Local decision only, NEVER fetch implicitly.
            reviewer = r.supervisor
            if getattr(r, "ai_first", None) is not None and self.settings.ai_first_enabled:
                reviewer = r.ai_first.reviewer  # AI consulted on EVERY entry; supervisor stays behind it.
            signal = await r.signals.evaluate(symbol, reviewer=reviewer, news=window)
            if signal.approved and await self.entries_permitted():
                try:
                    outcome = await r.engine.execute_signal(signal.signal_id)
                    if getattr(r, "ai_first", None) is not None:
                        await durable_call(r.ai_first.link_execution, signal.signal_id, outcome)
                    outcomes.append(
                        {
                            "signal_id": signal.signal_id,
                            "status": outcome.status.value if outcome else "no_effect",
                        }
                    )
                except BrokerError:
                    # Bridge audited a gate/uncertainty veto. Do not stage another key or resend.
                    outcomes.append({"signal_id": signal.signal_id, "status": "vetoed"})
            else:
                outcomes.append({"signal_id": signal.signal_id, "status": signal.state})
        return {"state": "evaluated", "outcomes": outcomes}

    async def news(self):
        return await self.resources.news.refresh()

    async def news_advisory(self):
        return await self.resources.news.advisory_sentiment(self.resources.supervisor)

    async def feature_bundle(self, logical):
        r, market = self.resources, self.resources.signals.market
        symbol = r.signals.symbols.resolve(logical).native
        info, cutoff = await market.get_symbol_info(symbol), market.clock.now()
        frames = {}
        for timeframe in dict.fromkeys(
            (self.settings.primary_timeframe, self.settings.higher_timeframe, self.settings.trend_timeframe)
        ):
            frames[timeframe] = await market.get_candles(
                symbol, timeframe, self.settings.candle_lookback, as_of=cutoff
            )
        tick = await market.get_tick(symbol)
        return await durable_call(
            r.signals.features.build,
            frames,
            logical_symbol=logical,
            info=info,
            tick=tick,
            source=market.source_kind,
            observed_at=market.clock.now(),
        )

    async def position_reviews(self):
        r = self.resources
        if not self.settings.allow_tp_extension:
            self.reviews = {}
            return {"state": "disabled"}
        reviews = {}
        for position in await r.engine.capture_owned_positions():
            logical = next(
                (
                    s
                    for s in self.settings.symbols
                    if self.settings.symbol_aliases.get(s, s) == position.symbol
                ),
                None,
            )
            if logical is None:
                continue
            try:
                features = await self.feature_bundle(logical)
                news = await r.news.window(logical)
                async with asyncio.timeout(self.settings.ai_timeout_seconds):
                    review = await r.supervisor.review_position(
                        position, features, news, account_key=r.engine.account_key
                    )
                if review is not None:
                    reviews[position.identifier] = review
            except (BrokerError, TimeoutError):
                continue  # Missing review cannot loosen protection or approve an extension.
        self.reviews = reviews
        return {"state": "reviewed", "count": len(reviews)}

    async def notifications(self):
        r = self.resources
        if r.telegram is None:
            return {"state": "no_transport"}
        result = {
            "runtime": await r.notices.drain(r.telegram),
            "news": await OwnerNewsNotifier(r.telegram, r.news.alerts).drain(),
        }
        if getattr(r, "ai_first", None) is not None:
            result["ai"] = await r.ai_first.notifier.drain(getattr(r.telegram, "bot", None))
        return result

    async def ai_learning(self):
        layer = getattr(self.resources, "ai_first", None)
        return {"state": "disabled"} if layer is None else await layer.learn_closed_trades()

    async def ai_config_review(self):
        layer = getattr(self.resources, "ai_first", None)
        return {"state": "disabled"} if layer is None else await layer.config_review()

    async def ai_nightly_review(self):
        layer = getattr(self.resources, "ai_first", None)
        return {"state": "disabled"} if layer is None else await layer.nightly_review()

    async def daily_report(self):
        r = self.resources
        report = await r.supervisor.daily_report(
            r.engine.account_key, start=r.broker.clock.now() - timedelta(days=1)
        )
        await durable_call(r.notices.enqueue, "daily_report", dedup=r.broker.clock.now().date().isoformat())
        return report

    async def learning(self):
        r = self.resources
        if not self.settings.runtime_learning_enabled:
            return {"state": "disabled"}
        state, _, _ = await durable_call(self.control_state)
        owned = await durable_call(r.engine.logger.owned, r.engine.account_key)
        if state != "paused" or owned:
            return {"state": "skipped", "reason": "requires_paused_owned_flat_start"}
        result = await r.supervisor.learning_cycle(account_key=r.engine.account_key)
        if result["state"] == "candidate":
            await durable_call(r.notices.enqueue, "learning_candidate", dedup=str(result["model_id"]))
        return result  # NEVER activate/apply/resume here.

    async def backup(self):
        if not self.settings.runtime_backup_enabled:
            return {"state": "disabled"}
        path = await durable_call(create_backup, self.settings, self.resources.database)
        await durable_call(prune_db_snapshots, self.settings)
        return {"state": "database_only", "name": path.name, "complete_recovery_bundle": False}

    def start(self):
        self.resources.engine._ready()
        cfg = self.settings
        for name, seconds in {
            "heartbeat": cfg.heartbeat_interval_seconds,
            "positions": cfg.position_interval_seconds,
            "signals": cfg.signal_interval_seconds,
            "news": cfg.news_poll_seconds,
            "notifications": cfg.runtime_notifications_seconds,
            "news_advisory": max(300, cfg.news_poll_seconds),
            "position_reviews": cfg.ai_position_review_seconds,
            **(
                {"ai_learning": 300, "ai_config_review": 1800}
                if getattr(self.resources, "ai_first", None) is not None
                else {}
            ),
        }.items():
            self.scheduler.add_job(
                self.run_job, "interval", seconds=seconds, args=(name,), id=name, replace_existing=False
            )
        for name, hour, enabled in (
            ("daily_report", cfg.runtime_report_hour_utc, True),
            ("learning", cfg.runtime_learning_hour_utc, cfg.runtime_learning_enabled),
            ("backup", cfg.runtime_backup_hour_utc, cfg.runtime_backup_enabled),
            (
                "ai_nightly_review",
                (cfg.runtime_report_hour_utc + 1) % 24,
                getattr(self.resources, "ai_first", None) is not None,
            ),
        ):
            if enabled:
                self.scheduler.add_job(
                    self.run_job,
                    "cron",
                    hour=hour,
                    minute=0,
                    args=(name,),
                    id=name,
                    misfire_grace_time=60,
                    replace_existing=False,
                )
        self.scheduler.start()

    def stop_accepting(self):
        self.accepting = False
        if self.scheduler.running:
            self.scheduler.pause()

    async def drain(self, timeout):
        active = {task for task in self.active if not task.done() and task is not asyncio.current_task()}
        if active:
            _, pending = await asyncio.wait(active, timeout=timeout)
            return not pending
        return True

    def finish(self):
        if self.active:
            raise RuntimeError("active jobs must drain before shutting down their executor")
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
