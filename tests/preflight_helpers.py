"""TEST ONLY preflight fixtures: miniature sealed source, private .env, sentinels, fakes.

Nothing here is a genuine Windows/VPS deployment, broker, provider or stage fact.
"""

import json
import os
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest

from readiness.preflight_checks import POWER_ARGV
from tests.readiness_helpers import source_tree

# Deliberate sentinels: a leak into any finding/observation/CLI output is a test failure.
BOT_TOKEN = "123456789:SENTINEL-BOT-TOKEN-VALUE-abcdefghijklmnop"
OPENAI_KEY = "sk-SENTINEL-OPENAI-KEY-VALUE"
MT5_PASSWORD = "SENTINEL-MT5-PASSWORD"
NEWS_KEY = "SENTINEL-NEWSAPI-KEY"


def private_environment(root, *, overrides=None, secret=True):
    """Write a reviewed-style private .env from the real template; never a genuine credential."""
    repository = Path(__file__).resolve().parents[1]
    text = (repository / ".env.example").read_text()
    values = {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN if secret else "",
        "TELEGRAM_REPORT_CHAT_ID": "123" if secret else "",
        "OPENAI_API_KEY": OPENAI_KEY if secret else "",
        "NEWS_API_KEY": NEWS_KEY if secret else "",
        **(overrides or {}),
    }
    lines = []
    seen = set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path = root / ".env"
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)  # Private POSIX bits required by the existing settings inspection.
    return path


@pytest.fixture
def deployment_root(tmp_path, monkeypatch):
    """Sealed miniature source root + private .env + writable state dirs; inspector identity patched."""
    root, doc, anchor = source_tree(tmp_path)
    monkeypatch.setattr("readiness.deployment_preflight.INSPECTOR_ROOT", root)
    # Positive SOFTWARE fixtures must not depend on temporary-disk pressure during a full suite.
    # The real 512 MiB production floor is unchanged; host tests exercise low/unknown capacity.
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("readiness.host.shutil.disk_usage", lambda _: usage(2**31, 0, 2**31))
    private_environment(root)
    for name in ("data", "data/logs", "data/backups", "data/runtime"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root, doc, anchor


def forbid_transports(monkeypatch):
    """Any socket/subprocess/native transport during a default preflight is a test failure."""
    import socket

    def never(*args, **kwargs):
        raise AssertionError("preflight must not use network/subprocess transport by default")

    monkeypatch.setattr(socket, "create_connection", never)
    monkeypatch.setattr(socket.socket, "connect", never)
    monkeypatch.setattr(subprocess, "Popen", never)
    monkeypatch.setattr(subprocess, "run", never)
    monkeypatch.setattr(os, "execv", never)
    monkeypatch.setattr(os, "spawnv", never)
    return never


def rows(document):
    """Accept either Finding dataclasses (unit seams) or serialized documents (CLI/runner)."""
    return [item if isinstance(item, dict) else item.to_dict() for item in document["findings"]]


def codes(document):
    return {item["code"]: item["status"] for item in rows(document)}


def dumped(document):
    return json.dumps(document, sort_keys=True, allow_nan=False, default=lambda value: value.to_dict())


def assert_no_secret(document):
    text = dumped(document)
    for secret in (BOT_TOKEN, OPENAI_KEY, MT5_PASSWORD, NEWS_KEY, "SENTINEL"):
        assert secret not in text, secret
    assert document["secret_values_printed"] is False


class FakePowerRunner:
    """Records the exact argv; never starts a real child process or changes a setting."""

    def __init__(self, *, stdout="Current AC Power Setting Index: 0x00000000\n", returncode=0, error=None):
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.error is not None:
            raise self.error
        return namedtuple("Completed", "returncode stdout stderr")(self.returncode, self.stdout, "")

    @property
    def safe_invocation(self):
        argv, kwargs = self.calls[0]
        return (
            argv == list(POWER_ARGV)
            and kwargs.get("shell") is False
            and kwargs.get("check") is False
            and kwargs.get("capture_output") is True
            and 1 <= kwargs.get("timeout", 0) <= 30
        )


def fake_http(handler):
    """Real httpx code path with a MockTransport: asserts requests without any network."""
    import httpx

    return httpx.MockTransport(handler)
