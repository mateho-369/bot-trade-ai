"""Owner-only news outbox delivery, staged BEFORE Telegram send (at-most-once).

A lost/timeout response may mean delivery happened. Such attempts are recorded as
uncertain and NEVER automatically resent. Success is acknowledged only after the
Telegram API succeeds. This is not an exactly-once network delivery guarantee.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.async_tools import durable_call
from core.models import AuditLog
from core.security import sanitize_text
from news.types import parse_time


class OwnerNewsNotifier:
    def __init__(self, transport, alerts):
        services = transport.services
        if (
            services.preview_only
            or services.settings.telegram_owner_id is None
            or alerts.database is not services.database
            or alerts.settings.safety_fingerprint() != services.settings.safety_fingerprint()
            or alerts.clock is not services.clock
        ):
            raise ValueError("explicit matching owner news outbox/transport required")
        self.transport, self.alerts, self.services = transport, alerts, services

    def _claim(self, candidate):
        with self.services.database.locked_session() as session:
            pending = session.get(AuditLog, candidate["queue_id"])
            if (
                pending is None
                or pending.action != "news.alert_pending"
                or pending.details.get("scope_hash") != self.alerts.scope
                or pending.details.get("alert_id") != candidate["alert_id"]
                or parse_time(pending.details["expires_at"]) <= self.services.clock.now()
            ):
                return False
            previous = session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action.in_(("news.alert_delivery_attempted", "news.alert_delivered")),
                    AuditLog.details["alert_id"].as_string() == candidate["alert_id"],
                )
                .limit(1)
            )
            if previous:
                return False
            record = self.services.database.add_audit(
                session,
                "news.alert_delivery_attempted",
                "telegram",
                {
                    "alert_id": candidate["alert_id"],
                    "scope_hash": self.alerts.scope,
                    "owner_id": self.services.settings.telegram_owner_id,
                    "fixture_only": candidate.get("fixture_only", True),
                    "remote_delivery_may_follow": True,
                },
            )
            record.time = self.services.clock.now()
            return True

    def _uncertain(self, candidate):
        with self.services.database.locked_session() as session:
            record = self.services.database.add_audit(
                session,
                "news.alert_delivery_uncertain",
                "telegram",
                {
                    "alert_id": candidate["alert_id"],
                    "scope_hash": self.alerts.scope,
                    "do_not_automatically_retry": True,
                },
            )
            record.time = self.services.clock.now()

    async def drain(self, *, limit=10):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("bounded notification batch required")
        pending = await durable_call(self.alerts.pending, limit=limit)
        result = {"delivered": 0, "uncertain": 0, "skipped": 0}
        for candidate in pending:
            if not await durable_call(self._claim, candidate):
                result["skipped"] += 1
                continue
            title = sanitize_text(candidate["title"], self.services.database.secrets)[:300]
            symbols = ", ".join(candidate.get("symbols", [])[:30])
            text = (
                "MT5 AI ReflexBot · news risk\n"
                + ("SYNTHETIC FIXTURE · " if candidate.get("fixture_only") else "")
                + candidate["kind"]
                + " · "
                + symbols
                + "\n"
                + title
                + "\nNew-entry news gates remain authoritative. No automatic resume or trade."
            )
            try:
                await self.transport.bot.send_message(
                    chat_id=self.services.settings.telegram_owner_id, text=text[:1000], parse_mode=None
                )
                await durable_call(
                    self.alerts.acknowledge,
                    candidate["alert_id"],
                    owner_id=self.services.settings.telegram_owner_id,
                )
                result["delivered"] += 1
            except BaseException as error:
                await durable_call(self._uncertain, candidate)
                result["uncertain"] += 1
                # Preserve cancellation/system-exit after recording possible delivery.
                if not isinstance(error, Exception) or isinstance(error, asyncio.CancelledError):
                    raise
        return result
