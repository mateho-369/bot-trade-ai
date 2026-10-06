"""Small facade for queued local reports and optional outbound-only Telegram delivery."""

from __future__ import annotations

from app.reporter import Reporter


class RuntimeNotices:
    def __init__(self, database, settings, clock, reporter: Reporter | None = None):
        self.database, self.settings, self.clock = database, settings, clock
        self.reporter = reporter or Reporter(settings, clock=clock)

    def enqueue(self, kind: str, *, dedup: str, details: dict | None = None):
        return self.reporter.queue_event(kind, dedup=dedup, details=details)

    async def drain(self, *, limit: int = 10):
        return await self.reporter.drain(limit=limit)
