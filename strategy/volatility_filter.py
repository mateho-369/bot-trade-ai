"""Quality vetoes are independent of scores: costs, volatility, stale/gapped bars."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.settings import Settings
from strategy.base_strategy import FeatureBundle
from trading.types import Clock, Side


@dataclass(frozen=True, slots=True)
class FilterDecision:
    allowed: bool
    reasons: tuple[str, ...]


class VolatilityFilter:
    def __init__(self, settings: Settings, clock: Clock):
        self.settings, self.clock = settings, clock

    def evaluate(self, features: FeatureBundle) -> FilterDecision:
        cfg, now, primary, tick = self.settings, self.clock.now(), features.primary, features.tick
        reasons = []
        if not -2 <= (now - tick.time).total_seconds() <= cfg.max_tick_age_seconds:
            reasons.append("stale_quote")
        if not -2 <= (now - features.observed_at).total_seconds() <= cfg.order_max_age_seconds:
            reasons.append("stale_analysis")
        if not 0 <= (now - primary.closed_at).total_seconds() <= cfg.max_candle_age_seconds:
            reasons.append("stale_closed_bar")
        if any(
            frame.value("recent_gap_bars") > cfg.strategy_max_recent_gap_bars
            for frame in (primary, features.higher, features.trend)
        ):
            reasons.append("recent_market_data_gap")
        atr = Decimal(str(primary.value("atr")))
        previous = Decimal(str(primary.value("atr_prev")))
        baseline = Decimal(str(primary.value("atr_median")))
        percent = Decimal(str(primary.value("atr_percent")))
        if atr <= 0 or previous <= 0 or baseline <= 0:
            reasons.append("zero_or_unknown_volatility")
        else:
            if not cfg.volatility_min_atr_percent <= percent <= cfg.volatility_max_atr_percent:
                reasons.append("volatility_out_of_range")
            if atr / baseline > cfg.volatility_max_atr_shock:
                reasons.append("volatility_shock")
            if Decimal(str(primary.value("true_range"))) / previous > cfg.volatility_max_bar_atr_ratio:
                reasons.append("climax_or_gap_bar")
            if (tick.ask - tick.bid) / atr > cfg.volatility_max_spread_atr_ratio:
                reasons.append("spread_too_large_for_atr")
        limit = cfg.symbol_spread_limits.get(
            features.logical_symbol, cfg.symbol_spread_limits.get(features.symbol, cfg.max_spread_points)
        )
        if tick.spread_points(features.info) > limit:
            reasons.append("excessive_spread")
        if tick.bid % features.info.tick_size != 0 or tick.ask % features.info.tick_size != 0:
            reasons.append("off_grid_quote")
        if features.info.trade_mode not in {1, 2, 4}:
            reasons.append("symbol_entry_disabled")
        if (
            not features.info.order_mode & 1
            or not features.info.order_mode & 16
            or not features.info.order_mode & 32
        ):
            reasons.append("native_protection_unavailable")
        return FilterDecision(not reasons, tuple(reasons))

    def entry_distance(self, features: FeatureBundle, side: Side) -> FilterDecision:
        atr = Decimal(str(features.primary.value("atr")))
        close = Decimal(str(features.primary.value("close")))
        if (
            atr <= 0
            or abs(features.tick.entry(side) - close) > atr * self.settings.strategy_max_entry_drift_atr
        ):
            return FilterDecision(False, ("entry_price_chasing",))
        if not (
            features.info.trade_mode == 4
            or features.info.trade_mode == 1
            and side == Side.BUY
            or features.info.trade_mode == 2
            and side == Side.SELL
        ):
            return FilterDecision(False, ("symbol_direction_disabled",))
        return FilterDecision(True, ())
