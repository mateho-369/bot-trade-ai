"""Read package DISTRIBUTION metadata only: no SDK/provider/package import, pip or subprocess."""

from __future__ import annotations

import importlib.metadata
import re
import sys

from readiness.contracts import Finding, InspectionError
from readiness.files import read_bytes

PIN = re.compile(r'([A-Za-z0-9_.-]{1,80})==([A-Za-z0-9_.+!-]{1,48})(?:; sys_platform == "win32")?')


def direct_pins(root, *, windows: bool):
    try:
        text = read_bytes(root / "requirements.txt", root=root, limit=65536).decode("ascii")
    except UnicodeError:
        raise InspectionError("invalid_dependency_pins") from None
    rows, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if match is None:
            raise InspectionError("invalid_dependency_pins")
        name, version = match.groups()
        key = re.sub(r"[-_.]+", "-", name).casefold()
        if key in seen:
            raise InspectionError("duplicate_dependency_pin")
        seen.add(key)
        if ";" not in line or windows:
            rows.append((name, version))
    if not rows or len(rows) > 80:
        raise InspectionError("invalid_dependency_pin_count")
    return tuple(rows)


def inspect_dependencies(root, *, windows=False, version_reader=None):
    reader = version_reader or importlib.metadata.version
    findings, matched, missing, different = [], 0, 0, 0
    try:
        pins = direct_pins(root, windows=windows)
        for name, expected in pins:
            try:
                observed = reader(name)
                matched += int(observed == expected)
                different += int(observed != expected)
            except importlib.metadata.PackageNotFoundError:
                missing += 1
            except Exception:
                different += 1  # Do not echo third-party metadata/errors that may contain private URLs.
        if missing or different:
            findings.append(
                Finding(
                    "direct_dependencies_missing_or_changed",
                    "blocked",
                    "Direct distribution versions do not all match the release pins; use a reviewed "
                    "platform-specific environment.",
                )
            )
        else:
            findings.append(
                Finding(
                    "direct_dependency_metadata_matches",
                    "passed",
                    "Pinned direct distribution metadata matches; no package/SDK was imported or contacted.",
                )
            )
        observations = {
            "required": len(pins),
            "matching": matched,
            "missing": missing,
            "different": different,
        }
    except InspectionError:
        findings.append(
            Finding(
                "dependency_inspection_refused",
                "blocked",
                "Dependency metadata could not be safely compared with exact direct release pins.",
            )
        )
        observations = {"required": 0, "matching": 0, "missing": 0, "different": 0}
    findings.append(
        Finding(
            "transitive_audit_not_performed",
            "not_checked",
            "Metadata matching is not pip consistency, transitive lock validation, vulnerability auditing "
            "or a native import test.",
        )
    )
    if sys.version_info < (3, 11):
        findings.append(Finding("python_target_not_met", "blocked", "Python 3.11 or newer is required."))
    return tuple(findings), observations
