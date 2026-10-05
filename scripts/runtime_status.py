"""Local bounded health viewer. No broker/provider/bot calls or control writes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.process_guard import read_json
from core.settings import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local runtime health evidence, never permission to trade")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            return 2
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        data = read_json(settings.resolve_path(settings.runtime_health_file))
        updated = datetime.fromisoformat(data["updated_at"])
        if updated.tzinfo is None:
            raise ValueError
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        fresh = -5 <= age <= settings.watchdog_stale_seconds
        # Strict output allowlist, never echo arbitrary local JSON/SQL details.
        print(
            json.dumps(
                {
                    "status": data.get("status")
                    if data.get("status") in {"starting", "ready", "degraded", "stopping", "stopped"}
                    else "invalid",
                    "control": data.get("control")
                    if data.get("control") in {"running", "paused", "killed"}
                    else "unknown",
                    "fresh": fresh,
                    "age_seconds": round(age, 1),
                    "not_live_authorization": True,
                },
                sort_keys=True,
            )
        )
        return 0 if fresh and data.get("status") in {"ready", "degraded"} else 1
    except Exception:
        print("Local health absent/stale/invalid. No broker request or automatic recovery performed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
