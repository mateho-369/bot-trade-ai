"""Stopped local operator workflow. Import needs fresh signed owner initData, never a raw owner integer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy.engine import make_url

from backtesting.contracts import utc_time
from backtesting.dataset import file_bytes
from backtesting.promotion import export_ledger_stage, import_reviewed_stage
from core.database import Database
from core.settings import Settings
from trading.types import SystemClock


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Review stage evidence; no resume, live approval or broker calls"
    )
    parser.add_argument("--env-file", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export", help="Core-verified qualifying native paper/demo ledger only")
    export.add_argument("--stage", choices=("paper", "demo"), required=True)
    export.add_argument("--account-key", required=True, help="Private exact account scope from local SQL")
    export.add_argument("--from", dest="started_at", required=True)
    export.add_argument("--until", dest="finished_at", required=True)
    export.add_argument("--output", type=Path, required=True)
    review = sub.add_parser(
        "import", help="Append a separately reviewed production artifact; research output is rejected"
    )
    review.add_argument("--report", type=Path, required=True)
    review.add_argument("--confirm-sha256", required=True)
    review.add_argument(
        "--owner-initdata-file",
        type=Path,
        required=True,
        help="Private file containing ORIGINAL fresh signed Mini App initData; never pass bearer in argv",
    )
    args = parser.parse_args(argv)
    database = None
    try:
        if not args.env_file.is_file():
            raise ValueError
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        url = make_url(settings.database_url.get_secret_value())
        if url.get_backend_name() == "sqlite":
            if not url.database or url.database == ":memory:" or url.query:
                raise ValueError("stage review needs an existing persistent ledger")
            path = settings.resolve_path(url.database)
            if not path.is_file() or any(item.is_symlink() for item in (path, *path.parents)):
                raise ValueError("stage review never creates a missing SQLite ledger")
        database = Database(settings)
        database.verify_schema()  # Existing schema only; NEVER initialize/reset a production ledger here.
        clock = SystemClock()
        if args.action == "export":
            output = args.output if args.output.is_absolute() else settings.project_root / args.output
            result = export_ledger_stage(
                database,
                settings,
                clock,
                stage=args.stage,
                account_key=args.account_key,
                started_at=utc_time(args.started_at),
                finished_at=utc_time(args.finished_at),
                output=output,
            )
        else:
            raw = file_bytes(args.owner_initdata_file, limit=settings.telegram_initdata_max_bytes)
            # Strip a text-file newline only, not signed parameter whitespace or serialization.
            init_data = raw.decode("ascii", errors="strict").rstrip("\r\n")
            evidence_id = import_reviewed_stage(
                database,
                settings,
                clock,
                report_path=args.report,
                confirm_sha256=args.confirm_sha256,
                owner_init_data=init_data,
            )
            result = {
                "evidence_id": evidence_id,
                "evidence_inserted_or_existing": True,
                "grants_resume": False,
                "grants_live": False,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "error_kind": type(exc).__name__,
                    "grants_resume": False,
                    "grants_live": False,
                }
            )
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
