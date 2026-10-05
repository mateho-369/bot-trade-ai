from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from core.settings import Settings
from scripts.synthetic_signal_market import ANCHOR, engineered_bars
from strategy.indicators import calculate_indicators
from tests.signal_helpers import bundle, frames
from trading.types import BrokerError


@pytest.fixture
def cfg(tmp_path):
    return Settings(_env_file=None, project_root=tmp_path, symbols=("EURUSD",))


def test_indicators_match_pinned_ta_and_previous_only_channel(cfg):
    data = frames()
    p = data["M5"]
    features = bundle(cfg, histories=data).primary
    ema = EMAIndicator(p.close, window=200).ema_indicator().iloc[-1]
    atr = AverageTrueRange(p.high, p.low, p.close, window=14).average_true_range().iloc[-1]
    bands = BollingerBands(p.close, window=20)
    assert features.value("ema_slow") == pytest.approx(ema)
    assert features.value("atr") == pytest.approx(atr)
    assert features.value("bb_upper") == pytest.approx(bands.bollinger_hband().iloc[-1])
    assert features.value("channel_high") == p.high.iloc[-21:-1].max()
    altered = p.copy()
    altered.loc[altered.index[-1], "high"] += 0.01
    result = calculate_indicators(altered)
    assert result.channel_high.iloc[-1] == features.value("channel_high")
    assert result.atr.iloc[-1] > atr


def test_forming_future_rows_even_extreme_prices_never_change_features(cfg):
    original = frames()
    expected = bundle(cfg, histories=original)
    extended = {}
    for tf, data in original.items():
        extra = engineered_bars(tf, 1, ANCHOR + timedelta(hours=1), phase=2.6)
        extra.loc[:, ["open", "high", "low", "close"]] = 999999.0
        extended[tf] = pd.concat([data, extra], ignore_index=True)
    actual = bundle(cfg, histories=extended)
    assert actual.history_hash == expected.history_hash
    assert (
        actual.primary == expected.primary
        and actual.higher == expected.higher
        and actual.trend == expected.trend
    )


def test_higher_bar_closed_after_primary_is_excluded_even_if_polling_later(cfg):
    data = frames()
    data["M5"] = data["M5"].iloc[:-1].copy()
    result = bundle(cfg, histories=data, observed=ANCHOR + timedelta(seconds=30))
    assert result.primary.closed_at == ANCHOR - timedelta(minutes=5)
    assert result.higher.closed_at == ANCHOR - timedelta(minutes=15)
    assert result.trend.closed_at == ANCHOR - timedelta(hours=1)
    assert (
        result.higher.closed_at <= result.primary.closed_at
        and result.trend.closed_at <= result.primary.closed_at
    )


def test_historical_price_change_affects_hash_and_indicators(cfg):
    first = frames()
    expected = bundle(cfg, histories=first)
    first["M5"].loc[290, "close"] += 0.00001
    actual = bundle(cfg, histories=first)
    assert actual.history_hash != expected.history_hash
    assert actual.primary.value("ema_fast") != expected.primary.value("ema_fast")


def test_exactly_flat_history_is_neutral_not_rsi_100_or_fake_volume(cfg):
    data = frames()
    for frame in data.values():
        frame.loc[:, ["open", "high", "low", "close"]] = 1.1
        frame.loc[:, "tick_volume"] = 0
    result = bundle(cfg, histories=data)
    assert result.primary.value("rsi") == 50 and result.primary.value("atr") == 0
    assert result.primary.value("efficiency") == 0 and result.primary.value("volume_ratio") == 0
    assert result.primary.value("bb_position") == 0.5


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "short",
        "nan",
        "negative",
        "geometry",
        "duplicate",
        "reversed",
        "naive",
        "nat",
        "bool",
        "strings",
        "overlap",
        "duplicate_close",
        "duplicate_column",
    ],
)
def test_malformed_history_never_imputes_a_trade(cfg, fault):
    data = frames()
    p = data["M5"]
    if fault == "missing":
        p = p.drop(columns="high")
    elif fault == "short":
        p = p.tail(199)
    elif fault == "nan":
        p.loc[299, "close"] = np.nan
    elif fault == "negative":
        p.loc[299, "tick_volume"] = -1
    elif fault == "geometry":
        p.loc[299, "high"] = 0.01
    elif fault == "duplicate":
        p.loc[299, "time"] = p.loc[298, "time"]
    elif fault == "reversed":
        p = p.iloc[::-1]
    elif fault == "naive":
        p["time"] = p.time.dt.tz_localize(None)
    elif fault == "nat":
        p.loc[299, "time"] = pd.NaT
    elif fault == "bool":
        p["spread"] = True
    elif fault == "strings":
        p["close"] = p.close.map(str)
    elif fault == "overlap":
        p["close_time"] = p.time + pd.Timedelta(minutes=6)
    elif fault == "duplicate_close":
        p["close_time"] = p.time + pd.Timedelta(minutes=5)
        p.loc[298, "close_time"] = p.loc[299, "close_time"]
    elif fault == "duplicate_column":
        p = pd.concat([p, p[["high"]]], axis=1)
    data["M5"] = p
    with pytest.raises(BrokerError):
        bundle(cfg, histories=data)


def test_no_mutation_and_immutable_feature_metrics(cfg):
    data = frames()
    copy = data["M5"].copy(deep=True)
    result = bundle(cfg, histories=data)
    pd.testing.assert_frame_equal(data["M5"], copy)
    with pytest.raises(AttributeError):
        result.primary.metrics = (("close", 0),)
    encoded = result.to_dict()
    encoded["frames"][0]["metrics"]["close"] = 5
    assert result.primary.value("close") != 5


@pytest.mark.parametrize("timeframe", ["M15", "H1"])
def test_missing_higher_history_blocks_instead_of_using_primary_copy(cfg, timeframe):
    data = frames()
    del data[timeframe]
    with pytest.raises(BrokerError):
        bundle(cfg, histories=data)


def test_stale_primary_and_stale_higher_history(cfg):
    with pytest.raises(BrokerError):
        bundle(cfg, observed=ANCHOR + timedelta(minutes=8))
    data = frames()
    data["H1"] = engineered_bars("H1", 300, ANCHOR - timedelta(hours=4), phase=2.6)
    with pytest.raises(BrokerError):
        bundle(cfg, histories=data)


def test_duplicate_timeframe_configuration_is_read_once_and_supported(tmp_path):
    cfg = Settings(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD",),
        higher_timeframe="M15",
        trend_timeframe="M15",
    )
    result = bundle(cfg)
    assert result.higher is result.trend
