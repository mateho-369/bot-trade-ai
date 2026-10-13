"""Print configured feed identifiers/coverage. Default: no HTTP, credentials or broker connection.

``--fetch`` performs ONE read-only GET per configured RSS feed and the calendar adapter and reports
whether each one is usable for entry (items, newest publication, coverage). Never prints secrets.
"""

import argparse
import asyncio
import json

from core.settings import get_settings
from news.evidence import configured_source_ids


def describe(cfg):
    names = configured_source_ids(cfg)
    rows = []
    for name in names:
        policy = cfg.news_source_coverage.get(name)
        rows.append({"source_id": name, "coverage": policy.model_dump(mode="json") if policy else None})
    return {
        "mode": cfg.mode.value,
        "startup": "paused",
        "sources": rows,
        "unconfigured_required_ids": sorted(set(cfg.news_required_source_ids) - set(names)),
        "calendar_provider": cfg.calendar_provider,
        "news_unavailable_policy": cfg.news_unavailable_policy,
        "warning": "Scope and realtime/entitlement need owner review; zero HTTP calls performed.",
    }


async def probe(cfg, clock=None, transport=None):
    """One fetch per source. Returns {"sources": [...], "calendar": {...}, "ok": bool}."""
    from news.economic_calendar import calendar_adapter
    from news.http_client import NewsHTTP
    from news.rss_parser import RSSAdapter
    from trading.types import SystemClock

    clock = clock or SystemClock()
    http = NewsHTTP(cfg, transport=transport)
    sources, ok = [], True
    try:
        if cfg.use_free_news_sources and cfg.use_rss:
            for url in cfg.rss_urls:
                adapter = RSSAdapter(url, cfg, clock, http)
                policy = cfg.news_source_coverage.get(adapter.source_id)
                row = {"source_id": adapter.source_id, "reviewed": bool(policy and policy.reviewed)}
                try:
                    batch = await adapter.fetch()
                    newest = max((item.published_at for item in batch.items), default=None)
                    lag = (clock.now() - newest).total_seconds() if newest else None
                    fresh = (
                        policy is not None and lag is not None and lag <= policy.max_publication_lag_seconds
                    )
                    row.update(
                        items=len(batch.items),
                        newest_at=newest.isoformat() if newest else None,
                        fresh_enough=fresh,
                        error=None,
                    )
                    ok = ok and bool(policy and policy.reviewed and fresh)
                except Exception as exc:  # noqa: BLE001 - report the class only, never payloads.
                    row.update(items=0, newest_at=None, fresh_enough=False, error=type(exc).__name__)
                    ok = False
                sources.append(row)
        calendar = {"provider": cfg.calendar_provider}
        try:
            snap = await calendar_adapter(cfg, clock, http).fetch()
            calendar.update(
                source_id=snap.source_id,
                events=len(snap.events),
                high_impact=sum(1 for e in snap.events if e.impact == "high"),
                covered_from=snap.covered_from.isoformat(),
                covered_until=snap.covered_until.isoformat(),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            calendar.update(events=0, error=type(exc).__name__)
            ok = False
    finally:
        await http.close()
    return {"sources": sources, "calendar": calendar, "ok": ok and bool(sources)}


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="one read-only GET per source")
    args = parser.parse_args(argv)
    cfg = get_settings()
    report = describe(cfg)
    if not args.fetch:
        print(json.dumps(report, indent=2))
        return 0
    result = asyncio.run(probe(cfg))
    report.update(result, warning="Live read-only probe; owner still reviews feed scope.")
    print(json.dumps(report, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
