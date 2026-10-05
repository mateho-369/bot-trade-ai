"""Bounded diagnostic findings. A green check is NEVER an owner/stage/live approval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["passed", "blocked", "warning", "not_checked"]
Profile = Literal["development", "windows_native"]


class InspectionError(ValueError):
    """Deliberately fixed/redacted failure codes; never emit original file/SQL/config values."""

    def __init__(self, code: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,79}", code):
            code = "inspection_refused"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    status: Status
    message: str

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{1,79}", self.code)
            or self.status not in {"passed", "blocked", "warning", "not_checked"}
            or not isinstance(self.message, str)
            or not 1 <= len(self.message) <= 360
            or any(ord(c) < 32 for c in self.message)
        ):
            raise ValueError("invalid diagnostic finding")

    def to_dict(self):
        return {"code": self.code, "status": self.status, "message": self.message}


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    manifest_valid: bool = False
    integrity_verified: bool = False
    trusted_anchor_supplied: bool = False
    manifest_sha256: str | None = None
    release: str | None = None
    declared_files: int = 0
    verified_files: int = 0
    declared_python_sources: int = 0
    findings: tuple[Finding, ...] = ()

    def to_dict(self):
        return {
            "manifest_valid": self.manifest_valid,
            "integrity_verified": self.integrity_verified,
            "trusted_anchor_supplied": self.trusted_anchor_supplied,
            "manifest_sha256": self.manifest_sha256,
            "release": self.release,
            "declared_files": self.declared_files,
            "verified_files": self.verified_files,
            "declared_python_sources": self.declared_python_sources,
            "findings": [item.to_dict() for item in self.findings],
            "is_signature": False,
            "trading_permission": False,
        }


@dataclass(slots=True)
class ReportBuilder:
    profile: Profile
    findings: list[Finding] = field(default_factory=list)
    observations: dict = field(default_factory=dict)

    def add(self, code, status, message):
        self.findings.append(Finding(code, status, message))

    def document(self, integrity: IntegrityResult):
        if self.profile not in {"development", "windows_native"}:
            raise ValueError("invalid readiness profile")
        blocked = any(item.status == "blocked" for item in self.findings) or not integrity.integrity_verified
        return {
            "format": "reflex-readiness-v1",
            "profile": self.profile,
            "overall": "blocked" if blocked else "offline_checks_passed",
            "blocking_findings": sum(item.status == "blocked" for item in self.findings),
            "offline_only": True,
            "integrity": integrity.to_dict(),
            "observations": self.observations,
            "findings": [item.to_dict() for item in self.findings],
            "native_validation_complete": False,
            "owner_authenticated": False,
            "stage_evidence": False,
            "trading_authorized": False,
            "automatic_resume": False,
            "financial_history_reset": False,
            "broker_connected": False,
            "actual_native_broker_calls": 0,
            "actual_provider_network_calls": 0,
            "actual_telegram_network_calls": 0,
            "actual_child_processes_spawned": 0,
            "real_orders": 0,
            "application_state_writes": 0,
        }
