"""Read-only local view of bounded runtime health and local report files.

No broker/provider/Telegram/database mutations or HTTP listener are started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.process_guard import operator_stop_requested, read_json
from core.settings import Settings

MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_TAIL_BYTES = 96 * 1024


def _health(settings) -> dict:
    try:
        value = read_json(settings.resolve_path(settings.runtime_health_file), max_bytes=65536)
        updated = datetime.fromisoformat(value["updated_at"])
        if updated.tzinfo is None:
            raise ValueError
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if not -5 <= age <= settings.watchdog_stale_seconds:
            raise ValueError
        return {
            "status": value.get("status")
            if value.get("status") in {"starting", "ready", "degraded", "stopping", "stopped"}
            else "invalid",
            "control": value.get("control")
            if value.get("control") in {"running", "paused", "killed"}
            else "unknown",
            "reconciled": value.get("reconciled") is True,
            "components_ready": value.get("components_ready") is True,
            "ai_healthy": value.get("ai_healthy") is True,
            "writes_quarantined": value.get("writes_quarantined") is True,
            "age_seconds": round(age, 1),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "missing_or_stale", "control": "unknown", "reconciled": False}


def _tail(path: Path, lines: int) -> list[str]:
    if path.is_symlink() or not path.is_file():
        return []
    info = path.stat()
    if info.st_size > MAX_LOG_BYTES or info.st_nlink != 1:
        return ["[report file refused: size/link bound]"]
    with path.open("rb") as source:
        source.seek(max(0, info.st_size - MAX_TAIL_BYTES))
        raw = source.read(MAX_TAIL_BYTES + 1)
    if len(raw) > MAX_TAIL_BYTES + 1:
        return ["[report tail refused: size bound]"]
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        return ["[report file refused: invalid UTF-8]"]
    values = text.splitlines()
    if info.st_size > MAX_TAIL_BYTES and values:
        values = values[1:]
    return values[-lines:]


def snapshot(settings, *, lines: int) -> dict:
    reports_dir = settings.resolve_path(Path(settings.data_dir) / "reports")
    if reports_dir.is_symlink():
        raise ValueError("report directory symlink refused")
    actions = _tail(reports_dir / "actions.log", lines)
    daily = reports_dir / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    daily_rows = []
    for raw in _tail(daily, min(lines, 20)):
        try:
            row = json.loads(raw)
            daily_rows.append(
                {
                    "time": str(row.get("time", ""))[:32],
                    "kind": str(row.get("kind", ""))[:48],
                    "language": row.get("language") if row.get("language") in {"en", "km"} else "unknown",
                    "message": str(row.get("message", ""))[:1500],
                }
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            daily_rows.append({"kind": "invalid_report_row"})
    return {
        "health": _health(settings),
        "operator_stop_requested": operator_stop_requested(settings),
        "actions_log": str(reports_dir / "actions.log"),
        "recent_actions": actions,
        "daily_reports": daily_rows,
        "not_live_authorization": True,
    }


def _render(settings, *, lines: int) -> str:
    view = snapshot(settings, lines=lines)
    health = view["health"]
    rows = [
        "MT5 AI ReflexBot — LOCAL READ-ONLY VIEW",
        f"Runtime: {health.get('status')} | Control: {health.get('control')} | "
        f"Age: {health.get('age_seconds', 'n/a')}s",
        f"Reconciled: {health.get('reconciled', False)} | "
        f"Components ready: {health.get('components_ready', False)} | "
        f"AI healthy: {health.get('ai_healthy', False)}",
        f"Broker writes quarantined: {health.get('writes_quarantined', 'unknown')} | "
        f"Stop requested: {view['operator_stop_requested']}",
        f"Local actions: {view['actions_log']}",
        "",
        "Recent actions:",
        *(view["recent_actions"] or ["(no local reports yet)"]),
    ]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only local runtime and report viewer; no network listener"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--watch", action="store_true", help="refresh every 2 seconds until Ctrl+C")
    args = parser.parse_args(argv)
    if not 1 <= args.lines <= 100:
        parser.error("--lines must be between 1 and 100")
    try:
        env_file = args.env_file.expanduser().resolve()
        if env_file.is_file():
            settings = Settings(_env_file=env_file, project_root=env_file.parent)
        else:
            settings = Settings(_env_file=None, project_root=Path.cwd())
        if args.watch:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                print(_render(settings, lines=args.lines), flush=True)
                time.sleep(2)
        print(_render(settings, lines=args.lines))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "unavailable", "error_kind": type(error).__name__, "raw_error_printed": False}
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
