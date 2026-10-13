"""Versioned immutable artifacts, serialized selection/rollback and existing native stage gates.

Selecting a model never resumes a runtime, alters risk capital, grants a broker write
or replaces the fresh live account/session/nonce confirmation required by StageGate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from ai.artifacts import artifact_path, read_model, save_immutable
from ai.feature_engineering import FEATURE_NAMES, FEATURE_SCHEMA_HASH, FeatureVector
from ai.json_validation import AIInvalidResponse, strict_json
from ai.local_operator_guard import require_local_operator, require_stopped_flat
from ai.model_trainer import TrainingResult
from ai.portable_model import PortableModel
from ai.replay_binding import validate_replay_binding, validate_replay_registry_context
from core.database import Database
from core.models import DeploymentEvidence, ModelVersion
from core.security import sha256_json
from core.settings import TIMEFRAME_MINUTES, Settings
from trading.risk_types import RuntimeProfile
from trading.stage_gate import StageGate
from trading.types import AccountInfo, BrokerError, Clock, SourceKind, TradingDisabled, aware_utc, valid_key

SCOPES = ("candidate", "backtest", "paper", "demo", "live")


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    model_id: int
    digest: str
    algorithm: str
    scope: str
    active: bool
    payload_json: str
    replay_binding_json: str | None = None

    def payload(self):
        return json.loads(self.payload_json)

    def predict(self, features: FeatureVector):
        return float(PortableModel(self.payload()["model"]).predict([features.values])[0])


class ModelRegistry:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile

    def _validate(self, payload: dict):
        try:
            keys = {
                "format",
                "created_at",
                "trained_through",
                "dataset_sha256",
                "feature_origin_code_hash",
                "source",
                "origin",
                "policy_hash",
                "code_hash",
                "feature_schema_hash",
                "feature_names",
                "sample_count",
                "model",
                "evaluation",
            }
            if (
                set(payload) != keys
                or payload["format"] != "reflex-model-v1"
                or payload["feature_schema_hash"] != FEATURE_SCHEMA_HASH
                or payload["feature_names"] != list(FEATURE_NAMES)
                or payload["source"] != self.profile.data_source.value
                or payload["policy_hash"] != self.settings.strategy_fingerprint()
                or payload["code_hash"] != self.profile.code_hash
                or payload["origin"] not in {"reconciled_trades", "fixture"}
                or payload["model"].get("algorithm") != self.settings.model_algorithm
                or type(payload["sample_count"]) is not int
                or not self.settings.model_min_labelled_trades
                <= payload["sample_count"]
                <= self.settings.model_max_dataset_rows
            ):
                raise ValueError
            for name in ("dataset_sha256", "feature_origin_code_hash", "code_hash", "policy_hash"):
                valid_key(payload[name])
            created = aware_utc(datetime.fromisoformat(payload["created_at"]))
            trained = aware_utc(datetime.fromisoformat(payload["trained_through"]))
            if not trained <= created <= self.clock.now() + timedelta(seconds=2):
                raise ValueError
            PortableModel(payload["model"])
            report = payload["evaluation"]
            if (
                set(report)
                != {
                    "format",
                    "dataset_sha256",
                    "source",
                    "selection",
                    "not_stage_evidence",
                    "threshold",
                    "folds",
                    "totals",
                    "passed",
                    "gate_reasons",
                }
                or report["format"] != "reflex-model-evaluation-v1"
                or report["dataset_sha256"] != payload["dataset_sha256"]
                or report["source"] != payload["source"]
                or report["selection"] != "executed_trades_only"
                or report["not_stage_evidence"] is not True
                or report["threshold"] != self.settings.model_min_probability
                or type(report["passed"]) is not bool
                or len(report["folds"]) != self.settings.model_walk_forward_folds
            ):
                raise ValueError
            test_count, selected, previous_end = 0, 0, None
            for i, fold in enumerate(report["folds"]):
                if (
                    set(fold)
                    != {
                        "number",
                        "train_count",
                        "test_count",
                        "purged",
                        "fit_cutoff",
                        "test_start",
                        "test_end",
                        "train_ids_hash",
                        "test_ids_hash",
                        "brier",
                        "auc",
                        "selected",
                    }
                    or any(
                        type(fold[k]) is not int
                        for k in ("number", "train_count", "test_count", "purged", "selected")
                    )
                    or fold["number"] != i
                    or fold["train_count"] < 20
                    or fold["test_count"] < 10
                    or fold["purged"] < 0
                    or not 0 <= fold["selected"] <= fold["test_count"]
                ):
                    raise ValueError
                valid_key(fold["train_ids_hash"])
                valid_key(fold["test_ids_hash"])
                start = aware_utc(datetime.fromisoformat(fold["test_start"]))
                end = aware_utc(datetime.fromisoformat(fold["test_end"]))
                cutoff = aware_utc(datetime.fromisoformat(fold["fit_cutoff"]))
                if not cutoff < start < end <= created or previous_end is not None and previous_end > start:
                    raise ValueError
                for metric in ("brier", "auc"):
                    value = fold[metric]
                    if value is None and metric == "auc":
                        continue
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or not 0 <= value <= 1
                    ):
                        raise ValueError
                expected_gap = timedelta(
                    minutes=TIMEFRAME_MINUTES[self.settings.primary_timeframe]
                    * self.settings.model_embargo_bars
                )
                if (
                    cutoff != start - expected_gap
                    or fold["train_count"] + fold["purged"] > payload["sample_count"]
                ):
                    raise ValueError
                previous_end = end
                test_count += fold["test_count"]
                selected += fold["selected"]
            if test_count > payload["sample_count"]:
                raise ValueError
            totals = report["totals"]
            if set(totals) != {
                "test_count",
                "auc",
                "brier",
                "baseline_brier",
                "brier_skill",
                "log_loss",
                "balanced_accuracy",
                "selected",
                "selected_net_r",
                "selected_profit_factor",
                "coverage",
            }:
                raise ValueError
            for value in totals.values():
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                ):
                    raise ValueError
            if (
                totals["test_count"] != test_count
                or totals["selected"] != selected
                or not 0 <= totals["brier"] <= 1
                or not 0 < totals["baseline_brier"] <= 1
                or abs(totals["brier_skill"] - (1 - totals["brier"] / totals["baseline_brier"])) > 1e-9
                or abs(totals["coverage"] - selected / test_count) > 1e-9
                or not 0 <= totals["balanced_accuracy"] <= 1
                or totals["log_loss"] < 0
                or totals["auc"] is not None
                and not 0 <= totals["auc"] <= 1
                or totals["selected_profit_factor"] is not None
                and totals["selected_profit_factor"] < 0
            ):
                raise ValueError
            reasons = []
            if totals["auc"] is None or totals["auc"] < self.settings.model_min_eval_auc:
                reasons.append("insufficient_auc")
            if totals["brier_skill"] < self.settings.model_min_brier_skill:
                reasons.append("insufficient_brier_skill")
            if selected < self.settings.model_min_selected_test_trades:
                reasons.append("insufficient_selected_samples")
            if totals["selected_profit_factor"] is None or totals["selected_profit_factor"] < float(
                self.settings.stage_min_profit_factor
            ):
                reasons.append("no_finite_qualifying_factor")
            if report["gate_reasons"] != reasons or report["passed"] != (not reasons):
                raise ValueError
        except (ValueError, KeyError, TypeError, ArithmeticError, BrokerError):
            raise TradingDisabled("model artifact/schema/evaluation/policy integrity is invalid") from None

    def _metadata(self, payload, digest):
        return {
            "format": "reflex-registry-v1",
            "model_sha256": digest,
            "header": {k: v for k, v in payload.items() if k not in {"model", "evaluation"}},
            "evaluation": payload["evaluation"],
            "evaluation_sha256": sha256_json(payload["evaluation"]),
        }

    def _registered(self, row):
        payload = read_model(self.settings, row.artifact_sha256)
        self._validate(payload)
        path = artifact_path(self.settings, row.artifact_sha256)
        metadata = self._metadata(payload, row.artifact_sha256)
        replay_binding = (
            row.metrics_json.get("replay_binding") if isinstance(row.metrics_json, dict) else None
        )
        if replay_binding is not None:
            validate_replay_binding(
                replay_binding, payload, row.artifact_sha256, self.settings, self.profile, self.clock.now()
            )
            validate_replay_registry_context(replay_binding, self.database, self.settings)
            metadata["replay_binding"] = replay_binding
        if row.active and self.profile.data_source == SourceKind.HISTORICAL and replay_binding is None:
            raise TradingDisabled("historical selected model requires a verified private replay binding")
        if (
            row.file_path != path.relative_to(self.settings.project_root.resolve()).as_posix()
            or row.metrics_json != metadata
            or row.model_type != "reflex-" + payload["model"]["algorithm"] + "-v1"
            or row.config_hash != self.settings.strategy_fingerprint()
            or row.deployment_scope not in SCOPES
        ):
            raise TradingDisabled("model database/artifact binding changed")
        return RegisteredModel(
            row.id,
            row.artifact_sha256,
            payload["model"]["algorithm"],
            row.deployment_scope,
            row.active,
            json.dumps(payload, separators=(",", ":")),
            json.dumps(replay_binding, separators=(",", ":")) if replay_binding is not None else None,
        )

    def register(self, result: TrainingResult) -> RegisteredModel:
        self.database.verify_schema()
        try:
            raw = result.artifact_json.encode()
            payload = strict_json(
                raw,
                max_bytes=self.settings.model_max_artifact_bytes,
                max_depth=16,
                max_nodes=20000,
                max_string=self.settings.model_max_artifact_bytes,
            )
            self._validate(payload)
        except (AIInvalidResponse, ValueError, AttributeError):
            raise TradingDisabled("invalid training result") from None
        path, digest = save_immutable(self.settings, raw)
        with self.database.locked_session() as session:
            row = session.scalar(select(ModelVersion).where(ModelVersion.artifact_sha256 == digest))
            if row is None:
                row = ModelVersion(
                    model_type="reflex-" + payload["model"]["algorithm"] + "-v1",
                    metrics_json=self._metadata(payload, digest),
                    file_path=path.relative_to(self.settings.project_root.resolve()).as_posix(),
                    artifact_sha256=digest,
                    active=False,
                    deployment_scope="candidate",
                    config_hash=self.settings.strategy_fingerprint(),
                    created_at=self.clock.now(),
                )
                session.add(row)
                session.flush()
                self.database.add_audit(
                    session,
                    "model.registered_candidate",
                    "learning",
                    {
                        "model_id": row.id,
                        "model_sha256": digest,
                        "source": payload["source"],
                        "evaluation_passed": payload["evaluation"]["passed"],
                        "not_stage_evidence": True,
                    },
                )
            return self._registered(row)

    def get(self, model_id: int):
        if type(model_id) is not int or model_id <= 0:
            raise TradingDisabled("positive registered model ID required")
        with self.database.session() as session:
            row = session.get(ModelVersion, model_id)
            if row is None:
                raise TradingDisabled("registered model missing")
            return self._registered(row)

    def _stage_settings(self, scope):
        values = self.settings.model_dump(mode="python")
        values.update(
            project_root=self.settings.project_root,
            backtest_mode=scope == "backtest",
            demo_mode=scope != "live",
            live_trading=scope == "live",
            paper_trading=scope == "paper",
            mt5_backend="mock" if scope == "backtest" else "real",
        )
        return Settings(_env_file=None, **values)

    def _native_evidence(self, session, model, scope, account):
        payload = model.payload()
        if (
            payload["source"] != "mt5"
            or payload["origin"] != "reconciled_trades"
            or payload["evaluation"]["passed"] is not True
            or not isinstance(account, AccountInfo)
            or account.source != SourceKind.MT5
            or self.clock.now() - datetime.fromisoformat(payload["trained_through"])
            > timedelta(days=self.settings.model_max_age_days)
        ):
            raise TradingDisabled(
                "real-source reviewed current model/account/evaluation required for promotion"
            )
        cfg = self._stage_settings(scope)
        profile = RuntimeProfile(self.profile.code_hash, model.digest, SourceKind.MT5)
        evidence = StageGate(self.database, cfg, self.clock, profile).required_evidence(session, account)
        for item in evidence:
            record = session.get(DeploymentEvidence, item)
            if record.started_at < datetime.fromisoformat(payload["trained_through"]):
                raise TradingDisabled(
                    "stage overlaps model training labels; independent forward evidence required"
                )
        return evidence

    def activate(
        self, model_id: int, *, scope: str, operator, account: AccountInfo | None = None, rollback=False
    ):
        operator_id = require_local_operator(operator)
        if type(model_id) is not int or model_id <= 0 or type(rollback) is not bool:
            raise TradingDisabled("positive model ID and explicit boolean rollback required")
        if scope not in SCOPES[1:]:
            raise TradingDisabled("invalid model activation scope")
        with self.database.locked_session() as session:
            state = require_stopped_flat(session)
            if (
                state.settings_overrides
                and state.settings_overrides.get("new_hash") != self.settings.safety_fingerprint()
            ):
                raise TradingDisabled("recompose with projected policy before model selection")
            row = session.get(ModelVersion, model_id)
            if row is None:
                raise TradingDisabled("registered model missing")
            model = self._registered(row)
            old = SCOPES.index(row.deployment_scope)
            new = SCOPES.index(scope)
            if (not rollback and new not in {old, old + 1}) or rollback and row.deployment_scope != scope:
                raise TradingDisabled("staged sequence/previously selected rollback scope required")
            evidence = () if scope == "backtest" else self._native_evidence(session, model, scope, account)
            current = session.scalars(select(ModelVersion).where(ModelVersion.active.is_(True))).all()
            if len(current) > 1:
                raise TradingDisabled("ambiguous active-model state")
            for item in current:
                item.active = False
            row.active, row.deployment_scope = True, scope
            state.revision += 1  # Never clear kill/loss/high-water/capital/session or resume.
            self.database.add_audit(
                session,
                "model.rollback_selected" if rollback else "model.selected_paused",
                "local_operator",
                {
                    "model_id": row.id,
                    "scope": scope,
                    "operator_id": operator_id,
                    "evidence_ids": list(evidence),
                    "previous_ids": [x.id for x in current],
                    "broker_permission": False,
                },
            )
            return self._registered(row)

    def rollback(self, model_id: int, *, scope: str, operator, account: AccountInfo | None = None):
        return self.activate(model_id, scope=scope, operator=operator, account=account, rollback=True)

    def active_for_runtime(self):
        with self.database.session() as session:
            rows = session.scalars(select(ModelVersion).where(ModelVersion.active.is_(True))).all()
            if len(rows) != 1:
                raise TradingDisabled("one explicit active model required")
            model = self._registered(rows[0])
            payload = model.payload()
            if model.scope != self.settings.mode.value or self.clock.now() - datetime.fromisoformat(
                payload["trained_through"]
            ) > timedelta(days=self.settings.model_max_age_days):
                raise TradingDisabled("active model scope/age differs from runtime")
            return model

    def runtime_profile(self):
        model = self.active_for_runtime()
        return RuntimeProfile(self.profile.code_hash, model.digest, self.profile.data_source)

    def inference(self, features: FeatureVector):
        model = self.active_for_runtime()
        if model.digest != self.profile.model_sha256:
            raise TradingDisabled("compose a stopped runtime with the selected model fingerprint first")
        return model.predict(features)

    def replay_inference(self, features: FeatureVector):
        """Predict using the SAME revalidated historical model/selection snapshot, not a probability DTO."""
        try:
            model = self.active_for_runtime()
            if (
                self.profile.data_source != SourceKind.HISTORICAL
                or model.replay_binding_json is None
                or model.digest != self.profile.model_sha256
            ):
                raise TradingDisabled("bound historical replay model required")
            return model.predict(features), sha256_json(json.loads(model.replay_binding_json))
        except Exception:
            # Library/filesystem/ledger failure is an entry veto, NEVER a fabricated score/fallback.
            raise TradingDisabled("historical model inference/binding unavailable") from None
