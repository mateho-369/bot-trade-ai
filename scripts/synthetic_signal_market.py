"""TEST/DIAGNOSTIC ONLY engineered OHLC; NOT historical data or stage evidence.

Source is always SYNTHETIC. The histories are independent engineered timeframe
fixtures, not an aggregation-consistent market replay or a profitable strategy.
Only regression tests/smoke_signals import this module. No network/native SDK.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from core.settings import TIMEFRAME_MINUTES
from trading.mock_mt5 import MockMarketData
from trading.types import BrokerError, SourceKind, Tick, aware_utc

ANCHOR = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def engineered_bars(
    timeframe: str,
    count: int,
    cutoff: datetime,
    *,
    sign: int = 1,
    padding: float = 0.0005,
    phase: float = 0.0,
) -> pd.DataFrame:
    seconds = TIMEFRAME_MINUTES[timeframe] * 60
    end = int(aware_utc(cutoff).timestamp()) // seconds * seconds
    anchor_index = int(ANCHOR.timestamp()) // seconds

    # Function of absolute bar index, so adding future bars cannot change an old prefix.
    def price(index: int):
        offset = index - anchor_index
        return round(1.10000 + sign * (offset * 0.00004 + 0.00025 * math.sin(offset * 0.65 + phase)), 5)

    rows = []
    for start in range(end - count * seconds, end, seconds):
        index = start // seconds
        opening, close = price(index), price(index + 1)
        rows.append(
            {
                "time": datetime.fromtimestamp(start, timezone.utc),
                "open": opening,
                "high": round(max(opening, close) + padding, 5),
                "low": round(min(opening, close) - padding, 5),
                "close": close,
                "tick_volume": 150 + index % 20,
                "real_volume": 0,
                "spread": 12,
            }
        )
    return pd.DataFrame(rows)


class EngineeredSignalMarket(MockMarketData):
    source_kind = SourceKind.SYNTHETIC

    def __init__(self, *args, sign=1, phase=2.6, **kwargs):
        super().__init__(*args, **kwargs)
        self.sign, self.phase = sign, phase
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}
        self.bad_symbol: str | None = None
        self.reads = 0

    async def get_candles(self, symbol, timeframe, count=300, *, as_of=None):
        await self.get_symbol_info(symbol)
        self.reads += 1
        if symbol == self.bad_symbol:
            raise BrokerError("TEST read fault password=NOT_A_REAL_SECRET")
        if (symbol, timeframe) in self.frames:
            return self.frames[symbol, timeframe].copy(deep=True)
        return engineered_bars(timeframe, count, as_of or self.clock.now(), sign=self.sign, phase=self.phase)

    async def get_tick(self, symbol):
        await self.get_symbol_info(symbol)
        if symbol in self._overrides:
            return self._overrides[symbol]
        frame = await self.get_candles(symbol, self.settings.primary_timeframe, 1, as_of=self.clock.now())
        bid = Decimal(str(frame.iloc[-1]["close"]))
        return Tick(symbol, bid, bid + Decimal("0.00012"), self.clock.now())
