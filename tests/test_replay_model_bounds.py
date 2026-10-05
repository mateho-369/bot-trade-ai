"""Resource/path/CPU/frozen-time limits over artificial captured input only."""

import json
from datetime import timedelta

import pytest

from ai.model_trainer import ModelTrainer
from backtesting.dataset import DatasetError, HistoricalDataset
from backtesting.model_replay import MAX_REPLAY_CORPUS_BYTES, MAX_REPLAY_TRAINING_ROWS, VerifiedReplayModel
from core.security import canonical_json
from tests.replay_model_helpers import changed_input, configured, prepare_private, verified
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.mark.parametrize("fault", ["corpus_bytes", "artifact_bytes", "rows", "feature_array"])
def test_bounded_reconstruction_rejects_before_fitting(small_model_input, tmp_path, monkeypatch, fault):
    cfg = configured(small_model_input, tmp_path / "nothing")
    if fault == "corpus_bytes":
        changed = changed_input(small_model_input, corpus_raw=b" " * (MAX_REPLAY_CORPUS_BYTES + 1))
    elif fault == "artifact_bytes":
        changed = changed_input(small_model_input, artifact_raw=b" " * (cfg.model_max_artifact_bytes + 1))
    elif fault == "rows":

        def many(body):
            body["samples"] = [body["samples"][0]] * (MAX_REPLAY_TRAINING_ROWS + 1)

        changed = changed_input(small_model_input, corpus=many)
    else:

        def array(body):
            body["samples"][0]["features"]["technical_score"] = [0] * (MAX_REPLAY_TRAINING_ROWS + 1)

        changed = changed_input(small_model_input, corpus=array)
    monkeypatch.setattr(ModelTrainer, "fit", lambda *args: pytest.fail("reject before CPU training"))
    with pytest.raises(DatasetError):
        VerifiedReplayModel.verify(changed, cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL))
    assert not (tmp_path / "nothing").exists()


@pytest.mark.parametrize("role", ["artifact", "learning_dataset"])
def test_model_or_corpus_symlink_never_followed(small_model_input, tmp_path, role):
    root = tmp_path / "new"
    root.mkdir()
    for name, raw in small_model_input.files.items():
        (root / name).write_bytes(raw)
    item = getattr(small_model_input.manifest.model, role)
    path = root / item.path
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(root / next(iter(small_model_input.files)))
    assert outside.read_bytes() == small_model_input.files[item.path]


@pytest.mark.parametrize("mode", [SourceKind.SYNTHETIC, SourceKind.MT5])
def test_model_verification_never_relabels_historical_scope(small_model_input, tmp_path, mode):
    cfg = configured(small_model_input, tmp_path)
    with pytest.raises(TradingDisabled):
        VerifiedReplayModel.verify(small_model_input, cfg, RuntimeProfile.current(cfg, mode))


def test_cpu_exception_is_safe_refusal_not_fallback_or_raw_log(small_model_input, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("UNTRUSTED_CORPUS_SECRET_NOT_IN_ERROR")

    monkeypatch.setattr(ModelTrainer, "train", fail)
    cfg = configured(small_model_input, tmp_path)
    with pytest.raises(DatasetError) as exc:
        VerifiedReplayModel.verify(small_model_input, cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL))
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("clock_delta", [timedelta(microseconds=1), timedelta(days=1)])
def test_midrun_import_refused_even_on_an_empty_private_ledger(small_model_input, tmp_path, clock_delta):
    root = tmp_path / "new"
    model, cfg, profile = verified(small_model_input, root)
    db = prepare_private(root, small_model_input, model, cfg, profile)
    try:
        clock = ManualClock(small_model_input.manifest.replay_from)
        clock.advance(clock_delta)
        with pytest.raises(TradingDisabled):
            model.install_private(db, cfg, clock, profile)
        assert db.status()["revision"] == 0
    finally:
        db.close()


def test_non_replay_db_path_cannot_receive_snapshot(small_model_input, tmp_path):
    from core.database import Database

    root = tmp_path / "private"
    model, cfg, profile = verified(small_model_input, root)
    # This is a new disposable sentinel DB, not the actual project financial ledger.
    cfg = cfg.model_copy(update={"database_url": cfg.database_url.__class__("sqlite:///data/original.db")})
    db = Database(cfg)
    db.initialize()
    before = db.status()
    try:
        with pytest.raises(TradingDisabled):
            model.install_private(db, cfg, ManualClock(small_model_input.manifest.replay_from), profile)
        assert db.status() == before
        assert not (root / "data/models").exists()
    finally:
        db.close()


def test_causal_embargo_rejection_of_fully_reconstructed_boundary_model(small_model_input, tmp_path):
    from ai.dataset import LearningDataset
    from core.settings import TIMEFRAME_MINUTES

    cfg = configured(small_model_input, tmp_path / "nothing")
    selection = small_model_input.manifest.model
    corpus = json.loads(small_model_input.files[selection.learning_dataset.path])
    embargo = timedelta(minutes=TIMEFRAME_MINUTES[cfg.primary_timeframe] * cfg.model_embargo_bars)
    boundary = small_model_input.manifest.replay_from - embargo
    corpus["samples"][-1]["label_available_at"] = boundary.isoformat()
    corpus["exported_at"] = (boundary + timedelta(minutes=1)).isoformat()
    dataset = LearningDataset.from_dict(corpus, cfg)
    created = boundary + timedelta(minutes=2)
    trained = ModelTrainer(cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL)).train(
        dataset, as_of=created
    )
    assert trained.payload()["evaluation"]["passed"]  # This is a REAL structurally consistent artifact.

    def times(body):
        body["model"]["available_at"] = (created + timedelta(minutes=1)).isoformat()
        body["model"]["selected_at"] = (created + timedelta(minutes=2)).isoformat()

    changed = changed_input(
        small_model_input,
        corpus_raw=canonical_json(corpus).encode(),
        artifact_raw=trained.artifact_json.encode(),
        manifest=times,
    )
    with pytest.raises(DatasetError):
        VerifiedReplayModel.verify(changed, cfg, RuntimeProfile.current(cfg, SourceKind.HISTORICAL))


def test_after_replay_end_registry_inference_scope_expires(small_model_input, tmp_path):
    from ai.dataset import LearningDataset
    from ai.model_registry import ModelRegistry

    root = tmp_path / "new"
    model, cfg, profile = verified(small_model_input, root)
    db = prepare_private(root, small_model_input, model, cfg, profile)
    clock = ManualClock(small_model_input.manifest.replay_from)
    try:
        model.install_private(db, cfg, clock, profile)
        vector = (
            LearningDataset.from_json(small_model_input.files[model.selection.learning_dataset.path], cfg)
            .samples[0]
            .features
        )
        registry = ModelRegistry(db, cfg, clock, profile)
        assert 0 <= registry.replay_inference(vector)[0] <= 1
        clock.advance(small_model_input.manifest.replay_until - clock.now() + timedelta(microseconds=1))
        with pytest.raises(TradingDisabled):
            registry.replay_inference(vector)
    finally:
        db.close()
