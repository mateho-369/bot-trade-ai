"""SQL story dedup + immutable snapshot files + append-only SQL publication epochs.

No automatic restore of old green state. Risk readers validate the latest committed
journal and its confined file in the same durable entry transaction. Orphan files
from failed SQL publication are inert; never erase/rewrite financial state.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select

from core.database import Database
from core.models import AuditLog, News
from core.security import canonical_json, sha256_json
from core.settings import Settings
from news.exposure import ExposureMapper
from news.sentiment_analyzer import SentimentAnalyzer
from news.types import CalendarSnapshot, HeadlineBatch, NewsInvalid, NewsUnavailable, parse_time
from trading.risk_types import RuntimeProfile
from trading.types import Clock, valid_key

FORMAT = "reflex-news-snapshot-v1"
PUBLISHED = "news.snapshot_published"


def scope_hash(settings: Settings, profile: RuntimeProfile):
    return sha256_json(
        {
            "format": FORMAT,
            "config_hash": settings.safety_fingerprint(),
            "code_hash": profile.code_hash,
            "market_source": profile.data_source.value,
        }
    )


def latest_publication(session, scope):
    return session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == PUBLISHED,
            AuditLog.source == "news",
            AuditLog.details["scope_hash"].as_string() == scope,
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )


def _path(settings: Settings, digest: str):
    valid_key(digest)
    root = settings.project_root.resolve()
    folder = root / "data" / "news" / "snapshots"
    for p in (folder, *folder.parents):
        if p == root.parent:
            break
        if p.is_symlink():
            raise NewsInvalid("snapshot_symlink")
    resolved = folder.resolve()
    if not resolved.is_relative_to(root):
        raise NewsInvalid("snapshot_confinement")
    return resolved / (digest + ".json")


def load_snapshot(settings: Settings, digest: str):
    try:
        path = _path(settings, digest)
        if path.is_symlink():
            raise ValueError
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or not 1 <= meta.st_size <= settings.news_snapshot_max_bytes:
                raise ValueError
            raw = stream.read(settings.news_snapshot_max_bytes + 1)
        if len(raw) > settings.news_snapshot_max_bytes or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError
        from ai.json_validation import AIInvalidResponse, strict_json

        try:
            result = strict_json(
                raw,
                max_bytes=settings.news_snapshot_max_bytes,
                max_depth=12,
                max_nodes=50000,
                max_string=4000,
                max_array=2000,
            )
        except AIInvalidResponse:
            raise ValueError from None
        if (
            set(result)
            != {
                "format",
                "scope_hash",
                "config_hash",
                "code_hash",
                "market_source",
                "published_at",
                "refreshing",
                "sources",
                "calendar",
                "calendar_error",
                "stories",
                "fixture_only",
            }
            or result["format"] != FORMAT
            or type(result["refreshing"]) is not bool
            or type(result["fixture_only"]) is not bool
            or not isinstance(result["sources"], list)
            or len(result["sources"]) > 16
            or not isinstance(result["stories"], list)
            or len(result["stories"]) > settings.news_max_total_items
        ):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError):
        raise NewsInvalid("missing_corrupt_or_oversized_snapshot") from None


def _immutable_write(settings, data):
    raw = canonical_json(data).encode()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > settings.news_snapshot_max_bytes:
        raise NewsInvalid("snapshot_size")
    path = _path(settings, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise NewsInvalid("snapshot_symlink")
    if path.exists():
        if load_snapshot(settings, digest) != json.loads(raw):
            raise NewsInvalid("snapshot_collision")
        return digest
    if sum(1 for _ in path.parent.glob("*.json")) >= settings.news_snapshot_max_files:
        raise NewsUnavailable("snapshot_storage_bound_owner_review_required")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError:
        if load_snapshot(settings, digest) != json.loads(raw):
            raise NewsInvalid("snapshot_collision") from None
    except OSError:
        raise NewsUnavailable("snapshot_durability") from None
    return digest


class NewsCache:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        self.scope = scope_hash(settings, profile)
        self.exposure = ExposureMapper(settings)
        self.sentiment = SentimentAnalyzer()

    def _base(self, *, refreshing, sources, calendar, calendar_error, stories, fixture):
        return {
            "format": FORMAT,
            "scope_hash": self.scope,
            "config_hash": self.settings.safety_fingerprint(),
            "code_hash": self.profile.code_hash,
            "market_source": self.profile.data_source.value,
            "published_at": self.clock.now().isoformat(),
            "refreshing": refreshing,
            "sources": sources,
            "calendar": asdict(calendar) if calendar else None,
            "calendar_error": calendar_error,
            "stories": stories,
            "fixture_only": fixture,
        }

    def _publish(self, session, data, *, kind):
        digest = _immutable_write(self.settings, data)
        record = self.database.add_audit(
            session,
            PUBLISHED,
            "news",
            {
                "scope_hash": self.scope,
                "snapshot_hash": digest,
                "kind": kind,
                "config_hash": self.settings.safety_fingerprint(),
                "code_hash": self.profile.code_hash,
                "market_source": self.profile.data_source.value,
            },
        )
        record.time = parse_time(data["published_at"])
        session.flush()
        return record.id, digest

    def latest(self):
        with self.database.session() as session:
            row = latest_publication(session, self.scope)
            return (
                (row.id, load_snapshot(self.settings, row.details["snapshot_hash"])) if row else (None, None)
            )

    def invalidate(self, reason="startup_requires_poll"):
        with self.database.locked_session() as session:
            row = latest_publication(session, self.scope)
            previous = load_snapshot(self.settings, row.details["snapshot_hash"]) if row else None
            data = self._base(
                refreshing=False,
                sources=[],
                calendar=None,
                calendar_error=reason,
                stories=previous["stories"] if previous else [],
                fixture=previous["fixture_only"] if previous else False,
            )
            return self._publish(session, data, kind="unknown")

    def invalidate_epoch(self, epoch: int, reason: str):
        with self.database.locked_session() as session:
            row = latest_publication(session, self.scope)
            if row is None or row.id != epoch:
                return False
            previous = load_snapshot(self.settings, row.details["snapshot_hash"])
            data = self._base(
                refreshing=False,
                sources=[],
                calendar=None,
                calendar_error=reason,
                stories=previous["stories"],
                fixture=previous["fixture_only"],
            )
            self._publish(session, data, kind="unknown")
            return True

    def begin(self):
        with self.database.locked_session() as session:
            row = latest_publication(session, self.scope)
            previous = None
            if row:
                previous = load_snapshot(self.settings, row.details["snapshot_hash"])
                if (
                    previous["refreshing"]
                    and (self.clock.now() - parse_time(previous["published_at"])).total_seconds()
                    < self.settings.news_refresh_timeout_seconds + 5
                ):
                    raise NewsUnavailable("another_news_poll_owns_claim")
            data = self._base(
                refreshing=True,
                sources=[],
                calendar=None,
                calendar_error="refresh_in_progress",
                stories=previous["stories"] if previous else [],
                fixture=previous["fixture_only"] if previous else False,
            )
            epoch, _ = self._publish(session, data, kind="refreshing")
            return epoch, previous

    def fail(self, claim: int, reason: str):
        with self.database.locked_session() as session:
            row = latest_publication(session, self.scope)
            if row is None or row.id != claim:
                return False
            row = latest_publication(session, self.scope)
            previous = load_snapshot(self.settings, row.details["snapshot_hash"]) if row else None
            data = self._base(
                refreshing=False,
                sources=[],
                calendar=None,
                calendar_error=reason,
                stories=previous["stories"] if previous else [],
                fixture=previous["fixture_only"] if previous else False,
            )
            self._publish(session, data, kind="failed")
            return True

    def finish(
        self,
        claim: int,
        results: tuple[HeadlineBatch | tuple[str, str], ...],
        calendar: CalendarSnapshot | None,
        calendar_error: str | None,
        previous: dict | None,
    ):
        cfg = self.settings
        now = self.clock.now()
        sources = []
        stories = {}
        fixture = bool(calendar and calendar.fixture_only)
        with self.database.locked_session() as session:
            row = latest_publication(session, self.scope)
            if row is None or row.id != claim:
                raise NewsUnavailable("news_poll_claim_replaced")
            for result in results:
                if not isinstance(result, HeadlineBatch):
                    identifier, error = result
                    sources.append(
                        {
                            "source_id": identifier,
                            "fetched_at": None,
                            "newest_at": None,
                            "fixture_only": False,
                            "complete": False,
                            "entry_eligible": False,
                            "language_supported": False,
                            "error": error,
                        }
                    )
                    continue
                fixture = fixture or result.fixture_only
                policy = cfg.news_source_coverage.get(result.source_id)
                language = policy.language if policy else "en"
                language_ok = True
                newest = None
                for item in result.items:
                    mood = self.sentiment.analyze(item.title, item.summary, language=language)
                    language_ok = language_ok and mood.language_supported
                    impact = mood.impact if mood.language_supported else "high"
                    affected = self.exposure.affected(
                        item, source_symbols=policy.symbols if policy else (), unknown_high=impact == "high"
                    )
                    stored = session.scalar(select(News).where(News.content_hash == item.content_hash))
                    if stored is None:
                        stored = News(
                            time=item.published_at,
                            fetched_at=item.first_seen_at,
                            source=item.source_id,
                            title=item.title,
                            summary=item.summary,
                            impact=impact,
                            sentiment=mood.score,
                            symbols=list(affected),
                            url=item.url,
                            content_hash=item.content_hash,
                        )
                        session.add(stored)
                        session.flush()
                    else:
                        stored.time = min(stored.time, item.published_at)
                        stored.fetched_at = min(stored.fetched_at, item.first_seen_at)
                        priority = {"low": 0, "medium": 1, "unknown": 2, "high": 3}
                        if priority[impact] > priority[stored.impact]:
                            stored.impact = impact
                            stored.summary = item.summary
                            stored.sentiment = mood.score
                        stored.symbols = sorted(set(stored.symbols) | set(affected))
                    # Re-poll/syndication never updates first-seen/pub times to manufacture freshness.
                    newest = max(newest, stored.time) if newest else stored.time
                    risk_at = stored.fetched_at
                    if (
                        stored.impact in {"high", "unknown"}
                        or cfg.news_impact_threshold == "medium"
                        and stored.impact == "medium"
                    ):
                        context_hash = sha256_json(
                            {
                                "scope_hash": self.scope,
                                "fixture_only": result.fixture_only,
                                "title": item.title,
                                "summary": item.summary,
                                "impact": stored.impact,
                                "symbols": stored.symbols,
                            }
                        )
                        seen = session.scalar(
                            select(AuditLog.id)
                            .where(
                                AuditLog.action == "news.story_risk_observed",
                                AuditLog.details["scope_hash"].as_string() == self.scope,
                                AuditLog.details["content_hash"].as_string() == stored.content_hash,
                                AuditLog.details["context_hash"].as_string() == context_hash,
                            )
                            .limit(1)
                        )
                        if seen is None:
                            discovery = self.database.add_audit(
                                session,
                                "news.story_risk_observed",
                                "news",
                                {
                                    "scope_hash": self.scope,
                                    "content_hash": stored.content_hash,
                                    "context_hash": context_hash,
                                    "symbols": stored.symbols,
                                    "impact": stored.impact,
                                    "fixture_only": result.fixture_only,
                                },
                            )
                            discovery.time = item.first_seen_at
                            session.flush()
                        newest_risk = session.scalar(
                            select(AuditLog)
                            .where(
                                AuditLog.action == "news.story_risk_observed",
                                AuditLog.details["scope_hash"].as_string() == self.scope,
                                AuditLog.details["content_hash"].as_string() == stored.content_hash,
                            )
                            .order_by(AuditLog.time.desc(), AuditLog.id.desc())
                            .limit(1)
                        )
                        risk_at = max(risk_at, newest_risk.time)
                    descriptor = {
                        "content_hash": stored.content_hash,
                        "title": stored.title,
                        "published_at": stored.time.isoformat(),
                        "first_seen_at": stored.fetched_at.isoformat(),
                        "risk_observed_at": risk_at.isoformat(),
                        "symbols": stored.symbols,
                        "impact": stored.impact,
                        "sentiment": stored.sentiment,
                        "source_ids": [result.source_id],
                    }
                    if stored.content_hash in stories:
                        descriptor["source_ids"] = sorted(
                            set(stories[stored.content_hash]["source_ids"]) | {result.source_id}
                        )
                    stories[stored.content_hash] = descriptor
                sources.append(
                    {
                        "source_id": result.source_id,
                        "fetched_at": (result.fresh_as_of or result.fetched_at).isoformat(),
                        "newest_at": newest.isoformat() if newest else None,
                        "fixture_only": result.fixture_only,
                        "complete": result.complete,
                        "entry_eligible": result.entry_eligible,
                        "language_supported": language_ok,
                        "error": None,
                    }
                )
            # Keep recent high-risk stories even when they disappear from a rolling feed.
            for old in previous["stories"] if previous else ():
                if (
                    old["content_hash"] not in stories
                    and parse_time(old["risk_observed_at"])
                    + timedelta(minutes=cfg.news_headline_block_minutes)
                    >= now
                ):
                    stories[old["content_hash"]] = old
            if len(stories) > cfg.news_max_total_items:
                raise NewsInvalid("aggregate_news_bound")
            data = self._base(
                refreshing=False,
                sources=sources,
                calendar=calendar,
                calendar_error=calendar_error,
                stories=sorted(stories.values(), key=lambda s: (s["published_at"], s["content_hash"])),
                fixture=fixture,
            )
            epoch, digest = self._publish(session, data, kind="complete" if calendar else "incomplete")
            self.database.add_audit(
                session,
                "news.refresh_committed",
                "news",
                {
                    "scope_hash": self.scope,
                    "snapshot_epoch": epoch,
                    "sources": len(sources),
                    "stories": len(stories),
                    "calendar_available": calendar is not None,
                    "fixture_only": fixture,
                },
            )
            return epoch, digest

    def list_news(self, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("bounded listing required")
        with self.database.session() as session:
            return [
                {
                    "id": r.id,
                    "published_at": r.time.isoformat(),
                    "first_seen_at": r.fetched_at.isoformat(),
                    "source": r.source,
                    "title": r.title,
                    "summary": r.summary,
                    "impact": r.impact,
                    "sentiment": r.sentiment,
                    "symbols": r.symbols,
                    "url": r.url,
                    "content_hash": r.content_hash,
                }
                for r in session.scalars(select(News).order_by(News.time.desc(), News.id.desc()).limit(limit))
            ]
