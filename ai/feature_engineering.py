"""Versioned portable features derived ONLY from the immutable pre-entry snapshot."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from core.security import sha256_json
from strategy.base_strategy import FEATURE_FORMAT
from trading.types import BrokerError, aware_utc

FEATURE_VERSION = "reflex-learning-features-v1"
FEATURE_NAMES = (
    "direction",
    "technical_score",
    "coverage",
    "agreement",
    "p_rsi",
    "p_adx",
    "p_di_balance",
    "p_fast_gap_atr",
    "p_mid_gap_atr",
    "p_fast_mid_atr",
    "p_mid_slow_atr",
    "p_atr_percent",
    "p_slope_atr",
    "p_momentum_atr",
    "p_momentum3_atr",
    "p_macd_hist_atr",
    "p_macd_change_atr",
    "p_efficiency",
    "p_volume_ratio",
    "p_bb_position",
    "p_body",
    "p_close_location",
    "h_rsi",
    "h_adx",
    "h_slope_atr",
    "h_fast_mid_atr",
    "t_rsi",
    "t_adx",
    "t_slope_atr",
    "t_mid_slow_atr",
    "utc_hour_sin",
    "utc_hour_cos",
)
FEATURE_SCHEMA_HASH = sha256_json({"version": FEATURE_VERSION, "names": FEATURE_NAMES})


def finite_number(value) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or abs(value) > 1e6
    ):
        raise BrokerError("learning features require finite bounded numbers")
    return float(value)


@dataclass(frozen=True, slots=True)
class FeatureVector:
    values: tuple[float, ...]

    def __post_init__(self):
        if not isinstance(self.values, tuple) or len(self.values) != len(FEATURE_NAMES):
            raise BrokerError("fixed learning feature vector required")
        object.__setattr__(self, "values", tuple(finite_number(value) for value in self.values))
        bounds = {
            name: (0, 1)
            for name in (
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
            )
        }
        bounds.update({"p_di_balance": (-1, 1), "utc_hour_sin": (-1, 1), "utc_hour_cos": (-1, 1)})
        data = self.to_dict()
        if data["direction"] not in {-1.0, 1.0} or data["p_atr_percent"] <= 0 or data["p_volume_ratio"] < 0:
            raise BrokerError("invalid directional/volatility/activity learning feature")
        if any(not low <= data[name] <= high for name, (low, high) in bounds.items()):
            raise BrokerError("normalized learning feature outside its defined geometry")

    def to_dict(self):
        return dict(zip(FEATURE_NAMES, self.values, strict=True))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != set(FEATURE_NAMES):
            raise BrokerError("learning feature schema mismatch")
        return cls(tuple(finite_number(data[name]) for name in FEATURE_NAMES))


class FeatureEngineering:
    @staticmethod
    def from_snapshot(snapshot: dict, technical: dict) -> FeatureVector:
        try:
            if snapshot.get("format") != FEATURE_FORMAT or technical["direction"] not in {"buy", "sell"}:
                raise ValueError
            observed = aware_utc(datetime.fromisoformat(snapshot["observed_at"]))
            frames = snapshot["frames"]
            if not isinstance(frames, list) or len(frames) != 3:
                raise ValueError
            p, h, t = frames
            primary_close = aware_utc(datetime.fromisoformat(p["closed_at"]))
            for frame in frames:
                if (
                    type(frame["bars"]) is not int
                    or frame["bars"] < 200
                    or aware_utc(datetime.fromisoformat(frame["closed_at"])) > primary_close
                    or aware_utc(datetime.fromisoformat(frame["bar_time"]))
                    >= aware_utc(datetime.fromisoformat(frame["closed_at"]))
                ):
                    raise ValueError
            if primary_close > observed:
                raise ValueError

            def metric(frame, key):
                return finite_number(frame["metrics"][key])

            def normalized(frame, a, b):
                atr = metric(frame, "atr")
                if atr <= 0:
                    raise ValueError
                return (metric(frame, a) - metric(frame, b)) / atr

            atr = metric(p, "atr")
            if atr <= 0:
                raise ValueError
            plus, minus = metric(p, "di_plus"), metric(p, "di_minus")
            if min(plus, minus) < 0:
                raise ValueError
            hour = observed.hour + observed.minute / 60
            values = (
                1.0 if technical["direction"] == "buy" else -1.0,
                finite_number(technical["score"]) / 100,
                float(technical["coverage"]),
                float(technical["agreement"]),
                metric(p, "rsi") / 100,
                metric(p, "adx") / 100,
                (plus - minus) / max(plus + minus, 1e-12),
                normalized(p, "close", "ema_fast"),
                normalized(p, "close", "ema_mid"),
                normalized(p, "ema_fast", "ema_mid"),
                normalized(p, "ema_mid", "ema_slow"),
                metric(p, "atr_percent"),
                metric(p, "ema_mid_slope_atr"),
                metric(p, "momentum_atr"),
                metric(p, "momentum3_atr"),
                metric(p, "macd_hist_atr"),
                metric(p, "macd_hist_change_atr"),
                metric(p, "efficiency"),
                metric(p, "volume_ratio"),
                metric(p, "bb_position"),
                finite_number(p["patterns"]["body_fraction"]),
                finite_number(p["patterns"]["close_location"]),
                metric(h, "rsi") / 100,
                metric(h, "adx") / 100,
                metric(h, "ema_mid_slope_atr"),
                normalized(h, "ema_fast", "ema_mid"),
                metric(t, "rsi") / 100,
                metric(t, "adx") / 100,
                metric(t, "ema_mid_slope_atr"),
                normalized(t, "ema_mid", "ema_slow"),
                math.sin(2 * math.pi * hour / 24),
                math.cos(2 * math.pi * hour / 24),
            )
            return FeatureVector(tuple(finite_number(value) for value in values))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise BrokerError("invalid or noncausal pre-entry learning snapshot") from None
