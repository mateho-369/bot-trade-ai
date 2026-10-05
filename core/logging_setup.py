"""UTC JSON-lines rotating logs, including secret-safe tracebacks."""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from core.security import canonical_json, sanitize_text, secret_values

if TYPE_CHECKING:
    from core.settings import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        # Strict field allowlist; arbitrary logging extra/request bodies are not
        # serialized. JSON escapes newlines to prevent forged log entries.
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = "Invalid log formatting; raw body and arguments suppressed"
        payload = {
            "time": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": sanitize_text(record.name, self.secrets),
            "thread": sanitize_text(record.threadName, self.secrets),
            "message": sanitize_text(message, self.secrets),
        }
        if record.exc_info:
            payload["exception"] = sanitize_text(self.formatException(record.exc_info), self.secrets)
        if record.stack_info:
            payload["stack"] = sanitize_text(record.stack_info, self.secrets)
        return canonical_json(payload)


def configure_logging(settings: Settings) -> None:
    settings.ensure_runtime_dirs()
    formatter = RedactingFormatter(secret_values(settings))
    file_handler = RotatingFileHandler(
        settings.resolve_path(settings.log_file),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
        delay=False,
    )
    console_handler = logging.StreamHandler(sys.stderr)
    # Prevent logging internals from dumping raw args on formatter/I/O errors.
    logging.raiseExceptions = False
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.setLevel(settings.log_level)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # HTTP DEBUG logging can contain complete request data; never enable it.
    # APScheduler logs two INFO lines per job run (every 5-30 s): on a multi-day
    # run that noise would rotate real events out of the bounded log. Job errors
    # and missed runs are still WARNING/ERROR.
    for name in ("httpx", "httpcore", "sqlalchemy.engine", "aiogram.event", "apscheduler"):
        logging.getLogger(name).setLevel(logging.WARNING)


def recent_log_lines(settings: Settings, limit: int = 100) -> list[str]:
    if not 1 <= limit <= 500:
        raise ValueError("log limit must be between 1 and 500")
    path = settings.resolve_path(settings.log_file)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=limit)
    secrets = secret_values(settings)
    return [sanitize_text(line.rstrip("\n"), secrets) for line in lines]
