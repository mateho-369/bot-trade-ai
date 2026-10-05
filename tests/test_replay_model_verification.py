"""Hostile declarations are rehashed; actual purged model reconstruction must still bind."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType

import pytest

from ai.dataset import LearningDataset
from ai.model_trainer import ModelTrainer
from ai.walk_forward import purged_walk_forward
from backtesting.dataset import DatasetError, HistoricalDataset
from backtesting.model_replay import VerifiedReplayModel
from core.security import sha256_json
from core.settings import TIMEFRAME_MINUTES
from scripts.make_backtest_model_fixture import make_model_fixture
from tests.replay_model_helpers import (
    changed_input,
    configured,
    verified,
)
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind, TradingDisabled


def check(history, tmp_path, **settings):
    cfg = configured(history, tmp_path / "NEVER_CREATED", **settings)
    return VerifiedReplayModel.verify(history, cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL))


def test_exact_real_purged_cpu_artifact_and_binding(small_model_input, tmp_path):
    model, cfg, profile = verified(small_model_input, tmp_path / "nothing")
    corpus = LearningDataset.from_json(small_model_input.files[model.selection.learning_dataset.path], cfg)
    result = ModelTrainer(cfg, profile).train(
        corpus, as_of=datetime.fromisoformat(json.loads(model.raw_artifact)["created_at"])
    )
    assert result.digest == model.digest
    assert not (tmp_path / "nothing").exists()
    binding = model.binding
    assert binding["corpus_sha256"] == corpus.digest and binding["reconstruction"] == "exact_current_trainer"
    assert not binding["promotion_eligible"] and not binding["genuine_provenance_verified"]
    assert model.summary()["fixture_labels"]
    binding["code_hash"] = "0" * 64
    assert model.binding["code_hash"] != "0" * 64
    embargo = timedelta(minutes=TIMEFRAME_MINUTES[cfg.primary_timeframe] * cfg.model_embargo_bars)
    for fold in purged_walk_forward(corpus, cfg):
        assert fold.fit_cutoff == fold.test_start - embargo
        assert all(corpus.samples[i].label_available_at < fold.fit_cutoff for i in fold.train)
        assert set(fold.train).isdisjoint(fold.test)
        assert all(
            corpus.samples[i].decision_at < corpus.samples[j].decision_at
            for i in fold.train
            for j in fold.test
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "mt5"),
        ("source", "synthetic"),
        ("origin", "unattested"),
        ("code_hash", "d" * 64),
        ("feature_origin_code_hash", "e" * 64),
        ("policy_hash", "a" * 64),
        ("dataset_sha256", "b" * 64),
        ("feature_schema_hash", "c" * 64),
        ("sample_count", True),
        ("sample_count", 479),
        ("created_at", "2030-01-01T00:00:00+00:00"),
        ("trained_through", "2020-01-01T00:00:00+00:00"),
        ("format", "pickle-v1"),
        ("unsafe_weights", "model.joblib"),
    ],
)
def test_rehashed_artifact_header_still_rejected(small_model_input, tmp_path, field, value):
    changed = changed_input(small_model_input, artifact=lambda body: body.update({field: value}))
    with pytest.raises(DatasetError):
        check(changed, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "mt5"),
        ("source", "synthetic"),
        ("origin", "claimed_real"),
        ("feature_code_hash", "d" * 64),
        ("policy_hash", "c" * 64),
        ("feature_schema_hash", "b" * 64),
        ("costs_included", False),
        ("selection", "all_opportunities"),
        ("currency", "EUR"),
        ("account_scope_hash", "wrong"),
    ],
)
def test_rehashed_corpus_policy_schema_source_are_not_declarations_of_validity(
    small_model_input, tmp_path, field, value
):
    changed = changed_input(small_model_input, corpus=lambda body: body.update({field: value}))
    with pytest.raises(DatasetError):
        check(changed, tmp_path)


@pytest.mark.parametrize("target", ["weights", "fold", "metrics", "labels", "feature_values", "proof"])
def test_self_consistent_rehashed_but_not_original_trainer_output_rejected(
    small_model_input, tmp_path, target
):
    def artifact(body):
        if target == "weights":
            body["model"]["intercept"] += 0.25
        elif target == "fold":
            body["evaluation"]["folds"][0]["train_ids_hash"] = "b" * 64
        elif target == "metrics":
            body["evaluation"]["totals"]["log_loss"] += 0.01

    def corpus(body):
        if target == "labels":
            body["samples"][0]["net_usd"] = "-5" if body["samples"][0]["net_usd"] == "6" else "6"
        elif target == "feature_values":
            body["samples"][0]["features"]["technical_score"] += 0.01
        elif target == "proof":
            body["samples"][0]["proof_hash"] = "c" * 64

    attacked = changed_input(small_model_input, artifact=artifact, corpus=corpus)
    if target in {"labels", "feature_values", "proof"}:
        # Make the headers appear to agree with the new corpus. Refit/evaluation must still differ.
        digest = sha256_json(json.loads(attacked.files[attacked.manifest.model.learning_dataset.path]))
        attacked = changed_input(
            attacked,
            artifact=lambda body: (
                body.update(dataset_sha256=digest),
                body["evaluation"].update(dataset_sha256=digest),
            ),
        )
        if target == "proof":
            # A proof-hash change alone has no numeric effect; if every bound digest is recomputed,
            # the output IS reproducible. That demonstrates consistency != authentic provenance.
            assert check(attacked, tmp_path).binding["genuine_provenance_verified"] is False
            return
    with pytest.raises(DatasetError):
        check(attacked, tmp_path)


@pytest.mark.parametrize(
    "target",
    [
        "export_future",
        "available_before_created",
        "label_at_replay",
        "label_at_embargo",
        "horizon_at_replay",
        "creation_after_selection",
    ],
)
def test_pre_replay_label_selection_and_embargo_bounds(small_model_input, tmp_path, target):
    start = small_model_input.manifest.replay_from
    cfg = configured(small_model_input, tmp_path / "none")
    embargo = timedelta(minutes=TIMEFRAME_MINUTES[cfg.primary_timeframe] * cfg.model_embargo_bars)

    def corpus(body):
        if target == "export_future":
            body["exported_at"] = (start + timedelta(seconds=1)).isoformat()
        elif target in {"label_at_replay", "label_at_embargo"}:
            body["samples"][-1]["label_available_at"] = (
                start if target == "label_at_replay" else start - embargo
            ).isoformat()
            body["exported_at"] = body["samples"][-1]["label_available_at"]
        elif target == "horizon_at_replay":
            row = body["samples"][-1]
            row["decision_at"] = (start - embargo).isoformat()
            row["entry_at"] = (start - embargo + timedelta(seconds=1)).isoformat()
            row["exit_at"] = (start - embargo + timedelta(minutes=5)).isoformat()
            row["label_available_at"] = (start - embargo + timedelta(minutes=6)).isoformat()
            body["exported_at"] = row["label_available_at"]

    def artifact(body):
        if target == "creation_after_selection":
            body["created_at"] = (
                small_model_input.manifest.model.selected_at + timedelta(seconds=1)
            ).isoformat()

    def manifest(body):
        if target == "available_before_created":
            body["model"]["available_at"] = (start - timedelta(days=2)).isoformat()

    attacked = changed_input(small_model_input, corpus=corpus, artifact=artifact, manifest=manifest)
    with pytest.raises(DatasetError):
        check(attacked, tmp_path)


@pytest.mark.parametrize("kind", ["duplicate", "nan", "non_dict", "deep", "unknown_key", "empty", "pickle"])
@pytest.mark.parametrize("role", ["artifact_raw", "corpus_raw"])
def test_raw_hostile_model_and_learning_json_refused(small_model_input, tmp_path, kind, role):
    raw = {
        "duplicate": b'{"format":1,"format":2}',
        "nan": b'{"x":NaN}',
        "non_dict": b"[]",
        "deep": b'{"x":' + b"[" * 40 + b"0" + b"]" * 40 + b"}",
        "unknown_key": b'{"pickle":"x"}',
        "empty": b"",
        "pickle": b"\x80\x04python_pickle",
    }[kind]
    attacked = changed_input(small_model_input, **{role: raw})
    with pytest.raises(DatasetError):
        check(attacked, tmp_path)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("model_filter_enabled", False),
        ("model_algorithm", "lightgbm"),
        ("model_min_probability", 0.75),
        ("model_random_seed", 7),
        ("model_embargo_bars", 13),
        ("model_min_labelled_trades", 500),
    ],
)
def test_bound_model_policy_cannot_silently_change(small_model_input, tmp_path, setting, value):
    cfg = configured(small_model_input, tmp_path / "none")
    cfg = cfg.model_copy(
        update={setting: value}
    )  # Controlled corruption/changed policy, never user configuration.
    with pytest.raises((DatasetError, TradingDisabled)):
        VerifiedReplayModel.verify(small_model_input, cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL))


def test_verified_capture_independent_of_later_input_file_edits(small_model_input, tmp_path):
    # Real source bytes copied then deliberately modified AFTER load. Captured source remains immutable.
    root = tmp_path / "captured"
    root.mkdir()
    for name, raw in small_model_input.files.items():
        (root / name).write_bytes(raw)
    captured = HistoricalDataset.load(root / next(iter(small_model_input.files)))
    model = check(captured, tmp_path)
    (root / captured.manifest.model.artifact.path).write_text("not model JSON")
    assert check(captured, tmp_path).digest == model.digest
    with pytest.raises(DatasetError):
        HistoricalDataset.load(root / next(iter(captured.files)))


@pytest.mark.parametrize("role", ["artifact", "learning_dataset"])
def test_capture_hash_cannot_be_skipped_by_constructed_internal_dto(small_model_input, tmp_path, role):
    files = dict(small_model_input.files)
    path = getattr(small_model_input.manifest.model, role).path
    files[path] += b"\n"
    malicious = replace(small_model_input, files=MappingProxyType(files))
    with pytest.raises(DatasetError):
        check(malicious, tmp_path)


def test_fixture_labels_cannot_hide_behind_historical_import_origin(small_model_input, tmp_path):
    changed = changed_input(
        small_model_input, manifest=lambda body: body["origin"].update(kind="historical_import")
    )
    with pytest.raises(DatasetError):
        check(changed, tmp_path)


@pytest.mark.parametrize("algorithm", ["logistic", "lightgbm"])
def test_real_supported_algorithms_reconstruct_without_fallback(tmp_path, algorithm):
    path = make_model_fixture(tmp_path / "input", warmup_minutes=60, algorithm=algorithm)
    history = HistoricalDataset.load(path)
    model = check(history, tmp_path, model_algorithm=algorithm)
    assert json.loads(model.raw_artifact)["model"]["algorithm"] == algorithm
    assert hashlib.sha256(model.raw_artifact).hexdigest() == history.manifest.model.artifact.sha256
