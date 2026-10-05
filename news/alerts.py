"""Deduplicated sanitized notification outbox in append-only audit rows.

Part 9 sends to the authenticated owner. Pending is not delivered; none of this
pauses/resumes, closes a trade or calls Telegram/broker/network services.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ai.owner_guard import require_owner
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
                delivered = session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action == "news.alert_delivered",
                        AuditLog.details["alert_id"].as_string() == row.details["alert_id"],
                    )
                    .limit(1)
                )
                if not delivered:
                    result.append({"queue_id": row.id, **row.details})
                if len(result) >= limit:
                    break
            return result

    def acknowledge(self, alert_id: str, *, owner_id: int):
        """Trusted sender calls only AFTER successful owner delivery; Part 9 authenticates it."""
        require_owner(self.settings, owner_id)
        from trading.types import valid_key

        valid_key(alert_id)
        with self.database.locked_session() as session:
            pending = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "news.alert_pending",
                    AuditLog.details["alert_id"].as_string() == alert_id,
                    AuditLog.details["scope_hash"].as_string() == self.scope,
                )
            )
            if pending is None:
                raise ValueError("pending owner notification missing")
            existing = session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == "news.alert_delivered",
                    AuditLog.details["alert_id"].as_string() == alert_id,
                )
                .limit(1)
            )
            if existing:
                return
            row = self.database.add_audit(
                session,
                "news.alert_delivered",
                "news",
                {"alert_id": alert_id, "scope_hash": self.scope, "owner_id": owner_id},
            )
            row.time = self.clock.now()
