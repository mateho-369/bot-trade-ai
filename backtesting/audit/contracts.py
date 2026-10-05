"""Fixed redacted research observations. Consistency is not provenance or permission."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from readiness.contracts import InspectionError
from readiness.files import strict_json

BUNDLE_NAME = "bundle.json"
BUNDLE_FORMAT = "reflex-replay-bundle-v1"
MAX_FILES = 256
MAX_SCAN_NODES = 4096
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ROWS = 200000
MAX_EQUITY_ROWS = 500002
MAX_LINE_BYTES = 32768
REQUIRED_FILES = frozenset(
    {
        "run.json",
        "report.json",
        "report.md",
        "completion.json",
        "signals.jsonl",
        "operations.jsonl",
        "equity.jsonl",
        "trades.jsonl",
        "ohlc_resolutions.jsonl",
        "data/replay.db",
        "data/paper/state.json",
    }
)
HASH_FIELDS = ("dataset_sha256", "code_hash", "model_sha256", "strategy_config_hash", "safety_config_hash")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    # Same UTF8/sorted/no-NaN contract as core.security, without Settings/ORM/ML import.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return sha(canonical(value).encode())


def valid_hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise InspectionError("bundle_hash_invalid")
    return value


def stamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise InspectionError("bundle_time_invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InspectionError("bundle_time_invalid") from None


def money(value, *, bound=Decimal("1e18")):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or re.fullmatch(r"-?\d+(?:\.\d+)?(?:[Ee][+-]?\d{1,3})?", value) is None
    ):
        raise InspectionError("bundle_exact_number_invalid")
    try:
        result = Decimal(value)
        if not result.is_finite() or abs(result) > bound or abs(result.as_tuple().exponent) > 100:
            raise ValueError
        return result
    except (ValueError, ArithmeticError):
        raise InspectionError("bundle_exact_number_invalid") from None


def object_json(raw, *, limit=MAX_JSON_BYTES):
    result = strict_json(raw, limit=limit)
    nodes = 0

    def walk(value):
        nonlocal nodes
        nodes += 1
        if nodes > 400000:
            raise InspectionError("bundle_json_node_bound")
        if isinstance(value, dict):
            if len(value) > 4096:
                raise InspectionError("bundle_json_object_bound")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            if len(value) > MAX_ROWS:
                raise InspectionError("bundle_json_array_bound")
            for item in value:
                walk(item)
        elif isinstance(value, str) and len(value) > 1048576:
            raise InspectionError("bundle_json_string_bound")

    walk(result)
    if not isinstance(result, dict):
        raise InspectionError("bundle_json_object_required")
    return result


def json_lines(raw, *, limit=MAX_ROWS):
    if not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES or type(limit) is not int:
        raise InspectionError("bundle_journal_size_bound")
    if raw and not raw.endswith(b"\n"):
        raise InspectionError("bundle_journal_incomplete_line")
    # Iterate without allocating raw.splitlines() copies of a large journal.
    import io

    rows = []
    for line in io.BytesIO(raw):
        if len(rows) >= limit or not 1 <= len(line) <= MAX_LINE_BYTES or line == b"\n":
            raise InspectionError("bundle_journal_row_bound")
        rows.append(object_json(line, limit=MAX_LINE_BYTES))
    return rows
