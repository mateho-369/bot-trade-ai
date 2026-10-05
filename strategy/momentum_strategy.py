"""MACD/RSI price momentum; range-reversal confirmation is a separate regime."""

from strategy.base_strategy import FeatureBundle, StrategyVote
from strategy.trend_strategy import trend_bias
from trading.types import Side


class MomentumStrategy:
    name = "momentum"

    def evaluate(self, features: FeatureBundle) -> StrategyVote:
        p = features.primary
        rsi, momentum, recent, hist = (
            p.value(key) for key in ("rsi", "momentum_atr", "momentum3_atr", "macd_hist_atr")
        )
        side = Side.BUY if momentum > 0 else Side.SELL if momentum < 0 else None
        if side is not None:
            sign = int(side.sign)
            aligned = all(trend_bias(frame) == side for frame in (features.higher, features.trend))
            rsi_ok = 52 <= rsi <= 74 if side == Side.BUY else 26 <= rsi <= 48
            if (
                aligned
                and rsi_ok
                and 0.15 <= abs(momentum) <= 2
                and sign * recent > 0
                and sign * hist >= 0.01
                and sign * p.value("macd_hist_change_atr") >= -0.02
            ):
                score = min(
                    94.0,
                    76
                    + min(8.0, abs(momentum) * 5)
                    + min(6.0, abs(hist) * 12)
                    + (3 if sign * (rsi - p.value("rsi_prev")) > 0 else 0),
                )
                return StrategyVote(
                    self.name,
                    side,
                    round(score, 4),
                    ("price_momentum", "macd_confirmation", "higher_trend_alignment"),
                )
        range_ok = (
            p.value("adx") <= 22
            and features.higher.value("adx") <= 25
            and features.trend.value("adx") <= 30
            and p.value("efficiency") <= 0.35
            and all(
                abs(frame.value("ema_mid_slope_atr")) <= 0.25 for frame in (features.higher, features.trend)
            )
        )
        change = p.value("macd_hist_change_atr")
        buy = (
            range_ok
            and 30 <= rsi <= 45
            and rsi > p.value("rsi_prev")
            and change >= 0.01
            and p.value("close") > p.value("prev_close")
            and (p.patterns.hammer or p.patterns.bullish_engulfing)
        )
        sell = (
            range_ok
            and 55 <= rsi <= 70
            and rsi < p.value("rsi_prev")
            and change <= -0.01
            and p.value("close") < p.value("prev_close")
            and (p.patterns.shooting_star or p.patterns.bearish_engulfing)
        )
        if buy or sell:
            return StrategyVote(
                self.name,
                Side.BUY if buy else Side.SELL,
                78.0,
                ("range_momentum_turn", "macd_turn", "reversal_candle"),
            )
        return StrategyVote.wait(self.name, "momentum_unconfirmed_or_extended")
