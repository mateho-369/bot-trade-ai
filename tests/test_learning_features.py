import copy
from datetime import timedelta
from decimal import Decimal

import pytest

from ai.dataset import LearningDataset
from ai.feature_engineering import FEATURE_NAMES, FeatureEngineering
from core.security import canonical_json
from tests.ai_helpers import fixture_dataset
from tests.risk_helpers import config
from tests.signal_helpers import bundle
from trading.types import BrokerError


def test_exact_pre_entry_feature_mapping_and_no_outcome_inputs(tmp_path):
    cfg = config(tmp_path)
    f = bundle(cfg)
    technical = {"direction": "buy", "score": 80, "coverage": "0.55", "agreement": "1"}
    vector = FeatureEngineering.from_snapshot(f.to_dict(), technical)
    data = vector.to_dict()
    assert data["direction"] == 1 and data["technical_score"] == 0.8
    assert data["p_rsi"] == f.primary.value("rsi") / 100
    assert data["p_fast_gap_atr"] == pytest.approx(
        (f.primary.value("close") - f.primary.value("ema_fast")) / f.primary.value("atr")
    )
    assert not set(data) & {
        "profit",
        "label",
        "exit_price",
        "close_time",
        "profit_lock_level",
        "balance",
        "owner_id",
    }
    snapshot = f.to_dict()
    snapshot["future_profit"] = 1e9
    assert FeatureEngineering.from_snapshot(snapshot, technical) == vector
    assert len(data) == len(FEATURE_NAMES) == 32


@pytest.mark.parametrize(
    "fault",
    ["future_higher", "future_primary", "zero_atr", "missing_metric", "nan", "boolean", "negative_adx"],
)
def test_bad_or_future_snapshot_fails(tmp_path, fault):
    cfg = config(tmp_path)
    snapshot = bundle(cfg).to_dict()
    technical = {"direction": "buy", "score": 80, "coverage": "0.55", "agreement": "1"}
    p = snapshot["frames"][0]
    if fault == "future_higher":
        snapshot["frames"][1]["closed_at"] = (bundle(cfg).observed_at + timedelta(minutes=1)).isoformat()
    if fault == "future_primary":
        p["closed_at"] = (bundle(cfg).observed_at + timedelta(minutes=1)).isoformat()
    if fault == "zero_atr":
        p["metrics"]["atr"] = 0
    if fault == "missing_metric":
        p["metrics"].pop("rsi")
    if fault == "nan":
        p["metrics"]["rsi"] = float("nan")
    if fault == "boolean":
        p["metrics"]["adx"] = True
    if fault == "negative_adx":
        p["metrics"]["adx"] = -1
    with pytest.raises(BrokerError):
        FeatureEngineering.from_snapshot(snapshot, technical)


def test_dataset_roundtrip_hash_and_frozen_feature_rows(tmp_path):
    cfg = config(tmp_path)
    dataset = fixture_dataset(cfg, count=480)
    restored = LearningDataset.from_json(canonical_json(dataset.to_dict()).encode(), cfg)
    assert restored.digest == dataset.digest and restored == dataset
    row = dataset.samples[0]
    with pytest.raises(AttributeError):
        row.net_usd = Decimal(9)
    x, y = dataset.matrices()
    x[0, 1] = 999
    assert dataset.samples[0].features.values[1] != 999 and y.shape == (480,)
    assert row.label == int(row.net_usd > 0)


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "unsorted",
        "future_label",
        "open_time",
        "negative_risk",
        "float_money",
        "side",
        "extra_feature",
        "cost_bool",
        "extra_header",
    ],
)
def test_dataset_contract_prevents_label_leaks(tmp_path, fault):
    cfg = config(tmp_path)
    data = fixture_dataset(cfg, count=300).to_dict()
    row = data["samples"][0]
    if fault == "duplicate":
        data["samples"][1] = copy.deepcopy(row)
    if fault == "unsorted":
        data["samples"].reverse()
    if fault == "future_label":
        row["label_available_at"] = "2099-01-01T00:00:00+00:00"
    if fault == "open_time":
        row["entry_at"] = row["exit_at"]
    if fault == "negative_risk":
        row["risk_usd"] = "-5"
    if fault == "float_money":
        row["net_usd"] = 6.0
    if fault == "side":
        row["direction"] = "sell" if row["direction"] == "buy" else "buy"
    if fault == "extra_feature":
        row["features"]["future_pnl"] = 9
    if fault == "cost_bool":
        data["costs_included"] = 1
    if fault == "extra_header":
        data["owner_id"] = 42
    with pytest.raises(BrokerError):
        LearningDataset.from_dict(data, cfg)
