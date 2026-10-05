"""Windows/VPS deployment preflight checks. Read-only by default.

Never connects a broker, launches the MT5 terminal, registers a task, places an order,
resumes control state, prints a secret value or creates the production database. Optional
network is limited to ONE read-only AI-provider GET; optional write probes create and
remove only their own temporary files. Passing preflight is NEVER trading authorization.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from readiness.contracts import Finding, InspectionError
from readiness.dependencies import direct_pins
from readiness.files import checked_path, read_bytes
from readiness.settings_inspection import ENV_MAX_BYTES, dotenv_values

# Distribution pins that expose an importable module. Native SDK import stays opt-in.
IMPORTABLE = {
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "python-dotenv": "dotenv",
    "SQLAlchemy": "sqlalchemy",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "aiogram": "aiogram",
    "APScheduler": "apscheduler",
    "httpx": "httpx",
    "pandas": "pandas",
    "numpy": "numpy",
    "ta": "ta",
    "scikit-learn": "sklearn",
    "threadpoolctl": "threadpoolctl",
    "lightgbm": "lightgbm",
    "joblib": "joblib",
    "feedparser": "feedparser",
    "defusedxml": "defusedxml",
    "psutil": "psutil",
    "pytest": "pytest",
    "pytest-asyncio": "pytest_asyncio",
}
METADATA_ONLY = {"tzdata"}
NATIVE_IMPORTS = {"MetaTrader5": "MetaTrader5"}
# Required non-empty keys for a private deployment .env. Names are not secrets.
REQUIRED_KEYS = (
    "BACKTEST_MODE",
    "DEMO_MODE",
    "LIVE_TRADING",
    "PAPER_TRADING",
    "START_PAUSED",
    "MT5_BACKEND",
    "MT5_TERMINAL_PATH",
    "SYMBOLS",
    "ACCOUNT_CURRENCY",
    "PRIMARY_TIMEFRAME",
    "HIGHER_TIMEFRAME",
    "TREND_TIMEFRAME",
    "TRADING_DAY_TIMEZONE",
    "DATABASE_URL",
    "DATA_DIR",
    "LOG_FILE",
    "BACKUP_DIR",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_OWNER_ID",
)
# Blank is a legitimate deliberate configuration, never an automatic failure.
BLANK_ALLOWED_KEYS = (
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "OPENAI_API_KEY",
    "NEWS_API_KEY",
    "FINNHUB_API_KEY",
    "CRYPTOPANIC_API_KEY",
)
SECRET_PATTERN = re.compile(
    r"password|passwd|token|api[_-]?key|secret|authorization|cookie|init[_-]?data|private[_-]?key",
    re.IGNORECASE,
)
BOT_TOKEN = re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,64}$")
OWNER_ID = re.compile(r"^[1-9][0-9]{4,19}$")
DISK_FLOOR_BYTES = 512 * 1024 * 1024
PROBE_LIMIT_BYTES = 65536
CLOCK_SAMPLE_SECONDS = 0.2
POWER_ARGV = ("powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE")
POWER_HEX = re.compile(r"(?:AC|DC) Power Setting Index:\s*0x([0-9A-Fa-f]{1,8})")


def is_windows():
    return os.name == "nt" and sys.platform == "win32"


def masked(value):
    """Report presence/length only. Never echo, prefix, suffix or hash a secret value."""
    return f"<set:{len(value)}chars>" if value else "<empty>"


def secret_key(name):
    return bool(SECRET_PATTERN.search(name))


def inspect_runtime():
    import struct

    findings, observations = (
        [],
        {
            "python_version": ".".join(str(part) for part in sys.version_info[:3]),
            "interpreter_bits": struct.calcsize("P") * 8,
            "platform": sys.platform,
            "implementation": sys.implementation.name,
            "virtualenv_isolated": sys.prefix != sys.base_prefix,
            "windows": is_windows(),
        },
    )
    if sys.version_info < (3, 11):
        findings.append(Finding("python_target_not_met", "blocked", "Python 3.11 or newer is required."))
    else:
        findings.append(
            Finding(
                "python_runtime_observed",
                "passed",
                "Interpreter version/bits/platform observed; this is not a native MT5 wheel or broker "
                "compatibility validation.",
            )
        )
    if observations["windows"] and observations["interpreter_bits"] != 64:
        findings.append(
            Finding(
                "native_windows_x64_target_missing",
                "blocked",
                "The native MT5 wheel requires Windows x64; a 32-bit interpreter cannot load it.",
            )
        )
    if not observations["virtualenv_isolated"]:
        findings.append(
            Finding(
                "shared_interpreter_environment",
                "warning",
                "Running outside a dedicated virtualenv; dependency drift is more likely. No environment "
                "was created or modified.",
            )
        )
    return tuple(findings), observations


def inspect_imports(root, *, windows, allow_native=False, reader=None, loader=None):
    """Actual import test for ordinary packages; distribution metadata for data-only pins."""
    reader = reader or importlib.metadata.version
    loader = loader or importlib.import_module
    findings, imported, failed, skipped = [], 0, [], 0
    try:
        pins = direct_pins(root, windows=windows)
    except InspectionError:
        return (
            Finding(
                "dependency_inspection_refused",
                "blocked",
                "Dependency pins could not be parsed safely; no package was imported.",
            ),
        ), {"required": 0, "imported": 0, "failed": [], "native_import_attempted": False}
    for name, _version in pins:
        module = NATIVE_IMPORTS.get(name) or IMPORTABLE.get(name)
        if module is None or name in METADATA_ONLY:
            skipped += 1
            continue
        if module in NATIVE_IMPORTS.values():
            if not (allow_native and windows):
                skipped += 1
                continue
            # Opt-in ONLY: importing loads the native SDK module, never initialize()/login/orders.
            findings.append(
                Finding(
                    "native_sdk_import_requested",
                    "warning",
                    "The opt-in native SDK module import was requested. It loads broker binaries; it does "
                    "NOT connect, authenticate, subscribe or place an order.",
                )
            )
        try:
            reader(name)
            loader(module)
            imported += 1
        except Exception:
            failed.append(name)  # Distribution names only; never exception text or private paths.
    if failed:
        findings.append(
            Finding(
                "required_packages_not_importable",
                "blocked",
                "One or more pinned packages are missing or failed to import in this interpreter. Use the "
                "reviewed platform environment; nothing was installed or upgraded.",
            )
        )
    else:
        findings.append(
            Finding(
                "required_packages_importable",
                "passed",
                "Pinned packages imported successfully (native SDK only with the explicit opt-in flag).",
            )
        )
    if not (allow_native and windows) and any(name in NATIVE_IMPORTS for name, _ in pins):
        findings.append(
            Finding(
                "native_sdk_import_not_attempted",
                "not_checked",
                "The Windows-only native SDK module import was skipped by default. Metadata presence is not "
                "a DLL load, terminal launch or broker connection test.",
            )
        )
    return tuple(findings), {
        "required": len(pins),
        "imported": imported,
        "failed": tuple(failed),
        "metadata_or_native_skipped": skipped,
        "native_import_attempted": bool(allow_native and windows),
    }


def inspect_terminal(terminal_path, *, windows):
    """Existence/metadata of the configured terminal EXE. It is NEVER launched or connected."""
    if not terminal_path:
        return (
            Finding(
                "terminal_path_not_configured",
                "warning",
                "No MT5 terminal path is configured; read-only inspection cannot verify it.",
            ),
        ), {"terminal_file_observed": False, "terminal_launched": False}
    if not windows:
        return (
            Finding(
                "terminal_path_not_evaluated_off_windows",
                "not_checked",
                "A Windows terminal path was configured, but this platform cannot evaluate native MT5 "
                "deployment. No executable was launched.",
            ),
        ), {"terminal_file_observed": False, "terminal_launched": False}
    try:
        path = checked_path(Path(terminal_path))
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or path.suffix.lower() != ".exe"
            or path.name.casefold() != "terminal64.exe"
        ):
            raise InspectionError("terminal_file_not_regular")
        return (
            Finding(
                "terminal_file_present",
                "passed",
                "The configured regular unlinked terminal64.exe exists. Signature, broker distribution, "
                "login, quotes and execution were NOT verified and it was NOT started.",
            ),
        ), {
            "terminal_file_observed": True,
            "terminal_launched": False,
            "terminal_bytes": info.st_size,
            "terminal_mtime_ns": info.st_mtime_ns,
        }
    except (InspectionError, OSError, ValueError):
        return (
            Finding(
                "terminal_file_not_verified",
                "blocked",
                "The configured terminal file is absent, linked, unreadable or not terminal64.exe. No launch "
                "was attempted and no path was created.",
            ),
        ), {"terminal_file_observed": False, "terminal_launched": False}


def demo_fast_track_file(upper: dict) -> bool:
    def flag(name):
        return str(upper.get(name, "")).strip().lower()

    return (
        flag("DEMO_FAST_TRACK") == "true" and flag("DEMO_MODE") == "true" and flag("LIVE_TRADING") == "false"
    )


def inspect_env_keys(root, *, env_name):
    """Presence/emptiness of required keys with masked values; safe defaults must stay unchanged."""
    findings, observations = [], {"source": env_name or "immutable_defaults_only"}
    if env_name is None:
        findings.append(
            Finding(
                "environment_file_not_supplied",
                "warning",
                "No explicit .env/.env.example was supplied; key presence was not inspected and no ambient "
                "process environment value was read or printed.",
            )
        )
        return tuple(findings), observations
    if env_name not in {".env", ".env.example"}:
        raise InspectionError("environment_must_be_source_root_file")
    try:
        values = dotenv_values(read_bytes(root / env_name, root=root, limit=ENV_MAX_BYTES))
    except InspectionError:
        findings.append(
            Finding(
                "environment_document_refused",
                "blocked",
                "The environment file could not be parsed safely (duplicate/oversize/interpolation). Its "
                "contents were not echoed and no value was applied.",
            )
        )
        return tuple(findings), observations
    upper = {key.upper(): value for key, value in values.items()}
    missing = [key for key in REQUIRED_KEYS if key not in upper]
    empty = [key for key in REQUIRED_KEYS if key in upper and not str(upper[key]).strip()]
    blank_allowed = {key: masked(str(upper[key]).strip()) for key in BLANK_ALLOWED_KEYS if key in upper}
    # Name-based sensitivity only; values are never printed, hashed or partially echoed.
    secrets_present = sorted(key for key in upper if secret_key(key) and str(upper[key]).strip())
    template = env_name == ".env.example"
    observations.update(
        {
            "keys_observed": len(upper),
            "required_missing": tuple(missing),
            "required_empty": tuple(empty),
            "blank_allowed_keys_observed": tuple(sorted(blank_allowed)),
            "sensitive_named_keys_observed": tuple(secrets_present),
            "secret_values_printed": False,
        }
    )
    if missing or empty:
        findings.append(
            Finding(
                "required_configuration_keys_incomplete",
                "not_checked" if template else "blocked",
                "Required deployment keys are missing or empty. Key NAMES are reported; values are never "
                "printed. The template intentionally omits private credentials."
                if template
                else "Required deployment keys are missing or empty in the private .env; key names only are "
                "reported and no value was printed or applied.",
            )
        )
    else:
        findings.append(
            Finding(
                "required_configuration_keys_present",
                "passed",
                "All required deployment keys are present and non-empty; values were masked.",
            )
        )
    for key, expected in (
        ("LIVE_TRADING", "false"),
        ("PAPER_TRADING", "true"),
        ("START_PAUSED", "true"),
        ("DEMO_MODE", "true"),
    ):
        observed = str(upper.get(key, "")).strip().lower()
        if key == "PAPER_TRADING" and observed == "false" and demo_fast_track_file(upper):
            findings.append(
                Finding(
                    "demo_fast_track_broker_stage",
                    "warning",
                    "PAPER_TRADING=false is accepted only for the owner-approved DEMO_FAST_TRACK stage "
                    "(DEMO_MODE=true, LIVE_TRADING=false); the terminal must still report a DEMO account.",
                )
            )
            continue
        if observed and observed != expected:
            findings.append(
                Finding(
                    "safe_default_changed_in_file",
                    "blocked",
                    f"{key} deviates from the required safe default for this stage. Preflight "
                    "never rewrites configuration, credentials or control state.",
                )
            )
    if not template and any(name in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"} for name in missing + empty):
        findings.append(
            Finding(
                "owner_controls_unconfigured",
                "blocked",
                "Owner controls are required for a private deployment; no Telegram API call was made and no "
                "token was printed.",
            )
        )
    return tuple(findings), observations


def inspect_telegram_format(values):
    """Format-only owner credential validation. No API call, no token echo, no owner authentication."""
    token = str(values.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    owner = str(values.get("TELEGRAM_OWNER_ID", "") or "").strip()
    observations = {
        "token_format_valid": bool(BOT_TOKEN.fullmatch(token)),
        "owner_id_format_valid": bool(OWNER_ID.fullmatch(owner)),
        "telegram_api_calls": 0,
        "owner_authenticated": False,
        "token_observed": masked(token),
        "owner_id_observed": masked(owner),
    }
    if not token and not owner:
        return (
            Finding(
                "telegram_credentials_absent",
                "not_checked",
                "No Telegram credentials are configured, so format validation was skipped. Owner "
                "unavailable, which is the safe default for research/paper inspection.",
            ),
        ), observations
    findings = []
    if bool(token) != bool(owner):
        findings.append(
            Finding(
                "telegram_pair_incomplete",
                "blocked",
                "Bot token and owner ID must be configured together; a token without an owner ID cannot "
                "restrict commands, and an owner ID without a token cannot authenticate.",
            )
        )
    if token and not observations["token_format_valid"]:
        findings.append(
            Finding(
                "telegram_token_format_invalid",
                "blocked",
                "The bot token does not match the documented `<bot-id>:<secret>` shape. Format only was "
                "checked; no API call was made and the value was never printed.",
            )
        )
    if owner and not observations["owner_id_format_valid"]:
        findings.append(
            Finding(
                "telegram_owner_id_format_invalid",
                "blocked",
                "The owner ID must be a positive decimal user ID. No API call was made and the value was "
                "never printed.",
            )
        )
    if not findings:
        findings.append(
            Finding(
                "telegram_format_valid",
                "passed",
                "Bot token and owner ID have valid formats. This is NOT validity, ownership, entitlement or "
                "an authenticated owner; no Telegram request was sent.",
            )
        )
    return tuple(findings), observations


def _listing_model_count(raw):
    """Count listed models from a complete bounded body; an unparsable listing is NOT unreachability."""
    from ai.json_validation import strict_json

    try:
        body = strict_json(raw, max_bytes=PROBE_LIMIT_BYTES)
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    models = body.get("models") if isinstance(body.get("models"), list) else body.get("data")
    return len(models) if isinstance(models, list) else None


def probe_ai_provider(cfg, *, timeout=None, transport=None):
    """ONE bounded read-only provider GET. Never a broker/Telegram/news request or a completion."""
    import httpx

    provider = cfg.ai_provider
    if provider == "disabled":
        return (
            Finding(
                "ai_provider_disabled",
                "not_checked",
                "AI provider is disabled in configuration; no connectivity probe is meaningful and none was "
                "sent.",
            ),
        ), {"requests": 0, "http_date": None}
    if provider == "ollama":
        url = cfg.ollama_base_url.rstrip("/") + "/api/tags"
        headers = {}
    else:
        url = cfg.openai_base_url.rstrip("/") + "/models"
        key = cfg.openai_api_key.get_secret_value()
        headers = {"Authorization": "Bearer " + key} if key else {}
    seconds = timeout if timeout is not None else min(max(cfg.ai_timeout_seconds, 1), 10)
    observations = {
        "requests": 0,
        "status_code": None,
        "elapsed_ms": None,
        "model_count": None,
        "http_date": None,
        "provider": provider,
        "origin_printed": False,
    }
    started = time.monotonic()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(seconds), trust_env=False, follow_redirects=False, transport=transport
        ) as client:
            observations["requests"] = 1
            with client.stream(
                "GET", url, headers={"Accept": "application/json", "Accept-Encoding": "identity", **headers}
            ) as response:
                observations["status_code"] = response.status_code
                observations["http_date"] = response.headers.get("date")
                raw, truncated = bytearray(), False
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > PROBE_LIMIT_BYTES:
                        truncated = True
                        break  # Connectivity is already established; never buffer an unbounded body.
                    raw.extend(chunk)
        observations["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if observations["status_code"] == 200 and not truncated:
            observations["model_count"] = _listing_model_count(bytes(raw))
    except Exception:
        observations["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return (
            Finding(
                "ai_provider_unreachable",
                "warning",
                "The single optional read-only provider probe failed or timed out. Provider unavailability "
                "reduces AI supervision; it never enables trading and no retry, secret or URL was printed.",
            ),
        ), observations
    if observations["status_code"] == 200:
        return (
            Finding(
                "ai_provider_reachable",
                "passed",
                "One read-only provider listing request succeeded. This proves reachability only: not model "
                "quality, entitlement, quota, schema compliance or trading permission.",
            ),
        ), observations
    return (
        Finding(
            "ai_provider_unexpected_status",
            "warning",
            "The provider answered with a non-200 status. Credentials/entitlement were not validated and the "
            "response body was not printed.",
        ),
    ), observations


def inspect_writability(cfg, root, *, probe=False):
    """Read-only permission/space observation; opt-in temporary probes never touch the ledger."""
    findings, observations, probes = [], {"write_probes_performed": 0, "database_file_modified": False}, 0
    targets = {}
    try:
        from sqlalchemy.engine import make_url

        url = make_url(cfg.database_url.get_secret_value())
        if url.get_backend_name() != "sqlite":
            raise ValueError("not a local SQLite URL")  # Never probe/contact a database server.
        targets["database"] = (
            cfg.resolve_path(url.database) if url.database and url.database != ":memory:" else None
        )
    except Exception:
        targets["database"] = None
        findings.append(
            Finding(
                "database_url_not_local_sqlite",
                "warning",
                "The configured database URL is not a local SQLite path; writability was not probed and no "
                "server was contacted.",
            )
        )
    for name, logical in (
        ("data_dir", cfg.data_dir),
        ("log_dir", cfg.log_file.parent),
        ("backup_dir", cfg.backup_dir),
    ):
        try:
            targets[name] = cfg.resolve_path(logical)
        except (ValueError, OSError):
            targets[name] = None
            findings.append(
                Finding(
                    "state_path_outside_source_root",
                    "blocked",
                    f"The configured {name} escapes the inspected source root; no directory was created, "
                    "probed or repaired and no permission was changed.",
                )
            )
    for name, path in targets.items():
        if path is None:
            continue
        try:
            checked_path(path, root=root, missing=True)
        except (InspectionError, OSError, ValueError):
            findings.append(
                Finding(
                    "state_path_outside_source_root",
                    "blocked",
                    f"The configured {name} path escapes the inspected source root or is linked; no write "
                    "probe was performed and nothing was created.",
                )
            )
            continue
        exists = path.exists()
        writable = os.access(path if exists else path.parent, os.W_OK)
        observations[f"{name}_exists"] = exists
        observations[f"{name}_writable_bits"] = writable
        if name == "database" and not exists:
            findings.append(
                Finding(
                    "database_not_initialized",
                    "not_checked",
                    "No database file exists yet. Preflight never creates, initializes, migrates or resets a "
                    "ledger; use the explicit reviewed `main.py init-db` on a new dedicated database.",
                )
            )
            continue
        if not writable:
            findings.append(
                Finding(
                    "state_path_not_writable",
                    "blocked",
                    f"The configured {name} path is not writable by this user. No permission was changed and "
                    "no file was created.",
                )
            )
    if probe:
        database = targets.get("database")
        before = None
        if database is not None and database.exists():
            info = database.lstat()
            before = (info.st_size, info.st_mtime_ns, info.st_ino)
        for name in ("data_dir", "log_dir", "backup_dir"):
            path = targets.get(name)
            if path is None or not path.is_dir() or not os.access(path, os.W_OK):
                continue
            handle = None
            try:
                descriptor, temporary = tempfile.mkstemp(prefix=".preflight-", suffix=".tmp", dir=path)
                handle = os.fdopen(descriptor, "wb")
                handle.write(b"0")
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                handle = None
                probes += 1
            except OSError:
                findings.append(
                    Finding(
                        "write_probe_failed",
                        "blocked",
                        f"A temporary write probe in the configured {name} failed. No permission was changed "
                        "and no production file was created or modified.",
                    )
                )
            finally:
                if handle is not None:
                    handle.close()
                try:
                    os.unlink(temporary)
                except (OSError, UnboundLocalError):
                    pass
        if database is not None and database.exists() and before is not None:
            info = database.lstat()
            observations["database_file_modified"] = (info.st_size, info.st_mtime_ns, info.st_ino) != before
            if observations["database_file_modified"]:
                findings.append(
                    Finding(
                        "database_file_changed_during_probe",
                        "blocked",
                        "The existing database file changed while probes ran. Preflight never opens "
                        "it; stop concurrent runtimes and investigate before any deployment.",
                    )
                )
        if probes:
            findings.append(
                Finding(
                    "write_probes_completed",
                    "passed",
                    "Temporary probe files were created and removed in the configured data/log/backup "
                    "directories only; the existing database file was never opened.",
                )
            )
    else:
        findings.append(
            Finding(
                "write_probes_not_performed",
                "not_checked",
                "Only permission bits and free space were observed. Pass --probe-writes for a temporary "
                "create/remove probe that never opens the database file.",
            )
        )
    observations["write_probes_performed"] = probes
    return tuple(findings), observations


def inspect_clock(cfg, *, http_date=None, drift_warning_seconds=5.0):
    """Local clock sanity always; true external drift only from the single opt-in provider response."""
    findings, observations = [], {}
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(cfg.trading_day_timezone)
        now = datetime.now(timezone.utc)
        observations["trading_day_timezone_resolved"] = True
        observations["local_utc_offset_seconds"] = int(now.astimezone(zone).utcoffset().total_seconds())
    except Exception:
        observations["trading_day_timezone_resolved"] = False
        findings.append(
            Finding(
                "trading_day_timezone_unresolved",
                "blocked",
                "The configured trading-day timezone could not be resolved; daily loss/drawdown "
                "news windows depend on it. No timezone data was installed or changed.",
            )
        )
        return tuple(findings), observations
    wall_start, mono_start = time.time(), time.monotonic()
    time.sleep(CLOCK_SAMPLE_SECONDS)
    drift = abs((time.time() - wall_start) - (time.monotonic() - mono_start))
    observations["local_wall_monotonic_divergence_seconds"] = round(drift, 3)
    if drift > 1.0:
        findings.append(
            Finding(
                "local_clock_unstable_during_check",
                "warning",
                "Wall-clock and monotonic time diverged during the check, suggesting a clock adjustment or "
                "suspended/virtualized host. Trailing, order-age and news-freshness logic depend on time.",
            )
        )
    if http_date is not None:
        try:
            if not isinstance(http_date, str) or not http_date.strip():
                raise ValueError("HTTP Date header must be a non-empty string")
            reference = parsedate_to_datetime(http_date)
            if reference.tzinfo is None:
                raise ValueError
            seconds = (datetime.now(timezone.utc) - reference).total_seconds()
            observations["external_clock_drift_seconds"] = round(seconds, 3)
            if abs(seconds) > drift_warning_seconds:
                findings.append(
                    Finding(
                        "system_clock_drift_warning",
                        "warning",
                        "Local time differs from the provider's HTTP Date header beyond the tolerance. Order "
                        "age, initData age, news freshness and trailing are time-sensitive. No clock "
                        "or system setting was changed.",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "system_clock_within_tolerance",
                        "passed",
                        "Local time agreed with the single provider response header within tolerance; "
                        "not NTP authentication or a guarantee of future stability.",
                    )
                )
        except (ValueError, TypeError, OverflowError):
            observations["external_clock_drift_seconds"] = None
            findings.append(
                Finding(
                    "external_clock_reference_unusable",
                    "not_checked",
                    "The provider response carried no parsable Date header, so external drift was not "
                    "measured.",
                )
            )
    else:
        observations["external_clock_drift_seconds"] = None
        findings.append(
            Finding(
                "external_clock_drift_not_measured",
                "not_checked",
                "True drift needs an external reference. Only local sanity was checked; enable the single "
                "opt-in provider probe to compare an HTTP Date header. No NTP query was sent.",
            )
        )
    return tuple(findings), observations


def inspect_power(*, windows, enabled=False, runner=None):
    """Informational Windows sleep/standby observation. Settings are NEVER changed."""
    runner = runner or subprocess.run
    if not enabled:
        return (
            Finding(
                "power_settings_not_checked",
                "not_checked",
                "Power/sleep configuration was not queried. Pass --check-power-settings on Windows for an "
                "informational read-only `powercfg` query; nothing is ever modified.",
            ),
        ), {"power_settings_queried": False}
    if not windows:
        return (
            Finding(
                "power_settings_not_applicable",
                "not_checked",
                "Power configuration is queried only on Windows; no subprocess was started on this platform.",
            ),
        ), {"power_settings_queried": False}
    try:
        completed = runner(
            list(POWER_ARGV), shell=False, capture_output=True, text=True, timeout=15, check=False
        )
        text = (completed.stdout or "") + (completed.stderr or "")
        values = [int(match, 16) for match in POWER_HEX.findall(text)]
        if completed.returncode or not values:
            raise InspectionError("power_query_failed")
        observations = {
            "power_settings_queried": True,
            "standby_idle_seconds": tuple(values),
            "power_settings_modified": False,
            "child_processes_spawned": 1,
        }
        findings = [
            Finding(
                "power_settings_observed",
                "warning" if any(values) else "passed",
                "Sleep/standby timeouts were read only. A sleeping or suspended host stops the watchdog, "
                "trailing and news polling; configure the plan yourself in Windows settings.",
            )
        ]
        return tuple(findings), observations
    except (InspectionError, OSError, subprocess.SubprocessError, ValueError):
        return (
            Finding(
                "power_settings_query_refused",
                "warning",
                "The informational power query failed or timed out. No setting was changed and no raw output "
                "was printed.",
            ),
        ), {"power_settings_queried": False, "power_settings_modified": False}
