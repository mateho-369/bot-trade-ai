"""Bounded strict JSON: never eval, repair, scan for a convenient object or load pickle."""

from __future__ import annotations

import json
import math

from trading.types import BrokerError


class AIUnavailable(BrokerError):
    def __init__(self, code: str = "provider_unavailable"):
        # Only internal codes; never attach the original HTTP exception/body.
        self.code = code
        super().__init__("AI provider unavailable; entry approval withheld")


class AIInvalidResponse(BrokerError):
    def __init__(self, code: str = "invalid_response"):
        self.code = code
        super().__init__("AI response rejected by strict contract validation")


def strict_json(
    raw: str | bytes,
    *,
    max_bytes: int = 32768,
    max_depth: int = 12,
    max_nodes: int = 4096,
    max_string: int = 16384,
    max_array: int = 512,
    allow_fence: bool = False,
) -> dict:
    try:
        if not isinstance(raw, (str, bytes)):
            raise ValueError
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        if len(text.encode("utf-8")) > max_bytes:
            raise ValueError
        text = text.strip()
        if allow_fence and text.startswith("```json\n") and text.endswith("\n```"):
            text = text[len("```json\n") : -len("\n```")].strip()

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        def constant(_):
            raise ValueError

        def integer(value):
            if len(value) > 24:
                raise ValueError
            return int(value)

        result = json.loads(text, object_pairs_hook=unique, parse_constant=constant, parse_int=integer)
        if not isinstance(result, dict):
            raise ValueError
        nodes = 0

        def walk(value, depth=0):
            nonlocal nodes
            nodes += 1
            if depth > max_depth or nodes > max_nodes:
                raise ValueError
            if isinstance(value, dict):
                if len(value) > 128:
                    raise ValueError
                for key, child in value.items():
                    if len(key) > 128:
                        raise ValueError
                    walk(child, depth + 1)
            elif isinstance(value, list):
                if len(value) > max_array:
                    raise ValueError
                for child in value:
                    walk(child, depth + 1)
            elif isinstance(value, str):
                if len(value) > max_string or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
                    raise ValueError
                value.encode("utf-8", errors="strict")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError

        walk(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise AIInvalidResponse("invalid_json") from None
