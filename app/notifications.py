"""Durable bounded owner runtime notices; claim before send, never blind retry."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.async_tools import durable_call
from core.models import AuditLog
from core.security import sha256_json

TEXT = {
    "started": (
        "Runtime started PAUSED. Existing protection is monitored. Owner review is required to "
        "resume entries."
    ),
    "restart": (
        "Watchdog launched a replacement for an exited child; it must start PAUSED. Readiness "
        "is not yet confirmed. No unknown order was resubmitted or safety latch reset."
    ),
    "stalled": (
        "Runtime health is stale/overdue. Graceful stop requested; replacement is withheld "
        "while the child is alive. Check MT5 and durable intents manually."
    ),
    "budget": (
        "Watchdog restart budget exhausted. Automatic starts are stopped; inspect logs and "
        "broker outcomes manually."
    ),
    "job_failed": (
        "A runtime job failed. New-entry safety gates remain authoritative. Inspect sanitized audit logs."
    ),
    "stopped": (
        "Runtime stopped PAUSED. Broker-side SL/TP remain; local trailing is unavailable while stopped."
    ),
    "daily_report": (
        "Daily closed-trade review was stored. It does not establish stage eligibility or change settings."
    ),
    "learning_candidate": (
        "Learning produced an INACTIVE model candidate. Owner selection and deployment "
        "evidence remain required."
    ),
}


class RuntimeNotices:
    def __init__(self, database, settings, clock):
        self.database, self.settings, self.clock = database, settings, clock
        self.scope = sha256_json(
            {"config": settings.safety_fingerprint(), "owner": settings.telegram_owner_id}
        )

    def enqueue(self, kind, *, dedup):
        if kind not in TEXT or not isinstance(dedup, str) or not 1 <= len(dedup) <= 128:
            raise ValueError("fixed runtime notice and bounded deduplication key required")
        identity = sha256_json({"scope": self.scope, "kind": kind, "dedup": dedup})
        with self.database.locked_session() as session:
            if session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == "runtime.notice_pending",
                    AuditLog.details["notice_id"].as_string() == identity,
                )
                .limit(1)
            ):
                return identity
            row = self.database.add_audit(
                session,
                "runtime.notice_pending",
                "runtime",
                {
                    "notice_id": identity,
                    "scope": self.scope,
                    "kind": kind,
                    "expires_at": (self.clock.now() + timedelta(hours=24)).isoformat(),
                    "not_live_authorization": True,
                },
            )
            row.time = self.clock.now()
        return identity

    def claim_batch(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("bounded batch required")
        from datetime import datetime

        claimed = []
        with self.database.locked_session() as session:
            # Scope/time bounded BEFORE LIMIT; old attempted rows do not starve new notices.
            attempted = select(AuditLog.details["notice_id"].as_string()).where(
                AuditLog.action == "runtime.notice_attempted",
                AuditLog.details["scope"].as_string() == self.scope,
            )
            rows = session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "runtime.notice_pending",
                    AuditLog.details["scope"].as_string() == self.scope,
                    AuditLog.time >= self.clock.now() - timedelta(hours=24),
                    AuditLog.details["notice_id"].as_string().not_in(attempted),
                )
                .order_by(AuditLog.id)
                .limit(limit)
            ).all()
            for row in rows:
                data = row.details
                if (
                    data.get("kind") not in TEXT
                    or datetime.fromisoformat(data["expires_at"]) <= self.clock.now()
                ):
                    continue
                self.database.add_audit(
                    session,
                    "runtime.notice_attempted",
                    "telegram",
                    {
                        "scope": self.scope,
                        "notice_id": data["notice_id"],
                        "owner_id": self.settings.telegram_owner_id,
                        "remote_delivery_may_follow": True,
                    },
                )
                claimed.append(dict(data, queued_at=row.time.isoformat()))
        return claimed

    async def drain(self, transport, *, limit=10):
        if (
            transport.services.preview_only
            or transport.services.database is not self.database
            or transport.services.clock is not self.clock
            or transport.services.settings.safety_fingerprint() != self.settings.safety_fingerprint()
            or self.settings.telegram_owner_id is None
        ):
            raise ValueError("matching authenticated-owner transport required")
        result = {"delivered": 0, "uncertain": 0}
        for data in await durable_call(self.claim_batch, limit):
            try:
                await transport.bot.send_message(
                    chat_id=self.settings.telegram_owner_id,
                    text="MT5 AI ReflexBot · recorded " + data["queued_at"] + "\n" + TEXT[data["kind"]],
                    parse_mode=None,
                )
                action = "runtime.notice_delivered"
                result["delivered"] += 1
            except BaseException as error:
                await durable_call(
                    self.database.audit,
                    "runtime.notice_uncertain",
                    "telegram",
                    {
                        "notice_id": data["notice_id"],
                        "scope": self.scope,
                        "do_not_retry": True,
                    },
                )
                result["uncertain"] += 1
                if not isinstance(error, Exception) or isinstance(error, asyncio.CancelledError):
                    raise
                continue
            await durable_call(
                self.database.audit,
                action,
                "telegram",
                {
                    "notice_id": data["notice_id"],
                    "scope": self.scope,
                },
            )
        return result
