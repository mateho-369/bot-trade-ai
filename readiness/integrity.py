"""Verify current-release bytes and runnable closure. Hashes are NOT signatures or stage evidence."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from readiness.contracts import Finding, InspectionError, IntegrityResult
from readiness.files import (
    MAX_DECLARED_FILES,
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_SCAN_NODES,
    MAX_TOTAL_BYTES,
    checked_path,
    read_bytes,
    relative_name,
    strict_json,
)

IGNORED = frozenset(
    {
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".arena",
        ".cache",
        ".local",
        "node_modules",
        "build",
        "dist",
        "coverage",
        ".next",
        ".vite",
        ".mypy_cache",
        ".tox",
        ".nox",
        "out",
        "target",
        "package",
        ".npm",
        ".nuxt",
        ".output",
        ".parcel-cache",
        ".svelte-kit",
        ".turbo",
        "data",
    }
)
RUNNABLE = frozenset({".py", ".pyw", ".pyi", ".ps1", ".vbs", ".js", ".html", ".css"})
CONFIG_NAMES = frozenset(
    {".env.example", ".gitignore", "requirements.txt", "requirements.linux.lock.txt", "pyproject.toml"}
)


def _secret_file(name):
    leaf = Path(name).name.lower()
    suffix = Path(leaf).suffix
    if leaf == ".env" or leaf.startswith(".env.") and leaf != ".env.example":
        return True
    if suffix in {".pem", ".key", ".initdata", ".db", ".sqlite", ".sqlite3", ".log", ".bak"}:
        return True
    return suffix not in {".py", ".md", ".js"} and ("initdata" in leaf or "init-data" in leaf)


def runnable_files(root: Path) -> set[str]:
    """Bounded walk; no following a symlink, runtime data or dependency/generated directories."""
    result, nodes = set(), 0
    root = checked_path(root)
    if not root.is_dir():
        raise InspectionError("source_root_not_directory")
    if os.name != "nt" and stat.S_IMODE(root.lstat().st_mode) & 0o022:
        raise InspectionError("source_directory_writable_by_others")
    try:
        for directory, folders, files in os.walk(root, topdown=True, followlinks=False, onerror=_walk_error):
            nodes += len(folders) + len(files)
            if nodes > MAX_SCAN_NODES:
                raise InspectionError("source_scan_too_large")
            folders[:] = [name for name in sorted(folders) if name not in IGNORED]
            for name in folders:
                folder = checked_path(Path(directory) / name, root=root)
                if os.name != "nt" and stat.S_IMODE(folder.lstat().st_mode) & 0o022:
                    raise InspectionError("source_directory_writable_by_others")
            for name in sorted(files):
                item = Path(directory) / name
                relative = item.relative_to(root).as_posix()
                if item.suffix.lower() in RUNNABLE or relative in CONFIG_NAMES:
                    relative_name(relative)
                    checked_path(item, root=root)
                    result.add(relative)
    except OSError:
        raise InspectionError("source_scan_refused") from None
    return result


def _walk_error(_):
    raise InspectionError("source_scan_refused")


def _manifest(document, name):
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise InspectionError("invalid_release_manifest")
    records = document["files"]
    if not 1 <= len(records) <= MAX_DECLARED_FILES:
        raise InspectionError("invalid_manifest_count")
    if not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", str(document.get("release", ""))):
        raise InspectionError("invalid_release_version")
    if document.get("database_schema") != 2 or type(document.get("database_schema")) is not int:
        raise InspectionError("unsupported_release_schema")
    for key, expected in (("hashed_files", len(records)), ("packaged_files", len(records) + 1)):
        if type(document.get(key)) is not int or document[key] != expected:
            raise InspectionError("manifest_count_mismatch")
    if document.get("manifest_self_hash_excluded") is not True or name in records:
        raise InspectionError("manifest_self_hash_contract")
    seen, total = set(), 0
    for relative, record in records.items():
        relative_name(relative)
        if relative.casefold() in seen or _secret_file(relative):
            raise InspectionError("manifest_path_collision_or_secret")
        seen.add(relative.casefold())
        if not isinstance(record, dict) or set(record) != {"sha256", "bytes"}:
            raise InspectionError("invalid_manifest_record")
        if not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
            raise InspectionError("invalid_manifest_digest")
        if type(record["bytes"]) is not int or not 0 <= record["bytes"] <= MAX_FILE_BYTES:
            raise InspectionError("invalid_manifest_file_size")
        total += record["bytes"]
    if total > MAX_TOTAL_BYTES:
        raise InspectionError("manifest_total_too_large")
    count = sum(Path(key).suffix == ".py" for key in records)
    if type(document.get("python_sources")) is not int or document["python_sources"] != count:
        raise InspectionError("manifest_python_count_mismatch")
    return records


def verify_release(
    root: Path, *, manifest_name="docs/RELEASE_15_MANIFEST.json", trusted_manifest_sha256=None
):
    findings, digest, valid, release, verified, count, python_count = [], None, False, None, 0, 0, 0
    anchored = trusted_manifest_sha256 is not None
    try:
        root = checked_path(root)
        relative_name(manifest_name)
        if anchored and (
            not isinstance(trusted_manifest_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", trusted_manifest_sha256)
        ):
            raise InspectionError("invalid_trusted_manifest_digest")
        raw = read_bytes(root / manifest_name, root=root, limit=MAX_MANIFEST_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if anchored and digest != trusted_manifest_sha256:
            raise InspectionError("trusted_manifest_digest_mismatch")
        document = strict_json(raw)
        records = _manifest(document, manifest_name)
        valid, release, count, python_count = (
            True,
            document["release"],
            len(records),
            document["python_sources"],
        )
        actual = runnable_files(root)
        declared = {key for key in records if Path(key).suffix.lower() in RUNNABLE or key in CONFIG_NAMES}
        if actual != declared:
            findings.append(
                Finding(
                    "runnable_set_mismatch",
                    "blocked",
                    "Runnable/config files differ from the current manifest; re-extract and review, never "
                    "delete history.",
                )
            )
        mismatches, unreadable, bytes_read = 0, 0, 0
        for name, record in records.items():
            try:
                data = read_bytes(root / name, root=root, limit=record["bytes"] or 1)
                bytes_read += len(data)
                if bytes_read > MAX_TOTAL_BYTES:
                    raise InspectionError("source_total_too_large")
                if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    mismatches += 1
                elif stat.S_IMODE((root / name).lstat().st_mode) & 0o022 and os.name != "nt":
                    findings.append(
                        Finding(
                            "source_writable_by_others",
                            "blocked",
                            "A declared source file is group/world writable; its executable provenance is "
                            "unsafe.",
                        )
                    )
                else:
                    verified += 1
            except InspectionError:
                unreadable += 1
        if mismatches:
            findings.append(
                Finding(
                    "declared_file_digest_mismatch",
                    "blocked",
                    "At least one declared file differs in raw length or SHA; modified files are not this "
                    "release.",
                )
            )
        if unreadable:
            findings.append(
                Finding(
                    "declared_file_read_refused",
                    "blocked",
                    "A declared file is missing, linked, changed, oversized or unreadable; complete bytes "
                    "were not verified.",
                )
            )
        # Re-read the manifest to reject an in-place manifest revision during a long scan.
        if read_bytes(root / manifest_name, root=root, limit=MAX_MANIFEST_BYTES) != raw:
            raise InspectionError("manifest_changed_during_inspection")
        if not any(item.status == "blocked" for item in findings) and verified == count:
            findings.append(
                Finding(
                    "release_bytes_verified",
                    "passed",
                    "All declared hashes/lengths and the bounded runnable/config file set match.",
                )
            )
        if not anchored:
            findings.append(
                Finding(
                    "trusted_anchor_not_supplied",
                    "warning",
                    "Content comparison only: an attacker can replace source AND manifest. Verify an "
                    "independently trusted archive/digest.",
                )
            )
    except InspectionError as exc:
        findings.append(
            Finding(
                exc.code,
                "blocked",
                "Current-release integrity inspection was refused; no application configuration or data "
                "should be opened.",
            )
        )
    except (OSError, ValueError, TypeError, RecursionError):
        findings.append(
            Finding(
                "release_inspection_refused",
                "blocked",
                "Current-release integrity inspection failed without disclosing external file values.",
            )
        )
    findings = list(dict.fromkeys(findings))
    ok = valid and verified == count and not any(item.status == "blocked" for item in findings)
    return IntegrityResult(
        valid, ok, anchored, digest, release, count, verified, python_count, tuple(findings)
    )
