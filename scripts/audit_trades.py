"""Trade audit CLI: every trade must have a matching valid AI approval in the decision journal.

    python -m scripts.audit_trades            # read-only report
    python -m scripts.audit_trades --sync     # also fill the additive trade_attribution table first
    python -m scripts.audit_trades --json     # machine-readable

Exit codes: 0 clean, 1 at least one flag, 2 database/config unavailable. No broker or AI calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.settings import Settings


def run(settings: Settings, *, sync: bool = False, limit: int = 1000, clock=None) -> dict:
    from sqlalchemy import inspect
    from sqlalchemy.engine import make_url

    from ai.decision_journal import ensure_journal_tables
    from ai.trade_audit import audit_trades
    from core.database import Database
    from trading.types import SystemClock

    url = make_url(settings.database_url.get_secret_value())
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        if not settings.resolve_path(url.database).is_file():
            raise RuntimeError("database not initialised (run init-db first)")
    database = Database(settings)
    try:
        if not inspect(database.engine).has_table("trades"):
            raise RuntimeError("database not initialised (run init-db first)")
        ensure_journal_tables(database)
        return audit_trades(database, settings, clock or SystemClock(), limit=limit, run_sync=sync)
    finally:
        database.engine.dispose()


def main(argv=()) -> int:
    parser = argparse.ArgumentParser(description="Check every trade against its AI approval")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--sync", action="store_true", help="fill trade attribution before auditing")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    from ai.trade_audit import format_report

    try:
        env = args.env_file if args.env_file.is_file() else None
        root = args.env_file.resolve().parent if env else Path.cwd()
        settings = Settings(_env_file=env, project_root=root)
        report = run(settings, sync=args.sync, limit=max(1, min(args.limit, 100000)))
    except Exception as exc:  # noqa: BLE001 - class name only; never echo config/secrets.
        print(f"Trade audit unavailable ({type(exc).__name__}). No trades were changed.")
        if isinstance(exc, RuntimeError):
            print(str(exc))
        return 2
    print(json.dumps(report, indent=2, default=str) if args.json else format_report(report, limit=50))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
