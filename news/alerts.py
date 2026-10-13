"""Deduplicated sanitized news report claims in append-only audit rows.

Claims are persisted before a local/outbound report attempt. This module cannot
pause/resume, close a trade, or call a broker.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from core.models import AuditLog
from core.security import sanitize_text, sha256_json
from news.exposure import ExposureMapper
from news.types import parse_time


class NewsAlerts:
    def __init__(self, database, settings, clock, scope):
        self.database, self.settings, self.clock, self.scope = database, settings, clock, scope

    def queue(self, state, decisions):
        now = self.clock.now()
        candidates = []
        exposure = ExposureMapper(self.settings)
        for story in state["stories"]:
            if (
                story["impact"] in {"high", "unknown"}
                and parse_time(story["risk_observed_at"])
                + timedelta(minutes=self.settings.news_headline_block_minutes)
                >= now
            ):
                candidates.append(
                    (
                        "headline_risk",
                        story["content_hash"] + "@" + story["risk_observed_at"],
                        tuple(story["symbols"]),
                        story["title"],
                    )
                )
        cal = state["calendar"]
        if cal:
            for event in cal["events"]:
                left = parse_time(event["starts_at"]) - timedelta(
                    minutes=self.settings.news_pre_event_minutes
                )
                right = parse_time(event["ends_at"]) + timedelta(
                    minutes=self.settings.news_post_event_minutes
                )
                if left <= now <= right and (
                    event["impact"] in {"high", "unknown"}
                    or event["tentative"]
                    or self.settings.news_impact_threshold == "medium"
                    and event["impact"] == "medium"
                ):
                    candidates.append(
                        (
                            "economic_event",
                            event["event_id"],
                            exposure.calendar_symbols(event["currency"]),
                            event["title"],
                        )
                    )
        for symbol, decision in decisions.items():
            if not decision.window.known:
                candidates.append(
                    (
                        "coverage_unknown",
                        "|".join(decision.reasons),
                        (symbol,),
                        "Required news/calendar coverage unknown; new entries blocked.",
                    )
                )
        count = 0
        with self.database.locked_session() as session:
            for kind, identifier, symbols, title in candidates:
                if count >= self.settings.news_alerts_per_refresh:
                    break
                if not symbols:
                    continue
                # Coverage-loss reminders are once per UTC day; story/event alerts once per identity.
                key = sha256_json(
                    {
                        "scope_hash": self.scope,
                        "kind": kind,
                        "identity": identifier,
                        "symbols": symbols,
                        "day": now.date().isoformat() if kind == "coverage_unknown" else None,
                    }
                )
                prior = session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action == "news.alert_pending",
                        AuditLog.details["alert_id"].as_string() == key,
                    )
                    .limit(1)
                )
                if prior:
                    continue
                text = sanitize_text(title, self.database.secrets)[:300]
                record = self.database.add_audit(
                    session,
                    "news.alert_pending",
                    "news",
                    {
                        "alert_id": key,
                        "scope_hash": self.scope,
                        "kind": kind,
                        "symbols": list(symbols),
                        "title": text,
                        "expires_at": (now + timedelta(minutes=15)).isoformat(),
                        "fixture_only": state["fixture_only"],
                        "delivered": False,
                    },
                )
                record.time = now
                count += 1
        return count

    def pending(self, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("bounded pending alerts required")
        now = self.clock.now()
        with self.database.session() as session:
            rows = session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "news.alert_pending",
                    AuditLog.details["scope_hash"].as_string() == self.scope,
                    AuditLog.time >= now - timedelta(minutes=15),
                )
                .order_by(AuditLog.id)
                .limit(1001)
            ).all()
            if len(rows) > 1000:
                raise ValueError("alert backlog exceeds bound")
            result = []
            for row in rows:
                if parse_time(row.details["expires_at"]) <= now:
                    continue
                attempted = session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action.in_(("news.alert_delivered", "news.alert_report_attempted")),
                        AuditLog.details["alert_id"].as_string() == row.details["alert_id"],
                    )
                    .limit(1)
                )
                if not attempted:
                    result.append({"queue_id": row.id, **row.details})
                if len(result) >= limit:
                    break
            return result

    def claim_batch(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded news report batch required")
        now = self.clock.now()
        claimed = []
        with self.database.locked_session() as session:
            rows = session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "news.alert_pending",
                    AuditLog.details["scope_hash"].as_string() == self.scope,
                    AuditLog.time >= now - timedelta(minutes=15),
                )
                .order_by(AuditLog.id)
                .limit(100)
            ).all()
            for row in rows:
                details = dict(row.details)
                if parse_time(details["expires_at"]) <= now:
                    continue
                existing = session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action.in_(("news.alert_delivered", "news.alert_report_attempted")),
                        AuditLog.details["alert_id"].as_string() == details["alert_id"],
                    )
                    .limit(1)
                )
                if existing:
                    continue
                claim = self.database.add_audit(
                    session,
                    "news.alert_report_attempted",
                    "reporter",
                    {
                        "alert_id": details["alert_id"],
                        "scope_hash": self.scope,
                        "remote_delivery_may_follow": True,
                    },
                )
                claim.time = now
                claimed.append(details)
                if len(claimed) >= limit:
                    break
        return claimed

    async def drain(self, reporter, *, limit=10):
        import asyncio

        result = {"reported": 0, "uncertain": 0, "disabled": 0}
        for details in await asyncio.to_thread(self.claim_batch, limit):
            symbols = ", ".join(details.get("symbols", [])[:10])
            title = sanitize_text(details.get("title", ""), self.database.secrets)[:300]
            message = (
                f"{reporter.text('report_header')} · {reporter.text('news_alert')}\n"
                f"{reporter.text('news_symbols')}: {symbols}\n{title}\n"
                f"{reporter.text('news_coverage_blocked')}"
            )[:1500]
            status = await reporter.publish(
                message,
                kind="news.alert",
                details={"kind": details.get("kind"), "symbols": details.get("symbols", [])},
            )
            if status == "sent":
                result["reported"] += 1
            elif status == "disabled":
                result["disabled"] += 1
            else:
                result["uncertain"] += 1
        return result
