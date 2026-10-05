"""Print configured feed identifiers/coverage only; no HTTP, credentials or broker connection."""

import json

from core.settings import get_settings
from news.evidence import configured_source_ids


def main():
    cfg = get_settings()
    names = configured_source_ids(cfg)
    rows = []
    for name in names:
        policy = cfg.news_source_coverage.get(name)
        rows.append({"source_id": name, "coverage": policy.model_dump(mode="json") if policy else None})
    print(
        json.dumps(
            {
                "mode": cfg.mode.value,
                "startup": "paused",
                "sources": rows,
                "unconfigured_required_ids": sorted(set(cfg.news_required_source_ids) - set(names)),
                "calendar_provider": cfg.calendar_provider,
                "warning": "Scope and realtime/entitlement need owner review; zero HTTP calls performed.",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
