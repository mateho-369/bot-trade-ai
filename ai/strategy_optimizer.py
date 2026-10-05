"""Small owner-review rebalancing from OOS vote associations, not independent strategy alpha."""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from sqlalchemy import select

from ai.dataset import LearningDataset
from ai.learning_engine import LearningEngine
from ai.suggestion_store import SuggestionStore
from ai.walk_forward import purged_walk_forward
from core.database import Database
from core.models import Trade
from core.security import sha256_json
from core.settings import Settings
from trading.types import TradingDisabled


class StrategyOptimizer:
    def __init__(self, database: Database, settings: Settings, store: SuggestionStore):
        self.database, self.settings, self.store = database, settings, store

    def propose(self, dataset: LearningDataset, evaluation: dict, *, account_key: str):
        if (
            evaluation.get("format") != "reflex-model-evaluation-v1"
            or evaluation.get("passed") is not True
            or evaluation.get("dataset_sha256") != dataset.digest
            or evaluation.get("source") != dataset.source.value
            or dataset.policy_hash != self.settings.strategy_fingerprint()
            or dataset.origin != "reconciled_trades"
        ):
            raise TradingDisabled("bound purged evaluation/reconciled dataset required for weight proposal")
        current = (
            LearningEngine(self.database, self.settings, self.store.clock, self.store.profile)
            .export(account_key)
            .dataset
        )
        if current is None or current.samples != dataset.samples or current.source != dataset.source:
            raise TradingDisabled("original closed feature/label proof cohort changed before proposal")
        folds = purged_walk_forward(dataset, self.settings)
        if len(evaluation.get("folds", [])) != len(folds):
            raise TradingDisabled("OOS folds differ")
        eligible = set()
        for fold, report in zip(folds, evaluation["folds"], strict=True):
            ids = [dataset.samples[i].sample_id for i in fold.test]
            if report["test_ids_hash"] != sha256_json(ids):
                raise TradingDisabled("OOS association cohort changed")
            eligible.update(ids)
        sample_map = {s.sample_id: s for s in dataset.samples if s.sample_id in eligible}
        returns = defaultdict(list)
        with self.database.session() as session:
            trades = session.scalars(
                select(Trade)
                .where(Trade.account_key == account_key, Trade.mode == self.settings.mode.value)
                .limit(self.settings.model_max_dataset_rows + 1)
            ).all()
            if len(trades) > self.settings.model_max_dataset_rows:
                raise TradingDisabled("attribution cohort exceeds cap")
            for trade in trades:
                key = sha256_json({"account": trade.account_key, "mode": trade.mode, "trade": trade.id})
                if key not in sample_map:
                    continue
                sample = sample_map[key]
                technical = trade.features_json["decision"]["features"]["technical"]
                if technical["direction"] != sample.direction:
                    raise TradingDisabled("original attribution changed")
                for vote in technical["votes"]:
                    name = vote["strategy"]
                    if (
                        name in self.settings.strategy_weights
                        and self.settings.strategy_weights[name] > 0
                        and vote["direction"] == sample.direction
                        and vote["score"] >= self.settings.strategy_min_vote_score
                    ):
                        returns[name].append(sample.net_r)
        statistics = {}
        for name, values in returns.items():
            if len(values) < 30:
                continue
            mean = float(np.mean(values))
            stderr = float(np.std(values, ddof=1) / math.sqrt(len(values)))
            statistics[name] = {"n": len(values), "mean_net_r": mean, "stderr": stderr}
        if len(statistics) < 2:
            return None
        best = max(statistics, key=lambda k: statistics[k]["mean_net_r"])
        worst = min(statistics, key=lambda k: statistics[k]["mean_net_r"])
        a, b = statistics[best], statistics[worst]
        if best == worst or a["mean_net_r"] - 2 * a["stderr"] <= b["mean_net_r"] + 2 * b["stderr"]:
            return None
        weights = dict(self.settings.strategy_weights)
        step = min(self.settings.max_strategy_weight_step, weights[worst], 1 - weights[best])
        if step <= 0:
            return None
        weights[best] += step
        weights[worst] -= step
        evidence = sha256_json(
            {
                "dataset": dataset.digest,
                "evaluation": sha256_json(evaluation),
                "association_statistics": statistics,
                "needs_independent_revalidation": True,
            }
        )
        return self.store.create(
            "rebalance_weights",
            {"weights": {k: str(v) for k, v in weights.items()}},
            reason=(
                "Small OOS vote-association rebalance; correlated votes, owner review "
                "and fresh independent stage validation required."
            ),
            request_hash=evidence,
            data_evidence_hash=evidence,
        )
