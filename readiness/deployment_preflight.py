"""Explicit Windows/VPS deployment preflight orchestrator.

Read-only by default and NEVER trading authorization. Integrity is attempted first; source
integrity failure blocks the result but the remaining local environment observations still
run, because they are read-only and the operator needs them. Configuration/state inspection
is bound to this inspected source root only.
"""

from __future__ import annotations

from pathlib import Path

from readiness.contracts import InspectionError, Profile, ReportBuilder
from readiness.dependencies import inspect_dependencies
from readiness.files import checked_path, read_bytes
from readiness.host import inspect_host
from readiness.integrity import verify_release
from readiness.preflight_checks import (
    inspect_clock,
    inspect_env_keys,
    inspect_imports,
    inspect_power,
    inspect_runtime,
    inspect_telegram_format,
    inspect_terminal,
    inspect_writability,
    probe_ai_provider,
)
from readiness.settings_inspection import ENV_MAX_BYTES, dotenv_values, inspect_settings

INSPECTOR_ROOT = Path(__file__).absolute().parents[1]
REQUESTED = (
    "probe_writes",
    "check_ai_provider",
    "check_power_settings",
    "allow_native_import",
    "inspect_sqlite_snapshot",
)


def _document(report, integrity, extras):
    document = report.document(integrity)
    document.update(extras)
    document["format"] = "reflex-preflight-v1"
    return document


def deployment_preflight(
    root: Path,
    *,
    profile: Profile = "development",
    env_name: str | None = None,
    manifest_name="docs/RELEASE_15_MANIFEST.json",
    trusted_manifest_sha256=None,
    probe_writes=False,
    check_ai_provider=False,
    check_power_settings=False,
    allow_native_import=False,
    inspect_sqlite_snapshot=False,
    ai_transport=None,
    power_runner=None,
):
    """Bounded preflight. Optional flags are the ONLY way to probe writes, network or power."""
    if profile not in {"development", "windows_native"}:
        raise ValueError("invalid preflight profile")
    flags = {name: value for name, value in locals().items() if name in REQUESTED}
    if any(type(value) is not bool for value in flags.values()):
        raise ValueError("preflight flags must be explicit booleans")
    report = ReportBuilder(profile)
    extras = {
        "network_requests": 0,
        "child_processes_spawned": 0,
        "write_probes_performed": 0,
        "telegram_api_calls": 0,
        "ai_completion_requests": 0,
        "news_provider_calls": 0,
        "broker_connected": False,
        "mt5_launched": False,
        "task_registered": False,
        "service_installed": False,
        "database_created": False,
        "database_file_modified": False,
        "configuration_modified": False,
        "secret_values_printed": False,
        "owner_authenticated": False,
        "stage_evidence": False,
    }
    integrity = verify_release(
        root, manifest_name=manifest_name, trusted_manifest_sha256=trusted_manifest_sha256
    )
    if not integrity.integrity_verified:
        report.findings.extend(integrity.findings)
        report.add(
            "release_integrity_not_verified",
            "blocked",
            "Source/manifest integrity was not established. Remaining observations are read-only local "
            "environment facts and do NOT make this source trustworthy; re-extract from a trusted archive.",
        )
    root = Path(root).absolute()
    if root != INSPECTOR_ROOT:
        report.add(
            "inspector_source_root_mismatch",
            "blocked",
            "Preflight must run from the inspected source install; no foreign-root configuration, "
            "credential or state file was loaded.",
        )
        return _document(report, integrity, extras)

    findings, observations = inspect_runtime()
    report.findings.extend(findings)
    report.observations["runtime"] = observations
    windows = bool(observations["windows"])
    findings, observations = inspect_dependencies(root, windows=profile == "windows_native")
    report.findings.extend(findings)
    report.observations["dependencies"] = observations
    findings, observations = inspect_imports(
        root, windows=windows, allow_native=allow_native_import and profile == "windows_native"
    )
    report.findings.extend(findings)
    report.observations["imports"] = observations

    cfg, findings, observations = inspect_settings(root, env_name=env_name, profile=profile)
    report.findings.extend(findings)
    report.observations["configuration"] = observations
    if cfg is not None and not cfg.paper_trading:
        if cfg.demo_fast_track and cfg.mode.value == "demo" and not cfg.live_trading:
            report.add(
                "demo_fast_track_broker_stage",
                "warning",
                "Owner-approved DEMO_FAST_TRACK: DEMO broker orders only after the terminal itself reports a "
                "DEMO account (REAL/unknown/LIVE fail closed), minimum lot, start paused, never promotion "
                "evidence.",
            )
        else:
            report.add(
                "paper_trading_disabled_in_file",
                "blocked",
                "PAPER_TRADING must remain true for this stage. Preflight observes configuration and never "
                "rewrites it, credentials or control state.",
            )

    values = {}
    if env_name is not None:
        try:
            values = {
                key.upper(): value
                for key, value in dotenv_values(
                    read_bytes(checked_path(root / env_name, root=root), root=root, limit=ENV_MAX_BYTES)
                ).items()
            }
        except InspectionError:
            values = {}
    findings, observations = inspect_env_keys(root, env_name=env_name)
    report.findings.extend(findings)
    report.observations["environment_keys"] = observations
    findings, observations = inspect_telegram_format(values)
    report.findings.extend(findings)
    report.observations["telegram_format"] = observations
    extras["telegram_api_calls"] = observations["telegram_api_calls"]

    findings, observations = inspect_terminal(cfg.mt5_terminal_path if cfg else None, windows=windows)
    report.findings.extend(findings)
    report.observations["terminal"] = observations
    extras["mt5_launched"] = bool(observations["terminal_launched"])
    findings, observations = inspect_host(root, profile=profile, terminal_path=None)
    report.findings.extend(findings)
    report.observations["host"] = observations

    if cfg is None:
        report.add(
            "configuration_dependent_checks_skipped",
            "not_checked",
            "Writability, clock, provider and database observations need valid configuration; none was "
            "loaded, created or repaired.",
        )
    else:
        findings, observations = inspect_writability(cfg, root, probe=probe_writes)
        report.findings.extend(findings)
        report.observations["writability"] = observations
        extras["write_probes_performed"] = observations["write_probes_performed"]
        extras["database_file_modified"] = observations["database_file_modified"]
        http_date = None
        if check_ai_provider:
            findings, observations = probe_ai_provider(cfg, transport=ai_transport)
            report.findings.extend(findings)
            report.observations["ai_provider"] = observations
            extras["network_requests"] += observations["requests"]
            http_date = observations.get("http_date")
        else:
            report.add(
                "ai_provider_not_probed",
                "not_checked",
                "No provider request was sent. Pass --check-ai-provider for ONE bounded read-only "
                "listing GET; it is never a completion, broker, Telegram or news request.",
            )
        findings, observations = inspect_clock(cfg, http_date=http_date)
        report.findings.extend(findings)
        report.observations["clock"] = observations
        if inspect_sqlite_snapshot:
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
                    "The optional snapshot path supports a persistent local SQLite file only; no server "
                    "connection, checkpoint, migration or reset was attempted.",
                )
    findings, observations = inspect_power(windows=windows, enabled=check_power_settings, runner=power_runner)
    report.findings.extend(findings)
    report.observations["power"] = observations
    extras["child_processes_spawned"] = observations.get("child_processes_spawned", 0)
    report.add(
        "preflight_is_not_deployment_authorization",
        "not_checked",
        "Preflight never installs, registers a task, starts the terminal, connects a broker, resumes "
        "control state, qualifies a stage or authorizes trading. Windows session/ACL, feed, owner and "
        "soak validation remain operator obligations.",
    )
    return _document(report, integrity, extras)
