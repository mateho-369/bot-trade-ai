from dataclasses import replace
from decimal import Decimal

import pytest

from core.settings import Settings
from scripts.synthetic_signal_market import ANCHOR
from strategy.base_strategy import CandlePatterns
from strategy.breakout_strategy import BreakoutStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.trend_strategy import TrendStrategy
from strategy.volatility_filter import VolatilityFilter
from tests.signal_helpers import bundle, patch
from trading.types import ManualClock, Side


@pytest.fixture
def technical(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path, symbols=("EURUSD",))
    base = bundle(cfg)
    return cfg, base


def trend_frame(frame, **kwargs):
    values = dict(
        close=1.1,
        ema_fast=1.1001,
        ema_mid=1.0995,
        ema_slow=1.098,
        ema_mid_slope_atr=0.2,
        rsi=60,
        rsi_prev=58,
        adx=32,
        di_plus=30,
        di_minus=15,
        atr=0.001,
        atr_prev=0.001,
        atr_median=0.001,
        atr_percent=0.09,
        macd_hist_atr=0.06,
        macd_hist_change_atr=0.01,
        momentum_atr=0.5,
        momentum3_atr=0.2,
        efficiency=0.6,
    )
    values.update(kwargs)
    return patch(frame, **values)


def trend_bundle(base):
    return replace(
        base,
        primary=trend_frame(base.primary),
        higher=trend_frame(base.higher),
        trend=trend_frame(base.trend),
    )


def mirrored(frame):
    values = dict(frame.metrics)
    price_names = (
        "open",
        "high",
        "low",
        "close",
        "prev_close",
        "ema_fast",
        "ema_mid",
        "ema_slow",
        "bb_mid",
        "bb_lower",
        "bb_upper",
        "bb_lower_prev",
        "bb_upper_prev",
        "channel_high",
        "channel_low",
        "swing_low",
        "swing_high",
    )
    for name in price_names:
        values[name] = 2.2 - values[name]
    for a, b in (
        ("high", "low"),
        ("bb_lower", "bb_upper"),
        ("bb_lower_prev", "bb_upper_prev"),
        ("channel_high", "channel_low"),
        ("swing_low", "swing_high"),
    ):
        values[a], values[b] = values[b], values[a]
    for name in ("rsi", "rsi_prev"):
        values[name] = 100 - values[name]
    for name in (
        "momentum_atr",
        "momentum3_atr",
        "macd_hist_atr",
        "macd_hist_change_atr",
        "ema_mid_slope_atr",
    ):
        values[name] = -values[name]
    values["di_plus"], values["di_minus"] = values["di_minus"], values["di_plus"]
    values["bb_position"] = 1 - values["bb_position"]
    p = frame.patterns
    patterns = CandlePatterns(
        p.bearish_engulfing,
        p.bullish_engulfing,
        p.shooting_star,
        p.hammer,
        p.doji,
        p.inside_bar,
        p.body_fraction,
        1 - p.close_location,
    )
    return replace(frame, metrics=tuple(sorted(values.items())), patterns=patterns)


def mirror(base):
    return replace(
        base, primary=mirrored(base.primary), higher=mirrored(base.higher), trend=mirrored(base.trend)
    )


@pytest.mark.parametrize("sell", [False, True])
def test_trend_and_momentum_are_direction_symmetric(technical, sell):
    _, base = technical
    data = trend_bundle(base)
    data = mirror(data) if sell else data
    for strategy in (TrendStrategy(), MomentumStrategy()):
        result = strategy.evaluate(data)
        assert result.side == (Side.SELL if sell else Side.BUY) and 70 <= result.score <= 100


@pytest.mark.parametrize("change", [{"adx": 19}, {"rsi": 80}, {"ema_mid_slope_atr": 0}, {"di_plus": 1}])
def test_trend_requires_strength_and_nonextended_rsi(technical, change):
    _, base = technical
    data = trend_bundle(base)
    assert TrendStrategy().evaluate(replace(data, primary=patch(data.primary, **change))).side is None


def test_higher_opposition_cannot_be_overridden_by_candle_pattern(technical):
    _, base = technical
    data = trend_bundle(base)
    data = replace(
        data,
        trend=mirrored(data.trend),
        primary=replace(
            data.primary, patterns=CandlePatterns(True, False, True, False, False, False, 0.8, 0.9)
        ),
    )
    assert TrendStrategy().evaluate(data).side is None
    assert MomentumStrategy().evaluate(data).side is None


@pytest.mark.parametrize("sell", [False, True])
def test_closed_range_band_reentry_and_momentum_turn(technical, sell):
    _, base = technical
    p = trend_frame(
        base.primary,
        close=1.0993,
        prev_close=1.099,
        bb_lower_prev=1.0992,
        bb_lower=1.0988,
        rsi=35,
        rsi_prev=32,
        adx=18,
        efficiency=0.2,
        macd_hist_change_atr=0.03,
    )
    p = replace(p, patterns=CandlePatterns(True, False, True, False, False, False, 0.6, 0.85))
    data = replace(
        base,
        primary=p,
        higher=trend_frame(base.higher, adx=18, ema_mid_slope_atr=0.05),
        trend=trend_frame(base.trend, adx=18, ema_mid_slope_atr=0.05),
    )
    data = mirror(data) if sell else data
    assert MeanReversionStrategy().evaluate(data).side == (Side.SELL if sell else Side.BUY)
    assert MomentumStrategy().evaluate(data).side == (Side.SELL if sell else Side.BUY)
    strong = replace(data, trend=patch(data.trend, adx=45))
    assert MeanReversionStrategy().evaluate(strong).side is None


@pytest.mark.parametrize("sell", [False, True])
def test_breakout_needs_closed_channel_activity_and_geometry(technical, sell):
    _, base = technical
    data = trend_bundle(base)
    primary = patch(data.primary, channel_high=1.0998, channel_low=1.0980, volume_ratio=1.8)
    primary = replace(primary, patterns=CandlePatterns(False, False, False, False, False, False, 0.7, 0.9))
    data = replace(data, primary=primary)
    data = mirror(data) if sell else data
    assert BreakoutStrategy().evaluate(data).side == (Side.SELL if sell else Side.BUY)
    assert (
        BreakoutStrategy().evaluate(replace(data, primary=patch(data.primary, volume_ratio=0))).side is None
    )
    assert (
        BreakoutStrategy()
        .evaluate(
            replace(
                data,
                primary=replace(data.primary, patterns=replace(data.primary.patterns, body_fraction=0.1)),
            )
        )
        .side
        is None
    )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("atr", 0, "zero_or_unknown_volatility"),
        ("atr_percent", 0.001, "volatility_out_of_range"),
        ("atr_percent", 5, "volatility_out_of_range"),
        ("atr", 0.0031, "volatility_shock"),
        ("true_range", 0.004, "climax_or_gap_bar"),
        ("recent_gap_bars", 4, "recent_market_data_gap"),
    ],
)
def test_quality_filter_can_veto_any_technical_score(technical, field, value, reason):
    cfg, base = technical
    data = trend_bundle(base)
    data = replace(data, primary=patch(data.primary, **{field: value}))
    result = VolatilityFilter(cfg, ManualClock(ANCHOR)).evaluate(data)
    assert not result.allowed and reason in result.reasons


def test_quote_age_spread_and_chasing_are_separate_quality_gates(technical):
    from datetime import timedelta

    cfg, base = technical
    data = trend_bundle(base)
    filter = VolatilityFilter(cfg, ManualClock(ANCHOR))
    stale = replace(data, tick=replace(data.tick, time=ANCHOR - timedelta(seconds=11)))
    assert "stale_quote" in filter.evaluate(stale).reasons
    spread = replace(data, tick=replace(data.tick, ask=data.tick.bid + Decimal("0.0004")))
    assert "excessive_spread" in filter.evaluate(spread).reasons
    assert "spread_too_large_for_atr" in filter.evaluate(spread).reasons
    far = replace(data, tick=replace(data.tick, bid=Decimal("1.101"), ask=Decimal("1.10112")))
    assert not filter.entry_distance(far, Side.BUY).allowed
