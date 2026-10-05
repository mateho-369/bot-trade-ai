"""Actual CPU training and chronological OOS evaluation. Never mutates active model/settings."""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from ai.dataset import LearningDataset
from ai.feature_engineering import FEATURE_NAMES, FEATURE_SCHEMA_HASH
from ai.portable_model import PortableModel
from ai.walk_forward import purged_walk_forward
from core.security import canonical_json, sha256_json
from core.settings import Settings
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, aware_utc


@dataclass(frozen=True, slots=True)
class TrainingResult:
    artifact_json: str

    @property
    def digest(self):
        return hashlib.sha256(self.artifact_json.encode()).hexdigest()

    def payload(self):
        return json.loads(self.artifact_json)


class ModelTrainer:
    def __init__(self, settings: Settings, profile: RuntimeProfile):
        self.settings, self.profile = settings, profile

    def fit(self, x, y) -> dict:
        if {int(v) for v in y} != {0, 1}:
            raise BrokerError("both classes required; never fit a guaranteed-win constant")
        if self.settings.model_algorithm == "logistic":
            scaler = StandardScaler().fit(x)  # FIT ONLY THE PURGED TRAIN WINDOW.
            estimator = LogisticRegression(
                C=0.5, max_iter=1000, tol=1e-8, random_state=self.settings.model_random_seed
            )
            with threadpool_limits(limits=1), warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                try:
                    estimator.fit(scaler.transform(x), y)
                except ConvergenceWarning:
                    raise BrokerError("model did not converge; candidate withheld") from None
            return {
                "algorithm": "logistic",
                "mean": scaler.mean_.tolist(),
                "scale": scaler.scale_.tolist(),
                "coefficients": estimator.coef_[0].tolist(),
                "intercept": float(estimator.intercept_[0]),
            }
        import lightgbm as lgb
        import pandas as pd

        estimator = lgb.LGBMClassifier(
            n_estimators=100,
            num_leaves=7,
            max_depth=3,
            learning_rate=0.03,
            min_child_samples=20,
            objective="binary",
            random_state=self.settings.model_random_seed,
            n_jobs=1,
            deterministic=True,
            force_col_wise=True,
            verbosity=-1,
        )
        estimator.fit(pd.DataFrame(x, columns=FEATURE_NAMES), y)
        return {
            "algorithm": "lightgbm",
            "model_text": estimator.booster_.model_to_string(),
            "library_version": lgb.__version__,
        }

    def train(self, dataset: LearningDataset, *, as_of: datetime) -> TrainingResult:
        cfg, profile = self.settings, self.profile
        now = aware_utc(as_of)
        if (
            dataset.exported_at > now
            or dataset.source != profile.data_source
            or dataset.policy_hash != cfg.strategy_fingerprint()
        ):
            raise BrokerError("training data is future or bound to another source/policy")
        folds = purged_walk_forward(dataset, cfg)
        x, y = dataset.matrices()
        reports, predictions, outcomes, baselines, selected_returns = [], [], [], [], []
        for fold in folds:
            train, test = list(fold.train), list(fold.test)
            payload = self.fit(x[train], y[train])
            model = PortableModel(payload)
            probability = model.predict(x[test])
            labels = y[test]
            baseline = np.full(len(test), float(y[train].mean()))
            chosen = probability >= cfg.model_min_probability  # fixed BEFORE testing, never OOS-tuned.
            returns = [dataset.samples[index].net_r for index, keep in zip(test, chosen, strict=True) if keep]
            selected_returns.extend(returns)
            predictions.extend(probability.tolist())
            outcomes.extend(labels.tolist())
            baselines.extend(baseline.tolist())
            reports.append(
                {
                    "number": fold.number,
                    "train_count": len(train),
                    "test_count": len(test),
                    "purged": fold.purged,
                    "fit_cutoff": fold.fit_cutoff.isoformat(),
                    "test_start": fold.test_start.isoformat(),
                    "test_end": fold.test_end.isoformat(),
                    "train_ids_hash": sha256_json([dataset.samples[i].sample_id for i in train]),
                    "test_ids_hash": sha256_json([dataset.samples[i].sample_id for i in test]),
                    "brier": float(brier_score_loss(labels, probability)),
                    "auc": float(roc_auc_score(labels, probability)) if len(set(labels)) == 2 else None,
                    "selected": int(chosen.sum()),
                }
            )
        outcomes = np.asarray(outcomes)
        predictions = np.asarray(predictions)
        baselines = np.asarray(baselines)
        brier = float(brier_score_loss(outcomes, predictions))
        baseline_brier = float(brier_score_loss(outcomes, baselines))
        skill = 1 - brier / baseline_brier if baseline_brier > 0 else None
        auc = float(roc_auc_score(outcomes, predictions)) if len(set(outcomes)) == 2 else None
        wins = sum(max(0, r) for r in selected_returns)
        losses = sum(max(0, -r) for r in selected_returns)
        factor = wins / losses if losses > 0 else None
        totals = {
            "test_count": len(outcomes),
            "auc": auc,
            "brier": brier,
            "baseline_brier": baseline_brier,
            "brier_skill": skill,
            "log_loss": float(log_loss(outcomes, predictions, labels=[0, 1])),
            "balanced_accuracy": float(balanced_accuracy_score(outcomes, predictions >= 0.5)),
            "selected": len(selected_returns),
            "selected_net_r": sum(selected_returns),
            "selected_profit_factor": factor,
            "coverage": len(selected_returns) / len(outcomes),
        }
        reasons = []
        if auc is None or auc < cfg.model_min_eval_auc:
            reasons.append("insufficient_auc")
        if skill is None or skill < cfg.model_min_brier_skill:
            reasons.append("insufficient_brier_skill")
        if len(selected_returns) < cfg.model_min_selected_test_trades:
            reasons.append("insufficient_selected_samples")
        if factor is None or factor < float(cfg.stage_min_profit_factor):
            reasons.append("no_finite_qualifying_factor")
        evaluation = {
            "format": "reflex-model-evaluation-v1",
            "dataset_sha256": dataset.digest,
            "source": dataset.source.value,
            "selection": "executed_trades_only",
            "not_stage_evidence": True,
            "threshold": cfg.model_min_probability,
            "folds": reports,
            "totals": totals,
            "passed": not reasons,
            "gate_reasons": reasons,
        }
        final = self.fit(
            x, y
        )  # after all OOS evaluation; cannot evaluate this final fit on its own training labels.
        result = {
            "format": "reflex-model-v1",
            "created_at": now.isoformat(),
            "trained_through": dataset.trained_through.isoformat(),
            "dataset_sha256": dataset.digest,
            "feature_origin_code_hash": dataset.feature_code_hash,
            "source": dataset.source.value,
            "origin": dataset.origin,
            "policy_hash": dataset.policy_hash,
            "code_hash": profile.code_hash,
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
            "feature_names": list(FEATURE_NAMES),
            "sample_count": len(dataset.samples),
            "model": final,
            "evaluation": evaluation,
        }
        raw = canonical_json(result)
        if len(raw.encode()) > cfg.model_max_artifact_bytes:
            raise BrokerError("trained artifact exceeds reviewed size bound")
        return TrainingResult(raw)
