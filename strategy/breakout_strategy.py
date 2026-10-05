"""Closed channel breakout; current candle is excluded from the channel baseline."""

from strategy.base_strategy import FeatureBundle, StrategyVote
from strategy.trend_strategy import trend_bias
from trading.types import Side


class BreakoutStrategy:
    name = "breakout"

    def evaluate(self, features: FeatureBundle) -> StrategyVote:
        p = features.primary
        atr, close = p.value("atr"), p.value("close")
        side = (
            Side.BUY
            if close > p.value("channel_high")
            else Side.SELL
            if close < p.value("channel_low")
            else None
        )
        if side is None or atr <= 0:
            return StrategyVote.wait(self.name, "no_closed_channel_breakout")
        boundary = p.value("channel_high") if side == Side.BUY else p.value("channel_low")
        distance = int(side.sign) * (close - boundary) / atr
        location = p.patterns.close_location if side == Side.BUY else 1 - p.patterns.close_location
        if not 0.1 <= distance <= 0.7 or p.patterns.body_fraction < 0.5 or location < 0.75:
            return StrategyVote.wait(self.name, "breakout_geometry_or_chase")
        if p.value("adx") < 20 or p.value("volume_ratio") < 1.25:
            return StrategyVote.wait(self.name, "breakout_activity_unconfirmed")
        if any(
            trend_bias(frame) == (Side.SELL if side == Side.BUY else Side.BUY)
            for frame in (features.higher, features.trend)
        ):
            return StrategyVote.wait(self.name, "breakout_against_higher_trend")
        alignment = sum(trend_bias(frame) == side for frame in (features.higher, features.trend))
        score = min(96.0, 74 + min(8.0, (p.value("volume_ratio") - 1.25) * 5) + location * 5 + alignment * 4)
        return StrategyVote(
            self.name,
            side,
            round(score, 4),
            ("closed_channel_breakout", "relative_tick_activity", "breakout_geometry"),
        )
