"""Local operator DOWNWARD-only persistent stop request, no trading endpoint.

The supervisor and child observe the request; no process is killed. Clearing the
request is a deliberate local action and never clears broker/risk/kill latches.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.process_guard import ProcessLock, operator_stop_path, request_operator_stop
from core.settings import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Persistently stop local runtime/supervisor without force kill"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--clear-request", action="store_true", help="requires stopped supervisor; NOT resume/reset"
    )
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            print("Reviewed .env required.")
            return 2
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        if args.clear_request:
            with ProcessLock(settings.resolve_path(settings.watchdog_lock_file)):
                with ProcessLock(settings.resolve_path(settings.runtime_lock_file)):
                    operator_stop_path(settings).unlink(missing_ok=True)
            print("Local stop request cleared ONLY. Runtime still starts paused; safety latches unchanged.")
        else:
            request_operator_stop(settings)
            print(
                "Persistent stop requested. Wait for clean child exit; "
                "never assume an in-flight write was recalled."
            )
        return 0
    except Exception:
        print("Local stop operation refused/failed; no process was force-killed or safety latch reset.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
