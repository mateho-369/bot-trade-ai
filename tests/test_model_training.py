from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from ai.model_trainer import ModelTrainer
from ai.portable_model import PortableModel
from ai.walk_forward import purged_walk_forward
from tests.ai_helpers import fixture_dataset
from tests.risk_helpers import MOMENT, config
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, SourceKind


@pytest.mark.parametrize("algorithm", ["logistic", "lightgbm"])
def test_actual_training_portable_inference_and_oos_report(tmp_path, algorithm):
    cfg = config(tmp_path, model_algorithm=algorithm)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    dataset = fixture_dataset(cfg, profile=profile)
    trainer = ModelTrainer(cfg, profile)
    result = trainer.train(dataset, as_of=MOMENT)
    payload = result.payload()
    assert (
        payload["source"] == "synthetic"
        and payload["origin"] == "fixture"
        and payload["model"]["algorithm"] == algorithm
    )
    assert payload["evaluation"]["not_stage_evidence"] and len(payload["evaluation"]["folds"]) == 5
    assert payload["evaluation"]["totals"]["auc"] > 0.6
    x, y = dataset.matrices()
    prediction = PortableModel(payload["model"]).predict(x[:5])
    assert prediction.shape == (5,) and ((0 <= prediction) & (prediction <= 1)).all()
    assert result.digest and payload["dataset_sha256"] == dataset.digest
    if algorithm == "logistic":
        assert payload["model"]["mean"] == pytest.approx(x.mean(axis=0))
    else:
        assert "pickle" not in payload["model"] and "Tree=" in payload["model"]["model_text"]


def test_logistic_portable_numbers_equal_sklearn_and_scaling_is_train_only(tmp_path):
    cfg = config(tmp_path)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    dataset = fixture_dataset(cfg)
    x, y = dataset.matrices()
    train = x[:150]
    target = y[:150]
    exported = ModelTrainer(cfg, profile).fit(train, target)
    scaler = StandardScaler().fit(train)
    estimator = LogisticRegression(C=0.5, max_iter=1000, tol=1e-8, random_state=42).fit(
        scaler.transform(train), target
    )
    test = x[150:]
    test[:, 7] = 1000
    assert exported["mean"] == pytest.approx(train.mean(axis=0))
    assert PortableModel(exported).predict(test) == pytest.approx(
        estimator.predict_proba(scaler.transform(test))[:, 1], rel=1e-10, abs=1e-12
    )


def test_event_purging_embargo_and_simultaneous_bucket_never_leak(tmp_path):
    cfg = config(tmp_path)
    dataset = fixture_dataset(cfg)
    rows = list(dataset.samples)
    rows[30] = replace(rows[30], exit_at=rows[200].decision_at, label_available_at=rows[220].decision_at)
    rows[31] = replace(rows[31], decision_at=rows[30].decision_at)
    rows.sort(key=lambda s: (s.decision_at, s.sample_id))
    dataset = replace(dataset, samples=tuple(rows))
    folds = purged_walk_forward(dataset, cfg)
    for fold in folds:
        assert not set(fold.train) & set(fold.test) and fold.purged > 0
        assert all(dataset.samples[i].label_available_at < fold.fit_cutoff for i in fold.train)
        assert all(dataset.samples[i].decision_at < fold.fit_cutoff for i in fold.train)
        assert all(dataset.samples[i].exit_at < fold.test_start for i in fold.train)
        assert not {dataset.samples[i].decision_at for i in fold.train} & {
            dataset.samples[i].decision_at for i in fold.test
        }
    assert (
        rows.index(next(s for s in rows if s.sample_id == fixture_dataset(cfg).samples[30].sample_id))
        not in folds[0].train
    )


@pytest.mark.parametrize("fault", ["too_few", "all_wins", "future", "policy", "source"])
def test_insufficient_or_noncausal_training_is_not_repaired(tmp_path, fault):
    cfg = config(tmp_path)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    dataset = fixture_dataset(cfg)
    if fault == "too_few":
        dataset = replace(dataset, samples=dataset.samples[:100])
    if fault == "all_wins":
        dataset = replace(dataset, samples=tuple(replace(s, net_usd=Decimal("5")) for s in dataset.samples))
    if fault == "future":
        dataset = replace(dataset, exported_at=MOMENT + timedelta(seconds=1))
    if fault == "policy":
        dataset = replace(dataset, policy_hash="f" * 64)
    if fault == "source":
        profile = RuntimeProfile(profile.code_hash, profile.model_sha256, SourceKind.MT5)
    with pytest.raises(BrokerError):
        ModelTrainer(cfg, profile).train(dataset, as_of=MOMENT)


@pytest.mark.parametrize(
    "payload",
    [
        {"algorithm": "pickle", "path": "evil.pkl"},
        {"algorithm": "logistic", "mean": [], "scale": [], "coefficients": [], "intercept": 0},
        {"algorithm": "lightgbm", "library_version": "unknown", "model_text": "exec()"},
    ],
)
def test_untrusted_portable_model_formats_rejected(payload):
    with pytest.raises(BrokerError):
        PortableModel(payload)
