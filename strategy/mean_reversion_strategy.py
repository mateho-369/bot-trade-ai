"""Range-only Bollinger re-entry; never average or counter a strong higher trend."""

from strategy.base_strategy import FeatureBundle, StrategyVote
from trading.types import Side


class MeanReversionStrategy:
    name = "mean_reversion"

    def evaluate(self, features: FeatureBundle) -> StrategyVote:
        p, higher, trend = features.primary, features.higher, features.trend
        if (
            p.value("adx") > 22
            or higher.value("adx") > 25
            or trend.value("adx") > 30
            or p.value("efficiency") > 0.35
            or any(abs(frame.value("ema_mid_slope_atr")) > 0.25 for frame in (higher, trend))
        ):
            return StrategyVote.wait(self.name, "not_a_verified_range")
        close, previous, rsi = (p.value(key) for key in ("close", "prev_close", "rsi"))
        patterns = p.patterns
        buy = (
            previous <= p.value("bb_lower_prev")
            and close > p.value("bb_lower")
            and rsi <= 40
            and close > previous
            and (patterns.hammer or patterns.bullish_engulfing)
            and patterns.close_location >= 0.6
        )
        sell = (
            previous >= p.value("bb_upper_prev")
            and close < p.value("bb_upper")
            and rsi >= 60
            and close < previous
            and (patterns.shooting_star or patterns.bearish_engulfing)
            and patterns.close_location <= 0.4
        )
        if not buy and not sell:
            return StrategyVote.wait(self.name, "no_closed_band_reentry")
        side = Side.BUY if buy else Side.SELL
        score = min(
            92.0,
            76
            + min(10.0, abs(rsi - 50) / 3)
            + (4 if (patterns.bullish_engulfing if buy else patterns.bearish_engulfing) else 0),
        )
        return StrategyVote(
            self.name, side, round(score, 4), ("range_regime", "closed_band_reentry", "reversal_candle")
        )
