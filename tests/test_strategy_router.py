from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.settings import Settings
from strategy.base_strategy import StrategyVote
from strategy.strategy_router import StrategyRouter
from trading.types import BrokerError, Side

D = Decimal


def votes(*, trend=("buy", 85), mean=(None, 0), breakout=(None, 0), momentum=("buy", 80)):
    rows = []
    for name, (side, score) in zip(
        ("trend", "mean_reversion", "breakout", "momentum"), (trend, mean, breakout, momentum), strict=True
    ):
        rows.append(StrategyVote(name, Side(side) if side else None, score, ("test_vote",)))
    return tuple(rows)


def test_weighted_coverage_and_agreement_not_sum_of_probabilities():
    cfg = Settings(_env_file=None)
    result = StrategyRouter(cfg).aggregate(votes())
    assert result.side == Side.BUY and result.coverage == D("0.55") and result.agreement == 1
    assert result.score == pytest.approx(float((D("0.3") * 85 + D("0.25") * 80) / D("0.55")), abs=0.000001)


@pytest.mark.parametrize(
    "params,reason",
    [
        ({"momentum": (None, 0)}, "insufficient_independent_strategy_votes"),
        ({"trend": (None, 0), "breakout": ("buy", 90)}, "insufficient_strategy_coverage"),
        ({"mean": ("sell", 85)}, "conflicting_strategy_votes"),
        ({"trend": ("buy", 60), "momentum": ("buy", 60)}, "low_weighted_technical_score"),
    ],
)
def test_quality_targets_never_force_insufficient_or_conflicting_votes(params, reason):
    result = StrategyRouter(Settings(_env_file=None)).aggregate(votes(**params))
    assert result.side is None and result.score == 0 and reason in result.reasons


def test_range_pair_can_qualify_without_renormalizing_inactive_weights():
    result = StrategyRouter(Settings(_env_file=None)).aggregate(
        votes(trend=(None, 0), mean=("buy", 86), momentum=("buy", 78))
    )
    assert result.side == Side.BUY and result.coverage == D("0.5") and result.score == 82


def test_equal_opposition_never_gets_an_arbitrary_direction():
    cfg = Settings(
        _env_file=None,
        strategy_weights={name: D("0.25") for name in ("trend", "mean_reversion", "breakout", "momentum")},
    )
    result = StrategyRouter(cfg).aggregate(
        votes(trend=("buy", 90), mean=("sell", 90), breakout=("sell", 90), momentum=("buy", 90))
    )
    assert result.side is None and result.score == 0


@pytest.mark.parametrize(
    "change",
    [
        {"strategy_weights": {"trend": 1}},
        {"strategy_weights": {"trend": True, "mean_reversion": 0, "breakout": 0, "momentum": 0}},
        {"strategy_weights": {"trend": 0.3, "mean_reversion": 0.25, "breakout": 0.2, "momentum": 0.24}},
        {"strategy_weights": {"trend": -0.1, "mean_reversion": 0.5, "breakout": 0.3, "momentum": 0.3}},
        {"strategy_weights": {"trend": "NaN", "mean_reversion": 0.25, "breakout": 0.2, "momentum": 0.25}},
        {"strategy_weights": {"trend": 1, "mean_reversion": 0, "breakout": 0, "momentum": 0}},
        {
            "strategy_weights": (
                '{"trend":0.3,"trend":0.3,"mean_reversion":0.25,"breakout":0.2,"momentum":0.25}'
            )
        },
        {"strategy_min_agreement": 0.5},
        {"strategy_min_agreeing": True},
        {"volatility_min_atr_percent": 2},
        {"strategy_max_entry_drift_atr": False},
    ],
)
def test_invalid_strategy_settings_fail_closed(change):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **change)


def test_strategy_weights_are_owner_config_hash_bound_and_read_only():
    cfg = Settings(_env_file=None)
    with pytest.raises(TypeError):
        cfg.strategy_weights["trend"] = D("0.5")
    weights = {"trend": D("0.35"), "mean_reversion": D("0.2"), "breakout": D("0.2"), "momentum": D("0.25")}
    changed = Settings(_env_file=None, strategy_weights=weights)
    assert cfg.safety_fingerprint() != changed.safety_fingerprint()
    assert cfg.strategy_fingerprint() != changed.strategy_fingerprint()
    assert cfg.public_config()["strategy_weights"]["trend"] == "0.30"


@pytest.mark.parametrize("score", [True, float("nan"), float("inf"), -1, 101, "90"])
def test_heuristic_votes_are_not_boolean_nan_or_unbounded(score):
    with pytest.raises(BrokerError):
        StrategyVote("trend", Side.BUY, score, ("test_vote",))


def test_duplicate_or_unknown_votes_cannot_inflate_agreement():
    router = StrategyRouter(Settings(_env_file=None))
    with pytest.raises(BrokerError):
        router.aggregate((votes()[0],) * 4)
    with pytest.raises(BrokerError):
        StrategyVote("fake", Side.BUY, 99, ("test_vote",))
    with pytest.raises(BrokerError):
        StrategyVote("trend", None, 99, ("test_vote",))
