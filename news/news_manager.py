"""Explicit async collector: unknown startup → complete poll → bound per-symbol windows.

No scheduler/HTTP endpoint/broker write, no headline-only calendar, no automatic
resume. Refresh begins by durably invalidating green evidence; cancelled/late
results cannot restore the old epoch. Read methods never silently fetch HTTP.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.database import Database
from core.settings import Settings
from news.alerts import NewsAlerts
from news.economic_calendar import calendar_adapter
from news.evidence import NewsDecision, configured_source_ids, project_window
from news.http_client import NewsHTTP
from news.news_cache import NewsCache
from news.providers import adapters
from news.types import CalendarSnapshot, HeadlineBatch, NewsInvalid, NewsUnavailable
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, Clock, TradingDisabled


@dataclass(frozen=True, slots=True)
class RefreshResult:
    snapshot_epoch: int
    snapshot_hash: str
    symbols_known: int
    symbols_safe: int
    pending_alerts_created: int


class NewsManager:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        profile: RuntimeProfile,
        *,
        http: NewsHTTP | None = None,
        sources: tuple | None = None,
        calendar=None,
    ):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        if database.settings.safety_fingerprint() != settings.safety_fingerprint():
            raise TradingDisabled("news/database policy differs")
        self.http = http or NewsHTTP(settings)
        self.sources = sources if sources is not None else adapters(settings, clock, self.http)
        self.calendar = calendar or calendar_adapter(settings, clock, self.http)
        if tuple(s.source_id for s in self.sources) != configured_source_ids(settings):
            raise TradingDisabled("collector sources differ from declared configuration")
        self.cache = NewsCache(database, settings, clock, profile)
        self.alerts = NewsAlerts(database, settings, clock, self.cache.scope)
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.news_max_concurrent)
        self._attempts = {}
        self._last_results = {}
        self._initialized = False

    async def initialize(self):
        await asyncio.to_thread(self.database.verify_schema)
        actual = await asyncio.to_thread(
            RuntimeProfile.current,
            self.settings,
            self.profile.data_source,
            model_sha256=self.profile.model_sha256,
        )
        if actual.code_hash != self.profile.code_hash:
            raise TradingDisabled("news runtime code fingerprint differs")
        await asyncio.to_thread(self.cache.invalidate, "startup_requires_new_poll")
        self._attempts.clear()
        self._last_results.clear()
        self._initialized = True

    def _ready(self):
        if not self._initialized:
            raise TradingDisabled("initialize read-only news services explicitly")

    async def _fetch(self, adapter):
        identifier = adapter.source_id
        policy = self.settings.news_source_coverage.get(identifier)
        interval = policy.poll_seconds if policy else self.settings.news_poll_seconds
        prior = self._attempts.get(identifier)
        if (
            prior is not None
            and identifier in self._last_results
            and 0 <= (self.clock.now() - prior).total_seconds() < interval
        ):
            return self._last_results[
                identifier
            ]  # Original freshness/error retained, never timestamp-refreshed.
        self._attempts[identifier] = self.clock.now()
        try:
            async with self._semaphore:
                result = await adapter.fetch()
            if not isinstance(result, HeadlineBatch) or result.source_id != identifier:
                raise NewsInvalid("wrong_provider_batch")
        except asyncio.CancelledError:
            self._last_results[identifier] = (identifier, "source_refresh_cancelled")
            raise
        except Exception:
            result = (identifier, "source_unavailable_or_invalid")
        self._last_results[identifier] = result
        return result

    async def _fetch_calendar(self):
        try:
            async with self._semaphore:
                result = await self.calendar.fetch()
            if not isinstance(result, CalendarSnapshot) or result.source_id != self.calendar.source_id:
                raise NewsInvalid("wrong_calendar_source")
            return result, None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None, "calendar_unavailable_or_invalid"

    async def refresh(self):
        self._ready()
        async with self._lock:
            claim, previous = await asyncio.to_thread(self.cache.begin)
            commit = None
            try:
                async with asyncio.timeout(self.settings.news_refresh_timeout_seconds):
                    work = [self._fetch(s) for s in self.sources] + [self._fetch_calendar()]
                    values = await asyncio.gather(*work)
                    calendar, error = values[-1]
                    results = tuple(values[:-1])
                    commit = asyncio.create_task(
                        asyncio.to_thread(self.cache.finish, claim, results, calendar, error, previous)
                    )
                    epoch, digest = await asyncio.shield(commit)
                decisions = await self.decisions()
                _, state = await asyncio.to_thread(self.cache.latest)
                alerts = await asyncio.to_thread(self.alerts.queue, state, decisions)
                return RefreshResult(
                    epoch,
                    digest,
                    sum(d.window.known for d in decisions.values()),
                    sum(d.allowed for d in decisions.values()),
                    alerts,
                )
            except asyncio.CancelledError:
                if commit is not None:
                    # SQL/file work cannot be killed. Wait then revoke only our resulting epoch.
                    try:
                        completed = await asyncio.shield(commit)
                        await asyncio.to_thread(
                            self.cache.invalidate_epoch, completed[0], "cancelled_news_commit"
                        )
                    except Exception:
                        pass  # No raw exception, do not manufacture old cached green.
                await asyncio.to_thread(self.cache.fail, claim, "cancelled_news_refresh")
                raise
            except (
                TimeoutError,
                BrokerError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                ArithmeticError,
            ):
                if commit is not None:
                    try:
                        completed = await asyncio.shield(commit)
                        await asyncio.to_thread(
                            self.cache.invalidate_epoch, completed[0], "failed_news_commit"
                        )
                    except Exception:
                        pass
                await asyncio.to_thread(self.cache.fail, claim, "news_refresh_failed")
                await asyncio.to_thread(
                    self.database.audit,
                    "news.refresh_failed",
                    "news",
                    {"scope_hash": self.cache.scope, "safe_entry_withheld": True},
                )
                raise NewsUnavailable("refresh_failed") from None

    def _decision(self, logical):
        epoch, state = self.cache.latest()
        if state is None:
            raise NewsUnavailable("published_news_snapshot_missing")
        from news.news_cache import latest_publication

        with self.database.session() as session:
            row = latest_publication(session, self.cache.scope)
            if row is None or row.id != epoch:
                raise NewsUnavailable("news_epoch_changed_during_read")
            digest = row.details["snapshot_hash"]
        return project_window(
            state,
            epoch=epoch,
            snapshot_hash=digest,
            logical_symbol=logical,
            settings=self.settings,
            profile=self.profile,
            now=self.clock.now(),
        )

    async def decision(self, logical_symbol: str) -> NewsDecision:
        self._ready()
        return await asyncio.to_thread(self._decision, logical_symbol)

    async def window(self, logical_symbol: str):
        return (await self.decision(logical_symbol)).window

    async def decisions(self):
        self._ready()
        return {symbol: await self.decision(symbol) for symbol in self.settings.symbols}

    async def windows(self):
        return {symbol: d.window for symbol, d in (await self.decisions()).items()}

    async def list_news(self, limit=50):
        self._ready()
        return await asyncio.to_thread(self.cache.list_news, limit=limit)

    async def advisory_sentiment(self, supervisor, *, limit=8):
        """Optional explicit AI call; result cannot change deterministic impact/coverage/windows."""
        self._ready()
        from ai.ai_supervisor import NewsSnippet
        from news.types import parse_time

        rows = await self.list_news(limit)
        snippets = tuple(
            NewsSnippet(
                r["title"], r["summary"], parse_time(r["published_at"]), r["source"], tuple(r["symbols"])
            )
            for r in rows
            if r["symbols"]
        )
        return await supervisor.analyze_news(snippets) if snippets else None

    async def close(self):
        try:
            if self._initialized:
                await asyncio.to_thread(self.cache.invalidate, "news_service_stopped")
        finally:
            self._initialized = False
            await self.http.close()
