"""Inspect explicit dotenv/defaults after integrity. No credential transport or raw input/exception output."""

from __future__ import annotations

import io
import os
import re
import stat
from pathlib import Path

from readiness.contracts import Finding, InspectionError
from readiness.files import checked_path, read_bytes

ENV_MAX_BYTES = 65536


def dotenv_values(raw: bytes):
    """Use python-dotenv's real grammar, but reject duplicates/errors/interpolation instead of last-wins."""
    from dotenv.parser import parse_stream

    if not isinstance(raw, bytes) or len(raw) > ENV_MAX_BYTES:
        raise InspectionError("environment_file_too_large")
    try:
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff") or "\x00" in text or "${" in text:
            raise ValueError
        values = {}
        for binding in parse_stream(io.StringIO(text)):
            if binding.error:
                raise ValueError
            if binding.key is None:
                continue
            key = binding.key.casefold()
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key)
                or key in values
                or binding.value is None
                or len(values) >= 300
            ):
                raise ValueError
            values[key] = binding.value
        return values
    except (ValueError, UnicodeError):
        raise InspectionError("environment_document_refused") from None


def inspect_settings(root, *, env_name=None, profile="development"):
    findings, observations, cfg = [], {}, None
    try:
        from pydantic_settings import DotEnvSettingsSource

        from core.settings import Settings

        values = {}
        if env_name is not None:
            if env_name not in {".env", ".env.example"}:
                raise InspectionError("environment_must_be_source_root_file")
            path = checked_path(root / env_name, root=root)
            values = dotenv_values(read_bytes(path, root=root, limit=ENV_MAX_BYTES))
            if env_name == ".env" and os.name != "nt":
                info = path.lstat()
                if stat.S_IMODE(info.st_mode) & 0o077:
                    findings.append(
                        Finding(
                            "private_environment_permissions_unsafe",
                            "blocked",
                            "Private .env is group/world accessible; no chmod or permission change was "
                            "performed.",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "posix_environment_private_bits",
                            "passed",
                            "Private .env mode has no group/world access. This is not Windows ACL proof.",
                        )
                    )
        if "project_root" in values:
            if checked_path(Path(values.pop("project_root"))) != root:
                raise InspectionError("environment_root_scope_mismatch")

        # The actual validators and Env/DotEnv decoding/aliases are reused, not a parallel risk policy.
        # Init root is bound explicitly; process ENV/default .env/secrets directories are NOT sources.
        class FileOnlySettings(Settings):
            @classmethod
            def settings_customise_sources(
                cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
            ):
                source = DotEnvSettingsSource(settings_cls, env_file=None)
                source.env_vars = values
                return init_settings, source

        cfg = FileOnlySettings(_env_file=None, project_root=root)
        known = {key.casefold() for key in Settings.model_fields}
        known.update(
            str(field.validation_alias).casefold()
            for field in Settings.model_fields.values()
            if field.validation_alias is not None
        )
        override_count = sum(key.casefold() in known for key in os.environ)
        observations = {
            "source": "explicit_environment_file" if env_name else "immutable_defaults_only",
            "process_environment_values_used": False,
            "runtime_process_override_count": override_count,
            "mode": cfg.mode.value,
            "backend": cfg.mt5_backend,
            "start_paused": cfg.start_paused,
            "live_enabled_in_configuration": cfg.live_trading,
            "symbol_count": len(cfg.symbols),
            "telegram_pair_configured": bool(
                cfg.telegram_bot_token.get_secret_value() and cfg.telegram_owner_id
            ),
            "mt5_login_triplet_configured": bool(
                cfg.mt5_login and cfg.mt5_password.get_secret_value() and cfg.mt5_server
            ),
            "model_filter_enabled": cfg.model_filter_enabled,
            "config_fingerprint": cfg.safety_fingerprint(),
        }
        findings.append(
            Finding(
                "configuration_validated",
                "passed",
                "Explicit file/defaults passed the existing complete Settings validators; no settings "
                "were applied or credentials transported.",
            )
        )
        if override_count:
            findings.append(
                Finding(
                    "runtime_environment_overrides_present",
                    "blocked",
                    "Runtime recognizes process environment overrides ignored by this file-only "
                    "diagnostic. Use a reviewed clean shell; no values/names were printed.",
                )
            )
        if cfg.live_trading:
            findings.append(
                Finding(
                    "live_configuration_requires_separate_owner_gates",
                    "blocked",
                    "Live configuration was observed, not changed. This offline tool cannot authenticate "
                    "an owner or authorize live execution.",
                )
            )
        if not cfg.start_paused:
            findings.append(
                Finding(
                    "startup_pause_not_configured",
                    "blocked",
                    "The startup pause invariant must remain enabled; diagnostics never change control "
                    "state.",
                )
            )
        if profile == "windows_native":
            if env_name != ".env":
                findings.append(
                    Finding(
                        "native_private_environment_not_supplied",
                        "blocked",
                        "Native checks require an explicitly reviewed private source-root .env, not a "
                        "template/defaults.",
                    )
                )
            if cfg.mt5_backend != "real" or cfg.mode.value == "backtest":
                findings.append(
                    Finding(
                        "native_source_configuration_missing",
                        "blocked",
                        "Historical/mock configuration is not native MT5 provenance. No backend was "
                        "replaced.",
                    )
                )
            if not observations["telegram_pair_configured"]:
                findings.append(
                    Finding(
                        "native_owner_controls_unconfigured",
                        "blocked",
                        "Actual private owner controls are unconfigured; a configured pair alone would "
                        "still not authenticate an owner.",
                    )
                )
        for logical in (
            cfg.data_dir,
            cfg.paper_state_file,
            cfg.runtime_health_file,
            cfg.runtime_lock_file,
            cfg.watchdog_lock_file,
            cfg.backup_dir,
            cfg.log_file,
        ):
            path = cfg.resolve_path(logical)
            checked_path(path, root=root, missing=True)
        findings.append(
            Finding(
                "state_paths_scoped",
                "passed",
                "Configured state paths stay under this unlinked source root; none was created or opened "
                "for writing.",
            )
        )
        if cfg.model_filter_enabled:
            findings.append(
                Finding(
                    "active_model_qualification_not_checked",
                    "not_checked",
                    "Enabled model policy remains unchanged. Selected artifact/registry/provenance must "
                    "be validated by the ordinary owner/runtime gates.",
                )
            )
    except InspectionError as exc:
        cfg = None
        findings.append(
            Finding(
                exc.code,
                "blocked",
                "Explicit environment/path inspection refused without exposing original configuration or "
                "secret values.",
            )
        )
    except ImportError:
        cfg = None
        findings.append(
            Finding(
                "configuration_dependencies_unavailable",
                "blocked",
                "Configuration dependencies are absent; no install or settings fallback was attempted.",
            )
        )
    except Exception:
        cfg = None
        findings.append(
            Finding(
                "configuration_validation_refused",
                "blocked",
                "Existing configuration validation failed. Raw exception/input values are deliberately "
                "suppressed.",
            )
        )
    return cfg, tuple(findings), observations
