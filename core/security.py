"""Secret-safe JSON and logging helpers. Authentication is added in Part 9."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from pydantic import SecretStr

SENSITIVE_KEY = re.compile(
    r"password|passwd|token|api[_-]?key|secret|authorization|cookie|init[_-]?data|private[_-]?key",
    re.IGNORECASE,
)
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|token|api[_-]?key|secret|authorization|init[_-]?data)\b"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
BOT_URL_TOKEN = re.compile(r"(https?://api\.telegram\.org/(?:file/)?bot)[^/\s?]+", re.IGNORECASE)
URL_CREDENTIALS = re.compile(r"(https?://|postgresql(?:\+psycopg)?://)([^/@\s]+@)", re.IGNORECASE)


def secret_values(settings: object) -> tuple[str, ...]:
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if raw:
                values.append(raw)
        elif isinstance(value, dict):  # e.g. AI_PROVIDERS keys resolved by env-var name.
            values.extend(
                v.get_secret_value()
                for v in value.values()
                if isinstance(v, SecretStr) and v.get_secret_value()
            )
    # Longer values first prevents partial redaction from exposing a suffix.
    return tuple(sorted(set(values), key=len, reverse=True))


def sanitize_text(text: object, secrets: Iterable[str] = ()) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = BEARER.sub("Bearer [REDACTED]", result)
    result = BOT_URL_TOKEN.sub(r"\1[REDACTED]", result)
    result = URL_CREDENTIALS.sub(r"\1[REDACTED]@", result)
    return ASSIGNMENT.sub(r"\1[REDACTED]", result)


def _json_default(value: object) -> object:
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite numbers are forbidden")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_data(value: Any, secrets: Iterable[str] = (), *, _depth: int = 0) -> Any:
    if _depth > 20:
        raise ValueError("audit payload exceeds nesting limit")
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("audit JSON keys must be strings")
        return {
            key: "[REDACTED]"
            if SENSITIVE_KEY.search(key)
            else sanitize_data(item, secrets, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item, secrets, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    if isinstance(value, (Decimal, datetime, Enum)):
        return _json_default(value)
    if value is None or isinstance(value, (bool, int, float)):
        # Final canonical_json check rejects NaN/Infinity.
        return value
    raise TypeError(f"unsupported audit type: {type(value).__name__}")
