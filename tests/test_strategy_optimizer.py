"""TEST ONLY hand-written association DTOs; no native/exporter/stage evidence."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ai.model_trainer import ModelTrainer
from ai.strategy_optimizer import StrategyOptimizer
from core.security import sha256_json
from tests.ai_helpers import fixture_dataset
from tests.risk_helpers import MOMENT, config
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind, TradingDisabled


class FixtureDatabase:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def session(self):
        yield self

    def scalars(self, _):
        return self

    def all(self):
        return self.rows


class CapturingStore:
    def __init__(self, profile):
        self.calls = []
        self.clock = None
        self.profile = profile

    def create(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return args, kwargs


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    dataset = fixture_dataset(cfg, origin="reconciled_trades")
    samples = tuple(
        replace(s, sample_id=sha256_json({"account": "fixture", "mode": "paper", "trade": i}))
        for i, s in enumerate(dataset.samples)
    )
    dataset = replace(dataset, samples=samples)
    # Artificial outcome-vote association constructed to exercise the small-step math.
    rows = []
    for i, s in enumerate(samples):
        name = "trend" if s.label else "mean_reversion"
        rows.append(
            SimpleNamespace(
                id=i,
                account_key="fixture",
                mode="paper",
                features_json={
                    "decision": {
                        "features": {
                            "technical": {
                                "direction": s.direction,
                                "votes": [{"strategy": name, "direction": s.direction, "score": 90}],
                            }
                        }
                    }
                },
            )
        )
    store = CapturingStore(profile)
    database = FixtureDatabase(rows)
    monkeypatch.setattr(
        "ai.strategy_optimizer.LearningEngine",
        lambda *a: SimpleNamespace(export=lambda account: SimpleNamespace(dataset=dataset)),
    )
    evaluation = ModelTrainer(cfg, profile).train(dataset, as_of=MOMENT).payload()["evaluation"]
    return cfg, dataset, evaluation, store, database


def test_bounded_association_proposal_never_applies_or_claims_independence(setup):
    cfg, dataset, report, store, db = setup
    proposal = StrategyOptimizer(db, cfg, store).propose(dataset, report, account_key="fixture")
    assert proposal is not None and len(store.calls) == 1
    kind, parameters = store.calls[0][0]
    assert kind == "rebalance_weights"
    from decimal import Decimal

    weights = {k: Decimal(v) for k, v in parameters["weights"].items()}
    assert sum(weights.values()) == 1 and weights["trend"] == cfg.strategy_weights["trend"] + Decimal(".02")
    assert weights["mean_reversion"] == cfg.strategy_weights["mean_reversion"] - Decimal(".02")
    assert "correlated" in store.calls[0][1]["reason"] and "independent" in store.calls[0][1]["reason"]


@pytest.mark.parametrize("fault", ["failed", "digest", "cohort", "fixture_origin"])
def test_unbound_or_failed_optimization_never_proposes(setup, fault):
    cfg, dataset, report, store, db = setup
    report = {**report}
    if fault == "failed":
        report["passed"] = False
    if fault == "digest":
        report["dataset_sha256"] = "f" * 64
    if fault == "cohort":
        report["folds"] = [{**f, "test_ids_hash": "f" * 64} for f in report["folds"]]
    if fault == "fixture_origin":
        dataset = replace(dataset, origin="fixture")
    with pytest.raises(TradingDisabled):
        StrategyOptimizer(db, cfg, store).propose(dataset, report, account_key="fixture")
    assert not store.calls


def test_correlated_identical_votes_have_no_rebalance_signal(setup):
    cfg, dataset, report, store, db = setup
    for row in db.rows:
        t = row.features_json["decision"]["features"]["technical"]
        t["votes"] = [
            {"strategy": name, "direction": t["direction"], "score": 90} for name in ("trend", "momentum")
        ]
    assert (
        StrategyOptimizer(db, cfg, store).propose(dataset, report, account_key="fixture") is None
        and not store.calls
    )
