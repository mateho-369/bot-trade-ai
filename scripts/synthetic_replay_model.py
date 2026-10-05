"""ARTIFICIAL engineering vectors/labels with a deliberately designed relationship.

Not prices, executed-trade evidence, genuine historical model availability, calibration,
or predictive skill. The fixture supports BOTH a real probability approval and veto,
without changing the live/research filter threshold or bypassing any trading gate.
"""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

import numpy as np

from ai.dataset import LearningDataset, TradeSample
from ai.feature_engineering import FEATURE_NAMES, FeatureVector
from core.security import sha256_json
from core.settings import TIMEFRAME_MINUTES
from trading.types import SourceKind


def artificial_corpus(settings, profile, replay_from, *, count=480, seed=42, relationship="quality"):
    if (
        type(count) is not int
        or not settings.model_min_labelled_trades <= count <= 5000
        or type(seed) is not int
        or not 0 <= seed <= 1000000
        or relationship not in {"quality", "inverse_quality"}
        or profile.data_source != SourceKind.HISTORICAL
    ):
        raise ValueError("bounded explicit artificial historical-research fixture required")
    buffer = timedelta(
        minutes=TIMEFRAME_MINUTES[settings.primary_timeframe]
        * max(settings.model_embargo_bars, settings.model_label_horizon_bars),
        hours=3,
    )
    exported = replay_from - buffer
    rng, rows = np.random.default_rng(seed), []
    unit_interval = {
        "technical_score",
        "coverage",
        "agreement",
        "p_rsi",
        "p_adx",
        "p_body",
        "p_close_location",
        "p_efficiency",
        "h_rsi",
        "h_adx",
        "t_rsi",
        "t_adx",
    }
    for i in range(count):
        now = exported - timedelta(minutes=15 * (count - i))
        values = rng.normal(0, 1, len(FEATURE_NAMES))
        for j, name in enumerate(FEATURE_NAMES):
            if name in unit_interval:
                values[j] = rng.uniform(0.1, 0.9)
        values[0] = 1 if i % 2 == 0 else -1
        values[FEATURE_NAMES.index("p_di_balance")] = rng.uniform(-1, 1)
        values[FEATURE_NAMES.index("p_atr_percent")] = 0.04
        values[FEATURE_NAMES.index("p_volume_ratio")] = 1.2
        hour = now.hour + now.minute / 60
        values[-2:] = [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24)]
        quality = values[FEATURE_NAMES.index("technical_score")] + rng.normal(0, 0.16) > 0.5
        win = quality if relationship == "quality" else not quality
        rows.append(
            TradeSample(
                sha256_json({"ARTIFICIAL_ONLY": i, "seed": seed, "relationship": relationship}),
                now,
                now + timedelta(seconds=1),
                now + timedelta(minutes=5),
                now + timedelta(minutes=6),
                "EURUSD",
                "buy" if values[0] > 0 else "sell",
                FeatureVector(tuple(float(v) for v in values)),
                Decimal("6") if win else Decimal("-5"),
                Decimal("5"),
                sha256_json({"ARTIFICIAL_PROOF_NOT_AUTHENTIC": i}),
            )
        )
    return LearningDataset(
        SourceKind.HISTORICAL,
        "fixture",
        settings.strategy_fingerprint(),
        profile.code_hash,
        sha256_json("ARTIFICIAL_ACCOUNT_NOT_BROKER_EVIDENCE"),
        exported,
        tuple(sorted(rows, key=lambda row: (row.decision_at, row.sample_id))),
    )
