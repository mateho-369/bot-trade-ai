"""Validate bounded CLOSED histories; align higher frames to the PRIMARY close."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime

import numpy as np
import pandas as pd

from core.security import canonical_json
from core.settings import TIMEFRAME_MINUTES, Settings
from strategy.base_strategy import FeatureBundle, TimeframeFeatures
from strategy.candle_patterns import detect_patterns
from strategy.indicators import calculate_indicators
from trading.candles import validated_candles
from trading.types import BrokerError, SourceKind, SymbolInfo, Tick, aware_utc

METRICS = (
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "ema_fast",
    "ema_mid",
    "ema_slow",
    "ema_mid_slope_atr",
    "rsi",
    "rsi_prev",
    "adx",
    "di_plus",
    "di_minus",
    "atr",
    "atr_prev",
    "atr_median",
    "atr_percent",
    "bb_upper",
    "bb_lower",
    "bb_mid",
    "bb_position",
    "bb_width_percent",
    "bb_upper_prev",
    "bb_lower_prev",
    "macd_hist_atr",
    "macd_hist_change_atr",
    "momentum_atr",
    "momentum3_atr",
    "efficiency",
    "channel_high",
    "channel_low",
    "swing_low",
    "swing_high",
    "volume_ratio",
    "true_range",
    "bar_range",
    "recent_gap_bars",
)
CANDLE_NUMBERS = ("open", "high", "low", "close", "tick_volume", "spread", "real_volume")


class FeatureEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _closed(self, raw: pd.DataFrame, timeframe: str, cutoff: datetime) -> pd.DataFrame:
        if not isinstance(raw, pd.DataFrame) or raw.columns.duplicated().any() or not 1 <= len(raw) <= 10000:
            raise BrokerError("invalid/oversized/duplicate-column candle history")
        try:
            times = pd.to_datetime(raw["time"])
            if times.isna().any():
                raise BrokerError("missing candle timestamps")
            result = validated_candles(raw, timeframe, cutoff)
            if len(result) < 200:
                raise BrokerError("insufficient closed history; do not fill warmup gaps")
            result = result.tail(self.settings.candle_lookback).reset_index(drop=True)
            for name in CANDLE_NUMBERS:
                values = result[name]
                if any(
                    isinstance(item, (bool, np.bool_)) or isinstance(item, (complex, np.complexfloating))
                    for item in values
                ):
                    raise BrokerError("boolean/complex candles are forbidden")
                if any(isinstance(item, str) for item in values):
                    raise BrokerError("candle numbers must be numeric, not strings")
                result[name] = pd.to_numeric(values, errors="raise").astype(float)
            if not result["close_time"].is_monotonic_increasing or result["close_time"].duplicated().any():
                raise BrokerError("bar closes are duplicated/out of order")
            if (result["close_time"].shift(1).iloc[1:] > result["time"].iloc[1:]).any():
                raise BrokerError("bar close overlaps the next opening")
            if "symbol" in result and not result["symbol"].nunique() == 1:
                raise BrokerError("mixed symbol history")
            return result
        except BrokerError:
            raise
        except (KeyError, ValueError, TypeError, OverflowError):
            raise BrokerError("malformed candle history") from None

    def _snapshot(self, frame: pd.DataFrame, timeframe: str) -> TimeframeFeatures:
        indicators = calculate_indicators(frame)
        last = indicators.iloc[-1]
        seconds = TIMEFRAME_MINUTES[timeframe] * 60
        gaps = frame["time"].diff().dt.total_seconds().iloc[-3:] / seconds
        durations = (frame["close_time"] - frame["time"]).dt.total_seconds().iloc[-3:] / seconds
        values = {name: float(last[name]) for name in METRICS if name != "recent_gap_bars"}
        values["recent_gap_bars"] = float(max(gaps.max(), durations.max()))
        if not all(math.isfinite(value) for value in values.values()):
            raise BrokerError("indicator warmup/nonfinite features; do not impute an approval")
        digest = hashlib.sha256()
        # Stream exact canonical normalized input rows; digest, not full history, goes to AI/DB.
        for row in frame.itertuples(index=False):
            columns = dict(zip(frame.columns, row, strict=True))
            digest.update(
                canonical_json(
                    {
                        "time": columns["time"].isoformat(),
                        "close_time": columns["close_time"].isoformat(),
                        **{name: float(columns[name]) for name in CANDLE_NUMBERS},
                    }
                ).encode()
            )
            digest.update(b"\n")
        return TimeframeFeatures(
            timeframe,
            frame["time"].iloc[-1].to_pydatetime(),
            frame["close_time"].iloc[-1].to_pydatetime(),
            len(frame),
            digest.hexdigest(),
            tuple(sorted(values.items())),
            detect_patterns(frame.iloc[-2], frame.iloc[-1]),
        )

    def build(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        logical_symbol: str,
        info: SymbolInfo,
        tick: Tick,
        source: SourceKind,
        observed_at: datetime,
    ) -> FeatureBundle:
        cfg, now = self.settings, aware_utc(observed_at)
        expected = cfg.symbol_aliases.get(logical_symbol, logical_symbol)
        if logical_symbol not in cfg.symbols or info.name != expected or tick.symbol != expected:
            raise BrokerError("disabled/mismatched logical/native feature symbol")
        try:
            primary = self._closed(frames[cfg.primary_timeframe], cfg.primary_timeframe, now)
            close = primary["close_time"].iloc[-1].to_pydatetime()
            if not 0 <= (now - close).total_seconds() <= cfg.max_candle_age_seconds:
                raise BrokerError("primary closed candle is stale")
            snapshots = {cfg.primary_timeframe: self._snapshot(primary, cfg.primary_timeframe)}
            for timeframe in dict.fromkeys((cfg.higher_timeframe, cfg.trend_timeframe)):
                # Future higher bars, even already closed by polling time, cannot
                # retroactively leak into a decision anchored to an earlier primary close.
                frame = self._closed(frames[timeframe], timeframe, close)
                if (close - frame["close_time"].iloc[-1].to_pydatetime()).total_seconds() > TIMEFRAME_MINUTES[
                    timeframe
                ] * 120:
                    raise BrokerError("higher timeframe coverage is stale")
                snapshots[timeframe] = self._snapshot(frame, timeframe)
            for frame in frames.values():
                if "symbol" in frame and (frame["symbol"] != expected).any():
                    raise BrokerError("source returned the wrong symbol history")
            return FeatureBundle(
                logical_symbol,
                expected,
                source,
                now,
                info,
                tick,
                snapshots[cfg.primary_timeframe],
                snapshots[cfg.higher_timeframe],
                snapshots[cfg.trend_timeframe],
            )
        except KeyError:
            raise BrokerError("required higher timeframe history is missing") from None
