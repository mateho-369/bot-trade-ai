"""Two CLOSED bars only. Candle patterns corroborate, never override other vetoes."""

from __future__ import annotations

import math
from collections.abc import Mapping

from strategy.base_strategy import CandlePatterns
from trading.types import BrokerError


def _ohlc(bar: Mapping) -> tuple[float, float, float, float]:
    try:
        values = tuple(bar[name] for name in ("open", "high", "low", "close"))
        if any(isinstance(value, bool) for value in values):
            raise ValueError
        opening, high, low, close = (float(value) for value in values)
        if not all(math.isfinite(value) and value > 0 for value in (opening, high, low, close)):
            raise ValueError
        if high < max(opening, close, low) or low > min(opening, close, high):
            raise ValueError
        return opening, high, low, close
    except (KeyError, TypeError, ValueError, OverflowError):
        raise BrokerError("invalid pattern OHLC geometry") from None


def detect_patterns(previous: Mapping, current: Mapping) -> CandlePatterns:
    po, ph, pl, pc = _ohlc(previous)
    opening, high, low, close = _ohlc(current)
    width, body = high - low, abs(close - opening)
    if width == 0:
        return CandlePatterns(False, False, False, False, True, high <= ph and low >= pl, 0.0, 0.5)
    upper, lower = high - max(opening, close), min(opening, close) - low
    body_fraction = min(1.0, body / width)
    location = min(1.0, max(0.0, (close - low) / width))
    meaningful = body_fraction >= 0.10
    return CandlePatterns(
        bullish_engulfing=bool(
            pc < po and close > opening and opening <= pc and close >= po and body >= po - pc
        ),
        bearish_engulfing=bool(
            pc > po and close < opening and opening >= pc and close <= po and body >= pc - po
        ),
        hammer=bool(meaningful and lower >= 2 * body and upper <= body and location >= 0.65),
        shooting_star=bool(meaningful and upper >= 2 * body and lower <= body and location <= 0.35),
        doji=bool(body_fraction <= 0.10),
        inside_bar=bool(high <= ph and low >= pl),
        body_fraction=body_fraction,
        close_location=location,
    )
