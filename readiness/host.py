"""OS facts + bounded terminal FILE metadata. No WinDLL/SDK load, account query or executable launch."""

from __future__ import annotations

import os
import shutil
import struct
import sys
from pathlib import Path

from readiness.contracts import Finding, InspectionError
from readiness.files import checked_path


def inspect_host(root, *, profile, terminal_path=None):
    findings = []
    windows = os.name == "nt" and sys.platform == "win32"
    bits = struct.calcsize("P") * 8
    observations = {"windows": windows, "interpreter_bits": bits, "terminal_file_observed": False}
    if profile == "windows_native":
        if not windows or bits != 64:
            findings.append(
                Finding(
                    "native_windows_x64_target_missing",
                    "blocked",
                    "Native deployment requires actual Windows x64; this platform cannot establish MT5 "
                    "readiness.",
                )
            )
        else:
            findings.append(
                Finding(
                    "native_os_architecture_observed",
                    "passed",
                    "Windows x64 OS/interpreter observed, not an MT5/interactive-session validation.",
                )
            )
        if terminal_path and windows:
            try:
                path = checked_path(Path(terminal_path))
                if not path.is_file() or path.suffix.lower() != ".exe" or path.stat().st_nlink != 1:
                    raise InspectionError("terminal_file_not_regular")
                observations["terminal_file_observed"] = True
                findings.append(
                    Finding(
                        "terminal_file_present",
                        "passed",
                        "Configured regular unlinked EXE exists; signature, broker distribution, "
                        "execution and login were NOT verified.",
                    )
                )
            except (InspectionError, OSError):
                findings.append(
                    Finding(
                        "terminal_file_not_verified",
                        "blocked",
                        "Configured terminal file is absent, linked, unreadable or not a regular EXE; no "
                        "launch was attempted.",
                    )
                )
        findings.append(
            Finding(
                "interactive_session_and_ntfs_acl_unverified",
                "blocked",
                "Logged-on non-Session-0 limited-user execution and actual NTFS/private-file ACLs require "
                "operator/native review.",
            )
        )
        findings.append(
            Finding(
                "native_feed_contract_and_owner_checks_unverified",
                "blocked",
                "Actual account kind, source, contracts, currencies, AI/news entitlements, Telegram/TLS "
                "and broker behavior were NOT contacted or validated.",
            )
        )
    else:
        findings.append(
            Finding(
                "development_platform_observed",
                "passed",
                "Development platform inspected; this profile does not qualify Windows/native deployment.",
            )
        )
    try:
        free = shutil.disk_usage(root).free
        observations["free_disk_meets_512mib_floor"] = free >= 512 * 1024 * 1024
        findings.append(
            Finding(
                "disk_space_floor",
                "passed" if observations["free_disk_meets_512mib_floor"] else "blocked",
                "Read-only free-space observation checked a 512 MiB diagnostic floor, not write "
                "permission or future capacity.",
            )
        )
    except OSError:
        observations["free_disk_meets_512mib_floor"] = None
        findings.append(
            Finding(
                "disk_space_unknown",
                "warning",
                "Free space could not be observed; no disk write probe was performed.",
            )
        )
    return tuple(findings), observations
