"""Causal pandas/ta indicators; no centered windows, backward fill or future rows."""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from trading.types import BrokerError


def calculate_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    if len(candles) < 200:
        raise BrokerError("200 finalized bars are required for indicator warmup")
    close, high, low = (candles[name].astype(float) for name in ("close", "high", "low"))
    result = candles.copy(deep=True)
    for name, window in (("ema_fast", 20), ("ema_mid", 50), ("ema_slow", 200)):
        result[name] = EMAIndicator(close, window=window, fillna=False).ema_indicator()
    atr = AverageTrueRange(high, low, close, window=14, fillna=False).average_true_range()
    result["atr"], result["atr_prev"] = atr, atr.shift(1)
    result["atr_median"] = atr.shift(1).rolling(20, min_periods=20).median()
    result["atr_percent"] = atr / close * 100
    result["ema_mid_slope_atr"] = (result["ema_mid"] - result["ema_mid"].shift(5)) / atr.replace(0, np.nan)
    result["rsi"] = RSIIndicator(close, window=14, fillna=False).rsi()
    # ta returns 100 on exactly flat prices; neutralize the 0/0 flat case explicitly.
    flat = close.diff().abs().rolling(14, min_periods=14).sum().eq(0)
    result.loc[flat, "rsi"] = 50.0
    result["rsi_prev"] = result["rsi"].shift(1)
    adx = ADXIndicator(high, low, close, window=14, fillna=False)
    result["adx"], result["di_plus"], result["di_minus"] = adx.adx(), adx.adx_pos(), adx.adx_neg()
    bands = BollingerBands(close, window=20, window_dev=2, fillna=False)
    result["bb_upper"], result["bb_lower"] = bands.bollinger_hband(), bands.bollinger_lband()
    result["bb_mid"] = bands.bollinger_mavg()
    width = result["bb_upper"] - result["bb_lower"]
    result["bb_position"] = (close - result["bb_lower"]) / width.replace(0, np.nan)
    result.loc[width.eq(0), "bb_position"] = 0.5
    result["bb_width_percent"] = width / close * 100
    result["bb_upper_prev"], result["bb_lower_prev"] = (
        result["bb_upper"].shift(1),
        result["bb_lower"].shift(1),
    )
    histogram = MACD(close, window_slow=26, window_fast=12, window_sign=9, fillna=False).macd_diff()
    result["macd_hist_atr"] = histogram / atr.replace(0, np.nan)
    result["macd_hist_change_atr"] = histogram.diff() / atr.replace(0, np.nan)
    result["momentum_atr"] = (close - close.shift(6)) / atr.replace(0, np.nan)
    result["momentum3_atr"] = (close - close.shift(3)) / atr.replace(0, np.nan)
    travelled = close.diff().abs().rolling(20, min_periods=20).sum()
    result["efficiency"] = (close - close.shift(20)).abs() / travelled.replace(0, np.nan)
    result.loc[travelled.eq(0), "efficiency"] = 0.0
    # Exclude the candidate bar from channels and volume/volatility baselines.
    result["channel_high"] = high.shift(1).rolling(20, min_periods=20).max()
    result["channel_low"] = low.shift(1).rolling(20, min_periods=20).min()
    result["swing_low"] = low.rolling(5, min_periods=5).min()
    result["swing_high"] = high.rolling(5, min_periods=5).max()
    volume = candles["tick_volume"].astype(float)
    prior_volume = volume.shift(1).rolling(20, min_periods=20).mean()
    result["volume_ratio"] = volume / prior_volume.replace(0, np.nan)
    result.loc[prior_volume.eq(0), "volume_ratio"] = 0.0  # Missing activity is not a fake confirmation.
    previous = close.shift(1)
    result["true_range"] = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
    ).max(axis=1)
    result["prev_close"] = previous
    result["bar_range"] = high - low
    # Flat zero-ATR history is valid input but is vetoed by VolatilityFilter.
    for name in (
        "ema_mid_slope_atr",
        "macd_hist_atr",
        "macd_hist_change_atr",
        "momentum_atr",
        "momentum3_atr",
    ):
        result.loc[atr.eq(0), name] = 0.0
    return result
