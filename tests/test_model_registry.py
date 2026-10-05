from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from ai.artifacts import artifact_path
from ai.model_registry import ModelRegistry
from ai.model_trainer import ModelTrainer, TrainingResult
from core.database import Database
from core.models import BotState, ModelVersion
from core.security import canonical_json
from tests.ai_helpers import fixture_dataset
from tests.risk_helpers import MOMENT, OWNER, config
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def registry(tmp_path):
    cfg = config(tmp_path, backtest_mode=True, paper_trading=False)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    registry = ModelRegistry(db, cfg, clock, profile)
    result = ModelTrainer(cfg, profile).train(fixture_dataset(cfg), as_of=MOMENT)
    yield registry, result
    db.close()


def test_candidate_is_inactive_immutable_and_deduplicated(registry):
    reg, result = registry
    model = reg.register(result)
    assert not model.active and model.scope == "candidate" and reg.register(result).model_id == model.model_id
    assert reg.get(model.model_id).digest == result.digest
    data = model.payload()
    data["model"]["intercept"] = 999
    assert reg.get(model.model_id).payload()["model"]["intercept"] != 999
    model = reg.activate(model.model_id, scope="backtest", owner_id=OWNER)
    assert model.active and reg.active_for_runtime().digest == result.digest
    assert reg.runtime_profile().model_sha256 == result.digest
    with pytest.raises(TradingDisabled):
        reg.inference(fixture_dataset(reg.settings).samples[0].features)


def test_stopped_recomposed_model_inference_and_owner_rollback(registry):
    reg, result = registry
    first = reg.register(result)
    reg.activate(first.model_id, scope="backtest", owner_id=OWNER)
    second_result = ModelTrainer(reg.settings, reg.profile).train(
        fixture_dataset(reg.settings, seed=91), as_of=MOMENT
    )
    second = reg.register(second_result)
    reg.activate(second.model_id, scope="backtest", owner_id=OWNER)
    current = ModelRegistry(
        reg.database,
        reg.settings,
        reg.clock,
        RuntimeProfile(reg.profile.code_hash, second.digest, SourceKind.SYNTHETIC),
    )
    score = current.inference(fixture_dataset(reg.settings).samples[0].features)
    assert 0 <= score <= 1
    selected = reg.rollback(first.model_id, scope="backtest", owner_id=OWNER)
    assert selected.digest == first.digest and reg.active_for_runtime().model_id == first.model_id
    with reg.database.session() as s:
        assert (
            sum(r.active for r in s.scalars(select(ModelVersion))) == 1
            and s.get(BotState, 1).desired_state == "paused"
        )


@pytest.mark.parametrize(
    "fault",
    [
        "file",
        "db_metrics",
        "db_path",
        "db_type",
        "db_policy",
        "multiple_active",
        "age",
        "wrong_owner",
        "live_skip",
        "paper_synthetic",
    ],
)
def test_artifact_pointer_and_stage_vetoes(registry, fault):
    reg, result = registry
    model = reg.register(result)
    if fault == "file":
        artifact_path(reg.settings, model.digest).write_text("{}")
    elif fault.startswith("db_"):
        with reg.database.session() as s:
            row = s.get(ModelVersion, model.model_id)
            if fault == "db_metrics":
                row.metrics_json = {}
            if fault == "db_path":
                row.file_path = "../evil.pkl"
            if fault == "db_type":
                row.model_type = "pickle"
            if fault == "db_policy":
                row.config_hash = "d" * 64
    if fault in {"file", "db_metrics", "db_path", "db_type", "db_policy"}:
        with pytest.raises(TradingDisabled):
            reg.get(model.model_id)
        return
    if fault == "wrong_owner":
        with pytest.raises(TradingDisabled):
            reg.activate(model.model_id, scope="backtest", owner_id=OWNER + 1)
    elif fault == "live_skip":
        with pytest.raises(TradingDisabled):
            reg.activate(model.model_id, scope="live", owner_id=OWNER)
    else:
        reg.activate(model.model_id, scope="backtest", owner_id=OWNER)
        if fault == "paper_synthetic":
            with pytest.raises(TradingDisabled):
                reg.activate(model.model_id, scope="paper", owner_id=OWNER)
        elif fault == "age":
            reg.clock.advance(timedelta(days=31))
            with pytest.raises(TradingDisabled):
                reg.active_for_runtime()
        elif fault == "multiple_active":
            other = reg.register(
                ModelTrainer(reg.settings, reg.profile).train(
                    fixture_dataset(reg.settings, seed=91), as_of=MOMENT
                )
            )
            with reg.database.session() as s:
                s.get(ModelVersion, other.model_id).active = True
            with pytest.raises(TradingDisabled):
                reg.active_for_runtime()


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "mt5"),
        ("code_hash", "d" * 64),
        ("policy_hash", "d" * 64),
        ("sample_count", True),
        ("created_at", "2099-01-01T00:00:00+00:00"),
        ("feature_names", ["future_pnl"]),
        ("origin", "native_verified"),
    ],
)
def test_forged_result_header_rejected(registry, field, value):
    reg, result = registry
    data = result.payload()
    data[field] = value
    with pytest.raises(TradingDisabled):
        reg.register(TrainingResult(canonical_json(data)))


def test_evaluation_failure_cannot_be_flipped_and_symlink_is_forbidden(registry, tmp_path):
    reg, result = registry
    data = result.payload()
    data["evaluation"]["passed"] = not data["evaluation"]["passed"]
    with pytest.raises(TradingDisabled):
        reg.register(TrainingResult(canonical_json(data)))
    folder = reg.settings.project_root / "data" / "models"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.rmdir()
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TradingDisabled):
        reg.register(result)


def test_concurrent_registration_and_selection_single_pointer(registry):
    reg, result = registry
    with ThreadPoolExecutor(max_workers=4) as pool:
        models = list(pool.map(lambda _: reg.register(result), range(8)))
    assert len({m.model_id for m in models}) == 1
    with ThreadPoolExecutor(max_workers=3) as pool:
        models = list(
            pool.map(lambda _: reg.activate(models[0].model_id, scope="backtest", owner_id=OWNER), range(6))
        )
    assert all(m.active for m in models)
    with reg.database.session() as s:
        assert len(s.scalars(select(ModelVersion)).all()) == 1


def test_selection_refuses_stale_projected_policy(registry):
    reg, result = registry
    model = reg.register(result)
    with reg.database.session() as session:
        session.get(BotState, 1).settings_overrides = {"new_hash": "f" * 64}
    with pytest.raises(TradingDisabled):
        reg.activate(model.model_id, scope="backtest", owner_id=OWNER)
    assert not reg.get(model.model_id).active
