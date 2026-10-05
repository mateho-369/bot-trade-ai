"""EMA/DI trend following with finalized M15/H1 corroboration; no chase."""

from strategy.base_strategy import FeatureBundle, StrategyVote, TimeframeFeatures
from trading.types import Side


def trend_bias(frame: TimeframeFeatures) -> Side | None:
    close, middle, slow = (frame.value(key) for key in ("close", "ema_mid", "ema_slow"))
    slope = frame.value("ema_mid_slope_atr")
    if close > middle > slow and slope >= 0.02:
        return Side.BUY
    if close < middle < slow and slope <= -0.02:
        return Side.SELL
    return None


class TrendStrategy:
    name = "trend"

    def evaluate(self, features: FeatureBundle) -> StrategyVote:
        p = features.primary
        fast, middle, slow, close = (p.value(key) for key in ("ema_fast", "ema_mid", "ema_slow", "close"))
        side = (
            Side.BUY
            if fast > middle > slow and close > middle
            else (Side.SELL if fast < middle < slow and close < middle else None)
        )
        if side is None or any(trend_bias(frame) != side for frame in (features.higher, features.trend)):
            return StrategyVote.wait(self.name, "higher_trend_not_aligned")
        sign, rsi = int(side.sign), p.value("rsi")
        if not (50 <= rsi <= 74 if side == Side.BUY else 26 <= rsi <= 50):
            return StrategyVote.wait(self.name, "trend_rsi_unconfirmed_or_extended")
        atr = p.value("atr")
        if atr <= 0 or not -0.25 <= sign * (close - fast) / atr <= 1.5:
            return StrategyVote.wait(self.name, "trend_price_extended")
        if (
            p.value("adx") < 20
            or sign * (p.value("di_plus") - p.value("di_minus")) <= 0
            or sign * p.value("ema_mid_slope_atr") < 0.04
        ):
            return StrategyVote.wait(self.name, "trend_strength_unconfirmed")
        patterns = p.patterns
        confirms = (
            patterns.bullish_engulfing or patterns.hammer
            if side == Side.BUY
            else patterns.bearish_engulfing or patterns.shooting_star
        )
        strength = min(
            100.0,
            78
            + min(10.0, (p.value("adx") - 20) / 3)
            + min(6.0, abs(p.value("ema_mid_slope_atr")) * 8)
            + (4 if confirms else 0),
        )
        return StrategyVote(
            self.name,
            side,
            round(strength, 4),
            ("ema_trend", "higher_trend_alignment", "directional_strength"),
        )
