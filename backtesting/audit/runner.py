"""Read-only bounded research-bundle consistency audit.

No Settings load, writes, broker or operator controls.
"""

from __future__ import annotations

from backtesting.audit.integrity import CapturedBundle
from readiness.contracts import Finding, InspectionError


def audit_bundle(root, *, trusted_sha256=None):
    findings, observations, capture = [], {}, None
    integrity = False
    consistent = False
    try:
        capture = CapturedBundle.capture(root, trusted_sha256=trusted_sha256)
        integrity = True
        from backtesting.audit.semantics import inspect_semantics

        observations = inspect_semantics(capture)
        capture.unchanged()
        consistent = True
        findings.append(
            Finding(
                "research_bundle_internal_consistency",
                "passed",
                "Captured inputs, output journals, recomputed metrics and the closed private "
                "memory ledger agree; this is research consistency, NOT provenance or permission.",
            )
        )
        if observations["lightgbm_probability_observations_not_reexecuted"]:
            findings.append(
                Finding(
                    "lightgbm_inference_not_reexecuted",
                    "warning",
                    "LightGBM evidence/digests were checked, but its foreign-library inference "
                    "was NOT reexecuted by this read-only auditor.",
                )
            )
        if observations["private_snapshot_open_positions"]:
            findings.append(
                Finding(
                    "private_research_exposure_retained",
                    "warning",
                    "Completed research snapshot retains simulated positions. The auditor never "
                    "closes, resumes or changes the checkpoint/ledger.",
                )
            )
    except InspectionError as exc:
        observations = {}
        findings.append(
            Finding(
                exc.code,
                "blocked",
                "Research bundle inspection was refused without repairing "
                "files, opening the source DB through SQLite or exposing file/SQL values.",
            )
        )
    except Exception:
        observations = {}
        findings.append(
            Finding(
                "research_bundle_semantic_inspection_refused",
                "blocked",
                "The bounded research input contract could not be inspected; original "
                "paths/SQL/file values are not exposed and no state was changed.",
            )
        )
    findings.append(
        Finding(
            "research_audit_is_not_authenticity",
            "warning",
            "Hashes/declarations can be forged together. No authentic history/model "
            "availability, genuine owner/strategy/native qualification or live consent is established.",
        )
    )
    return {
        "format": "reflex-replay-bundle-audit-v1",
        "overall": "consistent_research_bundle" if consistent else "blocked",
        "research_only": True,
        "integrity_verified": integrity,
        "internal_consistency_verified": consistent,
        "bundle_sha256": capture.manifest_sha256 if capture else None,
        "trusted_anchor_supplied": trusted_sha256 is not None,
        "verified_file_count": len(capture.files) if capture else 0,
        "observations": observations,
        "findings": [finding.to_dict() for finding in findings],
        "original_db_opened_by_sqlite": False,
        "application_state_writes": 0,
        "broker_connected": False,
        "native_sdk_imported": False,
        "actual_native_broker_calls": 0,
        "actual_provider_network_calls": 0,
        "actual_telegram_network_calls": 0,
        "actual_child_processes_spawned": 0,
        "real_orders": 0,
        "owner_authenticated": False,
        "production_model_activated": False,
        "stage_evidence": False,
        "trading_authorized": False,
        "historical_provenance_verified": False,
        "native_validation_complete": False,
        "automatic_resume": False,
        "financial_history_reset": False,
    }
