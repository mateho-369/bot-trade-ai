"""Validate finalized OHLC data, including higher-timeframe close timestamps."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from core.settings import TIMEFRAME_MINUTES
from trading.types import BrokerError, aware_utc


def validated_candles(frame: pd.DataFrame, timeframe: str, as_of: datetime) -> pd.DataFrame:
    if timeframe not in TIMEFRAME_MINUTES:
        raise BrokerError("unsupported timeframe")
    required = {"time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"}
    if not required.issubset(frame.columns) or frame.empty:
        raise BrokerError("missing candle fields/history")
    result = frame.copy(deep=True)
    parsed = pd.to_datetime(result["time"])
    if parsed.dt.tz is None:
        raise BrokerError("candle timestamps must already be timezone-aware")
    result["time"] = parsed.dt.tz_convert("UTC")
    nominal = result["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    if "close_time" in result:
        closes = pd.to_datetime(result["close_time"])
        if closes.dt.tz is None or closes.isna().any():
            raise BrokerError("broker close timestamps must be aware and complete")
        result["close_time"] = closes.dt.tz_convert("UTC")
        if (result["close_time"] < nominal).any():
            raise BrokerError("broker close timestamps cannot precede the nominal bound")
    else:
        result["close_time"] = nominal
    result = result.loc[result["close_time"] <= pd.Timestamp(aware_utc(as_of))].copy()
    if result.empty or result["time"].duplicated().any() or not result["time"].is_monotonic_increasing:
        raise BrokerError("candle timestamps are empty/duplicated/out of order")
    columns = ["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    values = result[columns].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise BrokerError("candle prices must be finite and positive")
    if (result[["tick_volume", "spread", "real_volume"]] < 0).any().any():
        raise BrokerError("negative candle volume/spread")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any() or (
        result["low"] > result[["open", "close", "high"]].min(axis=1)
    ).any():
        raise BrokerError("invalid OHLC range")
    return result.reset_index(drop=True)
