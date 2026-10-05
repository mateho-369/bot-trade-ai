"""Strict private historical selection consistency; NOT owner auth or authentic provenance.

This binding is produced only after the offline runner reconstructs the original
portable artifact from its frozen pre-replay corpus. It is rechecked when a selected
historical model is read, including at signal finalization and execution revalidation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backtesting.contracts import ReplayModelSelection, utc_time
from core.security import sha256_json
from core.settings import TIMEFRAME_MINUTES, OperatingMode
from trading.types import BrokerError, SourceKind, TradingDisabled, valid_key

BINDING_FORMAT = "reflex-replay-model-binding-v1"
BINDING_KEYS = {
    "format",
    "purpose",
    "source",
    "origin",
    "selection",
    "manifest_sha256",
    "dataset_sha256",
    "corpus_sha256",
    "account_scope_sha256",
    "model_sha256",
    "fold_sha256",
    "code_hash",
    "policy_hash",
    "feature_schema_hash",
    "evaluation_sha256",
    "sample_count",
    "exported_at",
    "label_horizon_through",
    "replay_from",
    "replay_until",
    "reconstruction",
    "genuine_provenance_verified",
    "promotion_eligible",
}


def validate_replay_binding(binding, payload, digest, settings, profile, now: datetime):
    """Re-read persisted selection rather than trusting a caller-supplied probability or flag."""
    try:
        if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
            raise ValueError
        if (
            settings.mode != OperatingMode.BACKTEST
            or profile.data_source != SourceKind.HISTORICAL
            or binding["format"] != BINDING_FORMAT
            or binding["purpose"] != "research_only"
            or binding["source"] != "historical"
            or binding["origin"] != payload["origin"]
            or binding["reconstruction"] != "exact_current_trainer"
            or binding["genuine_provenance_verified"] is not False
            or binding["promotion_eligible"] is not False
            or binding["model_sha256"] != digest
            or binding["corpus_sha256"] != payload["dataset_sha256"]
            or binding["code_hash"] != profile.code_hash
            or binding["code_hash"] != payload["code_hash"]
            or binding["policy_hash"] != settings.strategy_fingerprint()
            or binding["policy_hash"] != payload["policy_hash"]
            or binding["feature_schema_hash"] != payload["feature_schema_hash"]
            or binding["evaluation_sha256"] != sha256_json(payload["evaluation"])
            or payload["feature_origin_code_hash"] != profile.code_hash
            or payload["evaluation"]["passed"] is not True
            or type(binding["sample_count"]) is not int
            or binding["sample_count"] != payload["sample_count"]
            or not 1 <= binding["sample_count"] <= 5000
        ):
            raise ValueError
        for name in BINDING_KEYS:
            if name.endswith("_sha256") or name.endswith("_hash"):
                valid_key(binding[name])
        selection = ReplayModelSelection.model_validate(binding["selection"])
        if selection.artifact.sha256 != digest:
            raise ValueError
        start, end = utc_time(binding["replay_from"]), utc_time(binding["replay_until"])
        trained, created = utc_time(payload["trained_through"]), utc_time(payload["created_at"])
        exported, horizon = utc_time(binding["exported_at"]), utc_time(binding["label_horizon_through"])
        embargo = timedelta(
            minutes=TIMEFRAME_MINUTES[settings.primary_timeframe] * settings.model_embargo_bars
        )
        if not (
            trained
            <= exported
            <= created
            <= selection.available_at
            <= selection.selected_at
            <= start
            <= now
            <= end
            and start < end
            and trained < start - embargo
            and horizon < start
            and now - trained <= timedelta(days=settings.model_max_age_days)
        ):
            raise ValueError
        return selection
    except (ValueError, KeyError, TypeError, ArithmeticError, BrokerError):
        raise TradingDisabled("historical model replay selection/causality binding is invalid") from None


def validate_replay_registry_context(binding, database, settings):
    """A historical selection may be read ONLY in its captured private SQLite replay context.

    Re-read the bounded run identity and captured manifest on every registry read. This
    catches ordinary persisted-metadata/path revisions; it is not an anti-admin trust
    anchor or a race-free hostile-filesystem sandbox.
    """
    import hashlib
    from pathlib import Path

    from backtesting.contracts import LocalFile
    from backtesting.dataset import file_bytes, strict_json_bytes

    try:
        root = settings.project_root.absolute()
        if (
            database.settings is not settings
            or database.engine.url.get_backend_name() != "sqlite"
            or Path(database.engine.url.database).absolute() != root / "data" / "replay.db"
            or settings.data_dir != Path("data")
        ):
            raise ValueError
        run = strict_json_bytes(file_bytes(root / "run.json", root=root, limit=1048576))
        summary = run["replay_model"]
        if (
            run["format"] != "reflex-replay-run-v1"
            or run["status"] != "created"
            or run["model_sha256"] != binding["model_sha256"]
            or run["code_hash"] != binding["code_hash"]
            or run["strategy_config_hash"] != binding["policy_hash"]
            or run["safety_config_hash"] != settings.safety_fingerprint()
            or run["dataset_sha256"] != binding["dataset_sha256"]
            or summary["binding_sha256"] != sha256_json(binding)
            or summary["production_model_activated"] is not False
            or summary["promotion_eligible"] is not False
        ):
            raise ValueError
        declaration = LocalFile(path=run["input_manifest"], sha256=binding["manifest_sha256"])
        raw = file_bytes(root / "inputs" / declaration.path, root=root / "inputs", limit=1048576)
        if hashlib.sha256(raw).hexdigest() != declaration.sha256:
            raise ValueError
    except (ValueError, KeyError, TypeError, OSError, BrokerError):
        raise TradingDisabled(
            "historical model registry differs from its captured private replay context"
        ) from None
