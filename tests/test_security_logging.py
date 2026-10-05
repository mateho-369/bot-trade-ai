import io
import logging
import math
from decimal import Decimal

import pytest

from core.logging_setup import RedactingFormatter
from core.security import canonical_json, sanitize_data, sanitize_text, sha256_json


def test_nested_audit_secret_redaction():
    result = sanitize_data(
        {"password": "dont-store", "nested": {"initData": "raw-init-data"}, "reason": "secret-from-env"},
        ("secret-from-env",),
    )
    assert result == {"password": "[REDACTED]", "nested": {"initData": "[REDACTED]"}, "reason": "[REDACTED]"}


def test_traceback_and_message_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(("env-secret",)))
    logger = logging.getLogger("reflexbot.test.redaction")
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    try:
        raise RuntimeError("password=env-secret https://api.telegram.org/bot123:LEAK/sendMessage")
    except RuntimeError:
        logger.exception("Authorization: Bearer abc123")
    text = stream.getvalue()
    assert "env-secret" not in text
    assert "123:LEAK" not in text
    assert "abc123" not in text
    assert "Traceback" in text
    logger.handlers.clear()
    handler.close()


def test_url_and_unknown_assignment_redacted():
    value = sanitize_text("postgresql+psycopg://user:pass@localhost/db api_key=abcd")
    assert "user:pass" not in value and "abcd" not in value


def test_canonical_hash_stable():
    assert sha256_json({"b": 2, "a": 1}) == sha256_json({"a": 1, "b": 2})
    assert canonical_json({"money": Decimal("0.10")}) == '{"money":"0.10"}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, Decimal("NaN")])
def test_nonfinite_json_rejected(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


def test_deep_audit_payload_rejected():
    value = {}
    for _ in range(22):
        value = {"nested": value}
    with pytest.raises(ValueError):
        sanitize_data(value)


def test_log_message_newline_does_not_forge_another_entry():
    import json

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "line one\nFAKE ENTRY", (), None)
    output = RedactingFormatter().format(record)
    assert len(output.splitlines()) == 1
    assert json.loads(output)["message"] == "line one\nFAKE ENTRY"


def test_malformed_logging_does_not_dump_secret_arguments():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "%d", ("raw-secret",), None)
    output = RedactingFormatter().format(record)
    assert "raw-secret" not in output
    assert "suppressed" in output
