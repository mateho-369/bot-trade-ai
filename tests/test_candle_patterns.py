from dataclasses import asdict

import pytest

from strategy.candle_patterns import detect_patterns
from trading.types import BrokerError


def bar(opening, high, low, close):
    return {"open": opening, "high": high, "low": low, "close": close}


def test_engulfing_requires_opposite_bodies_and_full_closed_body_coverage():
    result = detect_patterns(bar(11, 12, 9, 10), bar(9.5, 12, 9, 11.5))
    assert result.bullish_engulfing and not result.bearish_engulfing
    result = detect_patterns(bar(10, 12, 9, 11), bar(11.5, 12, 9, 9.5))
    assert result.bearish_engulfing and not result.bullish_engulfing
    assert not detect_patterns(bar(11, 12, 9, 10), bar(10.5, 12, 9, 11.5)).bullish_engulfing


def test_wick_patterns_are_geometry_not_predictions():
    previous = bar(10, 15, 5, 11)
    hammer = detect_patterns(previous, bar(10, 11.3, 7, 11))
    shooting = detect_patterns(previous, bar(11, 14, 9.7, 10))
    assert hammer.hammer and not hammer.shooting_star
    assert shooting.shooting_star and not shooting.hammer
    assert hammer.inside_bar and shooting.inside_bar
    assert all(
        type(value) is bool
        for key, value in asdict(hammer).items()
        if key not in {"body_fraction", "close_location"}
    )


def test_zero_range_is_neutral_doji_never_divides_by_zero():
    result = detect_patterns(bar(10, 11, 9, 10), bar(10, 10, 10, 10))
    assert result.doji and result.body_fraction == 0 and result.close_location == 0.5
    assert not result.hammer and not result.shooting_star


@pytest.mark.parametrize(
    "changes",
    [{"open": True}, {"close": float("nan")}, {"high": float("inf")}, {"low": -1}, {"high": 8}, {"low": 12}],
)
def test_bad_candles_cannot_create_confirmations(changes):
    current = bar(10, 12, 9, 11)
    current.update(changes)
    with pytest.raises(BrokerError):
        detect_patterns(bar(10, 12, 9, 11), current)
