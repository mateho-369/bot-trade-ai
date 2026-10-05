"""Atomic, checksummed shadow-broker checkpoint. Not tamper-proof authentication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from core.security import canonical_json
from trading.types import RiskViolation


class AtomicSnapshotStore:
    def __init__(self, path: Path, *, max_bytes: int = 16777216):
        self.path, self.max_bytes = path, max_bytes

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or self.path.stat().st_size > self.max_bytes:
            raise RiskViolation("paper checkpoint is symlinked/oversized")
        try:

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate checkpoint key")
                    result[key] = value
                return result

            raw = self.path.read_bytes()
            if len(raw) > self.max_bytes:
                raise ValueError("checkpoint grew while loading")
            envelope = json.loads(raw, object_pairs_hook=unique)
            body = canonical_json(envelope["state"]).encode()
            if envelope.get("version") != 1 or hashlib.sha256(body).hexdigest() != envelope.get("sha256"):
                raise ValueError("checkpoint digest")
            return envelope["state"]
        except Exception:
            raise RiskViolation("paper checkpoint is corrupt; NEVER reset capital automatically") from None

    def save(self, state: dict) -> None:
        body = canonical_json(state).encode()
        payload = canonical_json(
            {"version": 1, "sha256": hashlib.sha256(body).hexdigest(), "state": state}
        ).encode()
        if len(payload) > self.max_bytes:
            raise RiskViolation("paper checkpoint exceeds the reviewed storage bound")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RiskViolation("paper checkpoint may not be a symlink")
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=".paper-", suffix=".tmp", delete=False
            ) as handle:
                name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
