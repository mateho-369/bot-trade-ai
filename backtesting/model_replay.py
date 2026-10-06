"""One frozen research model/corpus; never read or activate the production registry.

Verify BEFORE run-directory/ledger creation, recomputing the exact existing CPU trainer
and purged OOS evaluation from past-only immutable captured bytes. Import only into a
new private BACKTEST ledger. No midrun fitting, selection, automatic fallback or owner
approval claim. Hashes/declared times/reconstruction cannot establish authentic provenance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from ai.dataset import LearningDataset
from ai.json_validation import strict_json
from ai.local_operator_guard import require_stopped_flat
from ai.model_registry import ModelRegistry
from ai.model_trainer import ModelTrainer, TrainingResult
from ai.replay_binding import BINDING_FORMAT, validate_replay_binding
from ai.walk_forward import purged_walk_forward
from backtesting.contracts import ReplayModelSelection, utc_time
from backtesting.dataset import DatasetError, file_bytes, strict_json_bytes
from core.models import DeploymentEvidence, ModelVersion, OrderIntent, RiskState, Signal, Trade
from core.security import canonical_json, sha256_json
from core.settings import TIMEFRAME_MINUTES, OperatingMode
from trading.types import ManualClock, SourceKind, TradingDisabled

MAX_REPLAY_TRAINING_ROWS = 5000
MAX_REPLAY_CORPUS_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VerifiedReplayModel:
    selection: ReplayModelSelection
    raw_artifact: bytes
    binding_json: str

    @property
    def binding(self):
        return json.loads(self.binding_json)  # Detached copy, never a mutable verification result.

    @property
    def digest(self):
        return hashlib.sha256(self.raw_artifact).hexdigest()

    @property
    def binding_digest(self):
        return sha256_json(self.binding)

    @classmethod
    def verify(cls, history, settings, profile):
        selection = history.manifest.model
        if selection is None or not settings.model_filter_enabled:
            raise TradingDisabled("explicit enabled model policy and frozen replay selection are required")
        if settings.mode != OperatingMode.BACKTEST or profile.data_source != SourceKind.HISTORICAL:
            raise TradingDisabled("research model input is historical BACKTEST-only")
        start = history.manifest.replay_from
        try:
            raw = history.files[selection.artifact.path]
            corpus_raw = history.files[selection.learning_dataset.path]
            digest = hashlib.sha256(raw).hexdigest()
            if (
                not 0 < len(corpus_raw) <= MAX_REPLAY_CORPUS_BYTES
                or not 0 < len(raw) <= settings.model_max_artifact_bytes
                or digest != selection.artifact.sha256
                or hashlib.sha256(corpus_raw).hexdigest() != selection.learning_dataset.sha256
            ):
                raise ValueError
            payload = strict_json(
                raw,
                max_bytes=settings.model_max_artifact_bytes,
                max_depth=16,
                max_nodes=20000,
                max_string=settings.model_max_artifact_bytes,
            )
            # Bound JSON arrays/nodes BEFORE constructing any sample or allocating a training matrix.
            corpus = LearningDataset.from_dict(
                strict_json(
                    corpus_raw,
                    max_bytes=MAX_REPLAY_CORPUS_BYTES,
                    max_depth=12,
                    max_nodes=MAX_REPLAY_TRAINING_ROWS * 96,
                    max_string=1000,
                    max_array=MAX_REPLAY_TRAINING_ROWS,
                ),
                settings,
            )
            if (
                len(corpus.samples) > MAX_REPLAY_TRAINING_ROWS
                or len(corpus.samples) < settings.model_min_labelled_trades
                or corpus.source != SourceKind.HISTORICAL
                or corpus.policy_hash != settings.strategy_fingerprint()
                or corpus.feature_code_hash != profile.code_hash
                or corpus.origin == "fixture"
                and history.manifest.origin.kind != "synthetic_fixture"
                or payload["origin"] != corpus.origin
                or payload["sample_count"] != len(corpus.samples)
                or payload["dataset_sha256"] != corpus.digest
                or payload["feature_origin_code_hash"] != corpus.feature_code_hash
                or utc_time(payload["trained_through"]) != corpus.trained_through
            ):
                raise ValueError
            created = utc_time(payload["created_at"])
            horizon = timedelta(
                minutes=TIMEFRAME_MINUTES[settings.primary_timeframe] * settings.model_label_horizon_bars
            )
            horizon_through = max(max(row.exit_at, row.decision_at + horizon) for row in corpus.samples)
            binding = {
                "format": BINDING_FORMAT,
                "purpose": "research_only",
                "source": "historical",
                "origin": corpus.origin,
                "selection": selection.model_dump(mode="json"),
                "manifest_sha256": history.manifest_sha256,
                "dataset_sha256": history.dataset_sha256,
                "corpus_sha256": corpus.digest,
                "account_scope_sha256": corpus.account_scope_hash,
                "model_sha256": digest,
                "code_hash": profile.code_hash,
                "policy_hash": settings.strategy_fingerprint(),
                "feature_schema_hash": payload["feature_schema_hash"],
                "evaluation_sha256": sha256_json(payload["evaluation"]),
                "sample_count": len(corpus.samples),
                "exported_at": corpus.exported_at.isoformat(),
                "label_horizon_through": horizon_through.isoformat(),
                "replay_from": start.isoformat(),
                "replay_until": history.manifest.replay_until.isoformat(),
                "reconstruction": "exact_current_trainer",
                "genuine_provenance_verified": False,
                "promotion_eligible": False,
            }
            # Schema/policy/evaluation validation precedes fitting. Never re-label a supplied artifact.
            ModelRegistry(None, settings, ManualClock(start), profile)._validate(payload)
            folds = purged_walk_forward(corpus, settings)
            binding["fold_sha256"] = sha256_json(
                [
                    {
                        "number": fold.number,
                        "train": fold.train,
                        "test": fold.test,
                        "cutoff": fold.fit_cutoff,
                        "purged": fold.purged,
                    }
                    for fold in folds
                ]
            )
            validate_replay_binding(binding, payload, digest, settings, profile, start)
            reconstructed = ModelTrainer(settings, profile).train(corpus, as_of=created)
            if reconstructed.digest != digest or reconstructed.payload() != payload:
                raise ValueError
            return cls(selection, raw, canonical_json(binding))
        except Exception:
            # Suppress untrusted content/library exception bodies. No fallback or partial success.
            raise DatasetError(
                "replay model/corpus/evaluation/causality/reconstruction does not bind"
            ) from None

    def install_private(self, database, settings, clock, profile):
        """Import one verified snapshot into ONLY a new replay.db. Never call owner activate()."""
        binding = self.binding
        if (
            database.settings is not settings
            or profile.model_sha256 != self.digest
            or clock.now() != utc_time(binding["replay_from"])
        ):
            raise TradingDisabled("verified model/private runtime differs")
        validate_replay_binding(
            binding, json.loads(self.raw_artifact), self.digest, settings, profile, clock.now()
        )
        root = settings.project_root.absolute()
        expected = root / "data" / "replay.db"
        if (
            database.engine.url.get_backend_name() != "sqlite"
            or Path(database.engine.url.database).absolute() != expected
            or settings.data_dir != Path("data")
            or settings.live_trading
            or not settings.start_paused
            or any((root / name).exists() for name in ("completion.json", "failure.json"))
        ):
            raise TradingDisabled("model import is restricted to a new isolated replay ledger")
        run = strict_json_bytes(file_bytes(root / "run.json", root=root, limit=1048576))
        if (
            run.get("format") != "reflex-replay-run-v1"
            or run.get("status") != "created"
            or run.get("model_sha256") != self.digest
            or run.get("code_hash") != profile.code_hash
            or run.get("strategy_config_hash") != settings.strategy_fingerprint()
            or run.get("safety_config_hash") != settings.safety_fingerprint()
            or run.get("dataset_sha256") != binding["dataset_sha256"]
            or run.get("replay_model") != self.summary()
        ):
            raise TradingDisabled("new private run identity/model binding differs")
        # Captured files must still match. Do not import into existing financial/model state.
        for local in (self.selection.artifact, self.selection.learning_dataset):
            raw = file_bytes(root / "inputs" / local.path, root=root / "inputs")
            if hashlib.sha256(raw).hexdigest() != local.sha256:
                raise TradingDisabled("private captured model/corpus differs")
        with database.locked_session() as session:
            state = require_stopped_flat(session)
            if (
                state.desired_state != "paused"
                or state.revision != 0
                or state.kill_switch_active
                or state.settings_overrides
            ):
                raise TradingDisabled("new untouched paused replay state required")
            if any(
                session.scalar(select(table.id).limit(1)) is not None
                for table in (ModelVersion, Signal, OrderIntent, Trade, RiskState, DeploymentEvidence)
            ):
                raise TradingDisabled("only a new empty private replay ledger can import a model snapshot")
        registry = ModelRegistry(database, settings, clock, profile)
        registered = registry.register(TrainingResult(self.raw_artifact.decode("utf-8")))
        with database.locked_session() as session:
            state = require_stopped_flat(session)
            row = session.get(ModelVersion, registered.model_id)
            if (
                state.revision != 0
                or state.settings_overrides
                or state.kill_switch_active
                or session.scalars(select(ModelVersion.id)).all() != [registered.model_id]
                or any(
                    session.scalar(select(table.id).limit(1)) is not None
                    for table in (Signal, OrderIntent, Trade, RiskState, DeploymentEvidence)
                )
            ):
                raise TradingDisabled("private ledger no longer empty/untouched")
            row.metrics_json = {**row.metrics_json, "replay_binding": binding}
            row.active, row.deployment_scope = True, "backtest"
            state.revision += 1
            database.add_audit(
                session,
                "replay.model_snapshot_imported",
                "offline_replay",
                {
                    "model_sha256": self.digest,
                    "binding_sha256": self.binding_digest,
                    "selected_at": self.selection.selected_at,
                    "research_only": True,
                    "production_model_activated": False,
                    "genuine_owner_authenticated": False,
                    "eligible_stage_evidence": False,
                },
            )
            registry._registered(row)
        if registry.runtime_profile() != profile:
            raise TradingDisabled("private frozen model differs")

    def summary(self):
        binding = self.binding
        return {
            "format": "reflex-replay-model-observation-v1",
            "model_sha256": self.digest,
            "binding_sha256": self.binding_digest,
            "training_dataset_sha256": binding["corpus_sha256"],
            "trained_through": json.loads(self.raw_artifact)["trained_through"],
            "available_at": self.selection.available_at.isoformat(),
            "selected_at": self.selection.selected_at.isoformat(),
            "fold_hash": binding["fold_sha256"],
            "reconstructed_from_frozen_past_corpus": True,
            "fixture_labels": binding["origin"] == "fixture",
            "midrun_selection": False,
            "production_model_activated": False,
            "genuine_model_provenance_verified": False,
            "promotion_eligible": False,
        }
