"""Frozen captured bytes and exact bundle closure; hashing does not authenticate the producer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from backtesting.audit.contracts import (
    BUNDLE_FORMAT,
    BUNDLE_NAME,
    HASH_FIELDS,
    MAX_DATABASE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    REQUIRED_FILES,
    object_json,
    sha,
    valid_hash,
)
from backtesting.audit.files import inventory
from readiness.contracts import InspectionError
from readiness.files import read_bytes, relative_name


@dataclass(frozen=True, slots=True)
class CapturedBundle:
    root: Path
    manifest_sha256: str
    manifest_json: str
    files: Mapping[str, bytes]
    trusted_anchor_supplied: bool

    @property
    def manifest(self):
        from backtesting.audit.contracts import object_json

        return object_json(self.manifest_json.encode())

    @classmethod
    def capture(cls, root, *, trusted_sha256=None):
        root, actual = inventory(root)
        if BUNDLE_NAME not in actual or "failure.json" in actual:
            raise InspectionError("completed_bundle_manifest_required")
        raw = read_bytes(root / BUNDLE_NAME, root=root, limit=1048576)
        digest = sha(raw)
        if trusted_sha256 is not None and valid_hash(trusted_sha256) != digest:
            raise InspectionError("bundle_trusted_digest_mismatch")
        body = object_json(raw, limit=1048576)
        if (
            set(body)
            != {
                "format",
                "purpose",
                "source",
                "completed",
                "promotion_eligible",
                "genuine_owner_authenticated",
                "production_model_activated",
                "manifest_self_hash_excluded",
                "bindings",
                "files",
            }
            or body["format"] != BUNDLE_FORMAT
            or body["purpose"] != "research_only"
            or body["source"] != "historical"
            or body["completed"] is not True
            or body["promotion_eligible"] is not False
            or body["genuine_owner_authenticated"] is not False
            or body["production_model_activated"] is not False
            or body["manifest_self_hash_excluded"] is not True
            or not isinstance(body["bindings"], dict)
            or set(body["bindings"]) != set(HASH_FIELDS)
            or not isinstance(body["files"], dict)
            or not len(REQUIRED_FILES) <= len(body["files"]) < MAX_FILES
        ):
            raise InspectionError("bundle_manifest_contract_invalid")
        for name in HASH_FIELDS:
            valid_hash(body["bindings"][name])
        declared = set()
        for name, record in body["files"].items():
            relative_name(name)
            if name == BUNDLE_NAME or not isinstance(record, dict) or set(record) != {"sha256", "bytes"}:
                raise InspectionError("bundle_file_record_invalid")
            valid_hash(record["sha256"])
            limit = MAX_DATABASE_BYTES if name == "data/replay.db" else MAX_FILE_BYTES
            if type(record["bytes"]) is not int or not 0 <= record["bytes"] <= limit:
                raise InspectionError("bundle_file_record_bound")
            declared.add(name)
        if len(raw) + sum(record["bytes"] for record in body["files"].values()) > MAX_TOTAL_BYTES:
            raise InspectionError("bundle_total_byte_bound")
        if any(name.endswith(("-wal", "-journal", "-shm")) for name in declared):
            raise InspectionError("bundle_sqlite_sidecar_refused")
        if len({name.casefold() for name in declared}) != len(declared):
            raise InspectionError("bundle_declared_case_collision")
        if not REQUIRED_FILES.issubset(declared) or actual != declared | {BUNDLE_NAME}:
            raise InspectionError("bundle_complete_file_set_mismatch")
        captures, total = {}, len(raw)
        for name in sorted(declared):
            limit = MAX_DATABASE_BYTES if name == "data/replay.db" else MAX_FILE_BYTES
            data = read_bytes(root / name, root=root, limit=limit)
            record = body["files"][name]
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise InspectionError("bundle_total_byte_bound")
            if len(data) != record["bytes"] or sha(data) != record["sha256"]:
                raise InspectionError("bundle_file_hash_or_length_changed")
            captures[name] = data
        if inventory(root)[1] != actual or read_bytes(root / BUNDLE_NAME, root=root, limit=1048576) != raw:
            raise InspectionError("bundle_changed_during_capture")
        return cls(root, digest, raw.decode(), MappingProxyType(captures), trusted_sha256 is not None)

    def unchanged(self):
        _, actual = inventory(self.root)
        if actual != set(self.files) | {BUNDLE_NAME}:
            raise InspectionError("bundle_changed_during_audit")
        if read_bytes(self.root / BUNDLE_NAME, root=self.root, limit=1048576) != self.manifest_json.encode():
            raise InspectionError("bundle_changed_during_audit")
        for name, before in self.files.items():
            limit = MAX_DATABASE_BYTES if name == "data/replay.db" else MAX_FILE_BYTES
            if read_bytes(self.root / name, root=self.root, limit=limit) != before:
                raise InspectionError("bundle_changed_during_audit")
