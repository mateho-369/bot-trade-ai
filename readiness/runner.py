"""Explicit sequential read-only preflight. Integrity precedes credentials/config/data inspection."""

from __future__ import annotations

from pathlib import Path

from readiness.contracts import Profile, ReportBuilder
from readiness.dependencies import inspect_dependencies
from readiness.host import inspect_host
from readiness.integrity import verify_release

INSPECTOR_ROOT = Path(__file__).absolute().parents[1]


def preflight(
    root: Path,
    *,
    profile: Profile = "development",
    env_name=None,
    inspect_sqlite_snapshot=False,
    manifest_name="docs/RELEASE_15_MANIFEST.json",
    trusted_manifest_sha256=None,
):
    if profile not in {"development", "windows_native"} or type(inspect_sqlite_snapshot) is not bool:
        raise ValueError("invalid diagnostic request")
    report = ReportBuilder(profile)
    integrity = verify_release(
        root, manifest_name=manifest_name, trusted_manifest_sha256=trusted_manifest_sha256
    )
    if not integrity.integrity_verified:
        report.findings.extend(integrity.findings)
        report.add(
            "config_and_state_inspection_skipped",
            "not_checked",
            "Integrity was not established; no configuration, credential or state file was inspected.",
        )
        return report.document(integrity)
    root = Path(root).absolute()
    if root != INSPECTOR_ROOT:
        report.add(
            "inspector_source_root_mismatch",
            "blocked",
            "Preflight must run from the inspected source install. The standalone verifier may inspect "
            "another root; no foreign-root config/state was loaded.",
        )
        return report.document(integrity)
    findings, observations = inspect_dependencies(root, windows=profile == "windows_native")
    report.findings.extend(findings)
    report.observations["dependencies"] = observations
    # Deferred import: standard-library verifier remains usable without Pydantic/SQLAlchemy/SDK.
    from readiness.settings_inspection import inspect_settings

    cfg, findings, observations = inspect_settings(root, env_name=env_name, profile=profile)
    report.findings.extend(findings)
    report.observations["configuration"] = observations
    findings, observations = inspect_host(
        root, profile=profile, terminal_path=cfg.mt5_terminal_path if cfg else None
    )
    report.findings.extend(findings)
    report.observations["host"] = observations
    if cfg is not None and inspect_sqlite_snapshot:
        try:
            from sqlalchemy.engine import make_url

            from readiness.sqlite_snapshot import inspect_sqlite

            url = make_url(cfg.database_url.get_secret_value())
            if (
                url.get_backend_name() != "sqlite"
                or not url.database
                or url.database == ":memory:"
                or url.query
            ):
                raise ValueError
            findings, observations = inspect_sqlite(cfg.resolve_path(url.database), root=root)
            report.findings.extend(findings)
            report.observations["sqlite_snapshot"] = observations
        except Exception:
            report.add(
                "persistent_local_sqlite_required",
                "blocked",
                "This offline snapshot path supports persistent local SQLite only; no PostgreSQL/server "
                "connection or fallback was attempted.",
            )
    else:
        report.add(
            "database_state_not_inspected",
            "blocked" if profile == "windows_native" else "not_checked",
            "Database schema/state was not inspected. Use the explicit stopped/checkpointed SQLite "
            "snapshot flag or separately validate PostgreSQL; no ledger was created.",
        )
    report.add(
        "diagnostic_not_native_qualification",
        "not_checked",
        "Offline checks are not actual-platform soak, restore, SDK/provider/owner/security tests, signed "
        "stage evidence or live approval.",
    )
    return report.document(integrity)
