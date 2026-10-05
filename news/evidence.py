"""Symbol-bound, expiring, latest-epoch news proof. Hashes are integrity, not data truth."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace
from datetime import timedelta

from core.security import sha256_json
from core.settings import OperatingMode, Settings
from news.news_cache import FORMAT, latest_publication, load_snapshot, scope_hash
from news.types import CalendarSnapshot, EconomicEvent, NewsInvalid, parse_time, source_id
from trading.risk_types import NewsWindow, RuntimeProfile
from trading.types import BrokerError, SourceKind, valid_key

MIN_LOT_DEMO_REASON = "news_unavailable_min_lot_demo"
# Reasons that mean "the news/calendar SOURCE could not be read" (NEWS_UNAVAILABLE_POLICY scope).
SOURCE_UNAVAILABLE_REASONS = frozenset(
    {
        "required_headline_source_unavailable",
        "stale_or_quiet_headline_source",
        "incomplete_source_snapshot",
        "calendar_unavailable",
        "calendar_file_changed_or_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class NewsDecision:
    window: NewsWindow
    reasons: tuple[str, ...]
    blocking_ids: tuple[str, ...]
    sentiment: float | None

    @property
    def allowed(self):
        return self.window.known and self.window.safe


def _calendar(data):
    if data is None:
        return None
    try:
        if set(data) != {
            "source_id",
            "produced_at",
            "fetched_at",
            "covered_from",
            "covered_until",
            "currencies",
            "events",
            "complete",
            "origin",
            "fixture_only",
            "source_digest",
        }:
            raise ValueError
        events = []
        for row in data["events"]:
            if set(row) != {"event_id", "title", "currency", "starts_at", "ends_at", "impact", "tentative"}:
                raise ValueError
            events.append(
                EconomicEvent(
                    row["event_id"],
                    row["title"],
                    row["currency"],
                    parse_time(row["starts_at"]),
                    parse_time(row["ends_at"]),
                    row["impact"],
                    row["tentative"],
                )
            )
        return CalendarSnapshot(
            data["source_id"],
            parse_time(data["produced_at"]),
            parse_time(data["fetched_at"]),
            parse_time(data["covered_from"]),
            parse_time(data["covered_until"]),
            tuple(data["currencies"]),
            tuple(events),
            data["complete"],
            data["origin"],
            data["fixture_only"],
            data["source_digest"],
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        raise NewsInvalid("snapshot_calendar_shape") from None


def project_window(
    data: dict,
    *,
    epoch: int,
    snapshot_hash: str,
    logical_symbol: str,
    settings: Settings,
    profile: RuntimeProfile,
    now,
) -> NewsDecision:
    if logical_symbol not in settings.symbols:
        raise NewsInvalid("disabled_or_alias_symbol")
    if (
        data.get("format") != FORMAT
        or data.get("scope_hash") != scope_hash(settings, profile)
        or data.get("config_hash") != settings.safety_fingerprint()
        or data.get("code_hash") != profile.code_hash
        or data.get("market_source") != profile.data_source.value
        or type(data.get("fixture_only")) is not bool
    ):
        raise NewsInvalid("snapshot_scope_binding")
    published = parse_time(data["published_at"])
    if published > now:
        raise NewsInvalid("future_news_publication")
    cfg = settings
    reasons = []
    blocks = []
    ages = []
    known = True
    fresh_times = []
    scores = []
    exposures = set(cfg.symbol_news_currencies.get(logical_symbol, ()))
    if not exposures:
        known = False
        reasons.append("unknown_symbol_exposure")
    if data["refreshing"]:
        known = False
        reasons.append("refresh_in_progress")
    if not cfg.block_trading_high_impact_news:
        known = False
        reasons.append("blocking_policy_disabled")
    sources = {}
    for row in data["sources"]:
        if (
            set(row)
            != {
                "source_id",
                "fetched_at",
                "newest_at",
                "fixture_only",
                "complete",
                "entry_eligible",
                "language_supported",
                "error",
            }
            or row["source_id"] in sources
            or any(
                type(row[k]) is not bool
                for k in ("fixture_only", "complete", "entry_eligible", "language_supported")
            )
        ):
            raise NewsInvalid("source_snapshot_shape")
        source_id(row["source_id"])
        sources[row["source_id"]] = row
    configured = set(configured_source_ids(cfg))
    if sources and set(sources) != configured:
        known = False
        reasons.append("incomplete_source_snapshot")
    required = set(cfg.news_required_source_ids) if cfg.news_required_source_ids else configured
    relevant = []
    covered = set()
    for identifier in sorted(required):
        policy = cfg.news_source_coverage.get(identifier)
        if policy is not None and logical_symbol not in policy.symbols:
            continue
        relevant.append(identifier)
        row = sources.get(identifier)
        if policy is None or not policy.reviewed or not policy.realtime or policy.language != "en":
            known = False
            reasons.append("unreviewed_or_delayed_headline_scope")
            continue
        covered.update(policy.currencies)
        if (
            row is None
            or row["error"] is not None
            or not row["complete"]
            or not row["entry_eligible"]
            or not row["language_supported"]
            or row["fetched_at"] is None
            or row["newest_at"] is None
        ):
            known = False
            reasons.append("required_headline_source_unavailable")
            continue
        fetched, newest = parse_time(row["fetched_at"]), parse_time(row["newest_at"])
        if (
            fetched > published
            or newest > published
            or not 0 <= (now - fetched).total_seconds() <= cfg.news_max_age_seconds
            or not 0 <= (now - newest).total_seconds() <= policy.max_publication_lag_seconds
        ):
            known = False
            reasons.append("stale_or_quiet_headline_source")
            continue
        fresh_times.append(fetched)
        ages.extend(
            (
                fetched + timedelta(seconds=cfg.news_max_age_seconds),
                newest + timedelta(seconds=policy.max_publication_lag_seconds),
            )
        )
    if not relevant or not exposures.issubset(covered):
        known = False
        reasons.append("incomplete_headline_currency_coverage")
    calendar = _calendar(data["calendar"])
    mode = cfg.calendar_provider
    if mode == "auto":
        mode = "json_http" if cfg.calendar_source_url else "file"
    if calendar is not None and calendar.source_id != "calendar:" + mode:
        known = False
        reasons.append("calendar_source_policy_mismatch")
    if calendar is not None and calendar.source_id == "calendar:file":
        from news.economic_calendar import confined_read

        try:
            matching = (
                calendar.source_digest is not None
                and hashlib.sha256(confined_read(cfg, cfg.calendar_file)).hexdigest()
                == calendar.source_digest
            )
        except BrokerError:
            matching = False
        if not matching:
            known = False
            reasons.append("calendar_file_changed_or_unavailable")
    pre = timedelta(minutes=cfg.news_pre_event_minutes)
    post = timedelta(minutes=cfg.news_post_event_minutes)
    if not cfg.use_economic_calendar or calendar is None or data["calendar_error"] is not None:
        known = False
        reasons.append("calendar_unavailable")
    elif (
        not calendar.complete
        or not exposures.issubset(calendar.currencies)
        or calendar.covered_from > now - post
        or calendar.covered_until < now + pre
        or calendar.produced_at > published
        or calendar.fetched_at > published
        or not 0 <= (now - calendar.produced_at).total_seconds() <= cfg.calendar_max_age_seconds
        or calendar.source_id == "calendar:file"
        and not cfg.calendar_file_reviewed
        or calendar.source_id == "calendar:finnhub"
        and not cfg.finnhub_calendar_scope_reviewed
        or calendar.source_id == "calendar:faireconomy"
        and not cfg.calendar_faireconomy_reviewed
        or not calendar.events
        and not cfg.calendar_allow_empty_reviewed
    ):
        known = False
        reasons.append("stale_incomplete_or_unreviewed_calendar")
    if calendar:
        ages.extend(
            (
                calendar.produced_at + timedelta(seconds=cfg.calendar_max_age_seconds),
                calendar.covered_until - pre,
            )
        )
        for event in calendar.events:
            if event.currency not in exposures:
                continue
            severe = (
                event.impact in {"high", "unknown"}
                or event.tentative
                or cfg.news_impact_threshold == "medium"
                and event.impact == "medium"
            )
            if not severe:
                continue
            left, right = event.starts_at - pre, event.ends_at + post
            if left <= now <= right:
                blocks.append(event.event_id)
                reasons.append("economic_event_window")
            elif now < left:
                ages.append(left)  # Green evidence expires exactly when risk begins, without another poll.
    for story in data["stories"]:
        if (
            set(story)
            != {
                "content_hash",
                "title",
                "published_at",
                "first_seen_at",
                "risk_observed_at",
                "symbols",
                "impact",
                "sentiment",
                "source_ids",
            }
            or story["impact"] not in {"low", "medium", "high", "unknown"}
            or not isinstance(story["symbols"], list)
            or not isinstance(story["title"], str)
            or len(story["title"]) > 300
            or not isinstance(story["source_ids"], list)
        ):
            raise NewsInvalid("story_snapshot_shape")
        valid_key(story["content_hash"])
        stamp, seen = parse_time(story["published_at"]), parse_time(story["first_seen_at"])
        risk_at = parse_time(story["risk_observed_at"])
        score = story["sentiment"]
        if (
            stamp > seen
            or seen > published
            or not seen <= risk_at <= published
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not -1 <= score <= 1
        ):
            raise NewsInvalid("story_chronology_or_sentiment")
        if logical_symbol not in story["symbols"]:
            continue
        scores.append(score)
        severe = (
            story["impact"] in {"high", "unknown"}
            or cfg.news_impact_threshold == "medium"
            and story["impact"] == "medium"
        )
        if severe and stamp <= now <= risk_at + timedelta(minutes=cfg.news_headline_block_minutes):
            blocks.append(story["content_hash"])
            reasons.append("breaking_headline_window")
    fixture = (
        data["fixture_only"]
        or any(s["fixture_only"] for s in sources.values())
        or bool(calendar and calendar.fixture_only)
    )
    if fixture and profile.data_source != SourceKind.SYNTHETIC:
        known = False
        reasons.append("fixture_news_on_non_synthetic_market")
    expires = min(ages) if ages else published + timedelta(seconds=cfg.order_max_age_seconds)
    if expires <= now:
        known = False
        reasons.append("news_proof_expired")
    if (
        not known
        and not blocks
        and cfg.news_unavailable_policy == "min_lot_demo"
        and cfg.mode == OperatingMode.DEMO
        and not cfg.live_trading
        and reasons
        and set(reasons) <= SOURCE_UNAVAILABLE_REASONS
    ):
        # Owner opt-in, DEMO only: sources are truly UNAVAILABLE (never stale-unreviewed, fixture,
        # expired or a reported block). Execution then uses the broker minimum lot only.
        known = True
        reasons.append(MIN_LOT_DEMO_REASON)
    window = NewsWindow(
        known,
        known and not blocks,
        min(fresh_times) if fresh_times else None,
        calendar.produced_at if calendar else None,
        calendar.covered_until if calendar else None,
        None,
        logical_symbol,
        cfg.safety_fingerprint(),
        profile.code_hash,
        profile.data_source,
        snapshot_hash,
        epoch,
        expires,
        calendar.covered_from if calendar else None,
        fixture,
    )
    unique_reasons = tuple(sorted(set(reasons)))
    blocking = tuple(sorted(set(blocks)))
    digest = sha256_json(
        {
            "format": "reflex-news-window-v2",
            "window": asdict(window),
            "reasons": unique_reasons,
            "blocking_ids": blocking,
        }
    )
    window = replace(window, evidence_hash=digest)
    return NewsDecision(window, unique_reasons, blocking, sum(scores) / len(scores) if scores else None)


def configured_source_ids(settings: Settings):
    from news.types import rss_source_id

    names = []
    if settings.use_free_news_sources and settings.use_rss:
        names.extend(rss_source_id(url) for url in settings.rss_urls)
    if settings.newsapi_enabled:
        names.append("newsapi")
    if settings.finnhub_news_enabled:
        names.extend("finnhub:" + c for c in settings.finnhub_news_categories)
    if settings.cryptopanic_enabled:
        names.append("cryptopanic")
    return tuple(names)


def verify_window(
    session, window: NewsWindow, *, logical_symbol: str, settings: Settings, profile: RuntimeProfile, now
) -> bool:
    """Native evidence is mandatory; old unbound diagnostic DTOs remain synthetic-only."""
    try:
        if not isinstance(window, NewsWindow) or not window.allows(settings, now, logical_symbol):
            return False
        if not window.managed:
            return profile.data_source in {SourceKind.SYNTHETIC, SourceKind.TEST_SDK}
        if (
            window.data_source != profile.data_source
            or window.code_hash != profile.code_hash
            or window.logical_symbol != logical_symbol
        ):
            return False
        row = latest_publication(session, scope_hash(settings, profile))
        if (
            row is None
            or row.id != window.snapshot_epoch
            or row.details.get("snapshot_hash") != window.snapshot_hash
        ):
            return False
        if row.time > now or set(row.details) != {
            "scope_hash",
            "snapshot_hash",
            "kind",
            "config_hash",
            "code_hash",
            "market_source",
        }:
            return False
        if (
            row.details["config_hash"] != settings.safety_fingerprint()
            or row.details["code_hash"] != profile.code_hash
            or row.details["market_source"] != profile.data_source.value
        ):
            return False
        state = load_snapshot(settings, window.snapshot_hash)
        calendar = _calendar(state["calendar"])
        if calendar is not None and calendar.source_id == "calendar:file":
            from news.economic_calendar import confined_read

            if (
                calendar.source_digest is None
                or hashlib.sha256(confined_read(settings, settings.calendar_file)).hexdigest()
                != calendar.source_digest
            ):
                return False
        if row.time != parse_time(state["published_at"]):
            return False
        expected = project_window(
            state,
            epoch=row.id,
            snapshot_hash=window.snapshot_hash,
            logical_symbol=logical_symbol,
            settings=settings,
            profile=profile,
            now=now,
        ).window
        return expected == window and expected.allows(settings, now, logical_symbol)
    except (BrokerError, KeyError, ValueError, TypeError, AttributeError, ArithmeticError, OSError):
        return False
