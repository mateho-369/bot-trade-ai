"""Causal file-backed MarketData, never MT5. Futures and mutable frames are not exposed."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from backtesting.dataset import Bar, HistoricalDataset
from core.settings import TIMEFRAME_MINUTES, OperatingMode, Settings
from trading.currency import CurrencyConverter
from trading.types import (
    ZERO,
    AccountInfo,
    AccountKind,
    BrokerError,
    ManualClock,
    Side,
    SourceKind,
    Tick,
    TradingDisabled,
)


class HistoricalMarket:
    source_kind = SourceKind.HISTORICAL

    def __init__(self, dataset: HistoricalDataset, settings: Settings, clock: ManualClock):
        if settings.mode != OperatingMode.BACKTEST or not isinstance(clock, ManualClock):
            raise TradingDisabled("historical replay requires BACKTEST and a private simulation clock")
        if settings.account_currency != dataset.manifest.account_currency:
            raise TradingDisabled("historical account currency mismatch")
        self.dataset, self.settings, self.clock = dataset, settings, clock
        self._specs = {item.name: item for item in dataset.manifest.symbols}
        self._quotes, self._frames = {}, {}
        self._batches = defaultdict(list)
        for bars in dataset.bars.values():
            for bar in bars:
                self._batches[bar.available_at].append(("bar_close", bar))
                if dataset.manifest.quote_mode == "ohlc_conservative":
                    self._batches[bar.open_available_at].append(("bar_open", bar))
        for quotes in dataset.quotes.values():
            for quote in quotes:
                self._batches[quote.available_at].append(("quote", quote.tick))
        self.event_times = tuple(sorted(self._batches))
        self._cursor = 0
        self.current_batch_time = None
        self.current_open_symbols = frozenset()
        self._initialized = False
        self._converter = CurrencyConverter(self, settings, clock)
        self._source_key = dataset.dataset_sha256[:32]

    async def initialize(self):
        self._initialized = True
        return True

    async def shutdown(self):
        self._initialized = False

    def _ready(self):
        if not self._initialized:
            raise BrokerError("initialize historical market first")

    def seed(self):
        """Bootstrap prices known by replay start. No fills or trades exist during warmup."""
        while self._cursor < len(self.event_times) and self.event_times[self._cursor] <= self.clock.now():
            _, opening = self.begin_batch(self.event_times[self._cursor])
            self.finish_batch(opening)

    def begin_batch(self, when: datetime) -> tuple[tuple[Bar, ...], tuple[Bar, ...]]:
        if when > self.clock.now() or self._cursor >= len(self.event_times):
            raise BrokerError("future or exhausted historical batch")
        if self.event_times[self._cursor] != when:
            raise BrokerError("historical batch skipped or replayed")
        self._cursor += 1
        self.current_batch_time = when
        self.current_open_symbols = frozenset()
        closing, opening = [], []
        batch = self._batches[when]
        if self.dataset.manifest.quote_mode == "ticks":
            # All conversion legs at this timestamp become visible before any valuation.
            for role, item in batch:
                if role == "quote":
                    self._quotes[item.symbol] = item
        else:
            for role, bar in batch:
                if role == "bar_close":
                    point = self._specs[bar.symbol].point
                    self._quotes[bar.symbol] = Tick(
                        bar.symbol, bar.close, bar.close + bar.spread_max_points * point, when
                    )
                    closing.append(bar)
                elif role == "bar_open":
                    opening.append(bar)
        return tuple(closing), tuple(opening)

    def finish_batch(self, opening: tuple[Bar, ...]):
        # Bar-close exits are resolved BEFORE a new bar's opening quote/entry is allowed.
        self.current_open_symbols = frozenset(bar.symbol for bar in opening)
        for bar in opening:
            point = self._specs[bar.symbol].point
            self._quotes[bar.symbol] = Tick(
                bar.symbol, bar.open, bar.open + bar.spread_open_points * point, bar.open_available_at
            )

    async def get_account_info(self):
        self._ready()
        capital = self.settings.paper_initial_balance
        return AccountInfo(
            1,
            "local-history:" + self._source_key,
            self.settings.account_currency,
            AccountKind.SIMULATED,
            self.source_kind,
            capital,
            capital,
            ZERO,
            capital,
            leverage=self.settings.mock_leverage,
            trade_allowed=False,
            trade_expert=False,
        )

    async def get_symbols(self):
        self._ready()
        return tuple(self._specs)

    async def select_symbol(self, symbol):
        return await self.get_symbol_info(symbol)

    async def get_symbol_info(self, symbol):
        self._ready()
        spec = self._specs.get(symbol)
        if spec is None or self.clock.now() < spec.effective_from:
            raise BrokerError("historical contract unavailable at this time")
        return spec.broker_info()

    async def get_tick(self, symbol):
        self._ready()
        tick = self._quotes.get(symbol)
        if tick is None or tick.time > self.clock.now():
            raise BrokerError("no causally observed historical quote")
        # DO NOT restamp an old tick. Core freshness/FX gates must see its actual timestamp.
        return tick

    def _aggregate(self, symbol, timeframe):
        minutes = TIMEFRAME_MINUTES[timeframe]
        groups = defaultdict(list)
        for bar in self.dataset.bars[symbol]:
            anchor = pd.Timestamp(bar.time).floor(f"{minutes}min").to_pydatetime()
            groups[anchor].append(bar)
        rows = []
        for start, bars in sorted(groups.items()):
            # Never complete a higher timeframe using partial/missing M1 rows.
            if len(bars) != minutes or any(
                bar.time != start + timedelta(minutes=i) for i, bar in enumerate(bars)
            ):
                continue
            rows.append(
                {
                    "time": start,
                    "close_time": start + timedelta(minutes=minutes),
                    "available_at": max(bar.available_at for bar in bars),
                    "open": float(bars[0].open),
                    "high": float(max(bar.high for bar in bars)),
                    "low": float(min(bar.low for bar in bars)),
                    "close": float(bars[-1].close),
                    "tick_volume": sum(bar.tick_volume for bar in bars),
                    "spread": float(max(bar.spread_max_points for bar in bars)),
                    "real_volume": float(sum((bar.real_volume for bar in bars), ZERO)),
                }
            )
        return pd.DataFrame(
            rows,
            columns=(
                "time",
                "close_time",
                "available_at",
                "open",
                "high",
                "low",
                "close",
                "tick_volume",
                "spread",
                "real_volume",
            ),
        )

    async def get_candles(self, symbol, timeframe, count=500, *, as_of=None):
        self._ready()
        if symbol not in self._specs or timeframe not in TIMEFRAME_MINUTES:
            raise BrokerError("unsupported historical symbol/timeframe")
        if type(count) is not int or not 1 <= count <= 10000:
            raise BrokerError("bounded candle request required")
        cutoff = as_of or self.clock.now()
        if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff > self.clock.now():
            raise BrokerError("future/naive historical candle request forbidden")
        key = symbol, timeframe
        if key not in self._frames:
            self._frames[key] = self._aggregate(symbol, timeframe)
        frame = self._frames[key]
        if frame.empty:
            raise BrokerError("no complete higher-timeframe history")
        selected = frame.loc[(frame["close_time"] <= cutoff) & (frame["available_at"] <= cutoff)]
        if selected.empty:
            raise BrokerError("no historical bars available at this cutoff")
        return selected.drop(columns=["available_at"]).tail(count).copy(deep=True).reset_index(drop=True)

    async def calculate_profit(self, symbol, side, volume, entry, exit_price):
        spec = self._specs.get(symbol)
        if spec is None or not isinstance(side, Side):
            raise BrokerError("unsupported historical profit contract")
        if any(
            not isinstance(v, Decimal) or not v.is_finite() or v <= ZERO for v in (volume, entry, exit_price)
        ):
            raise BrokerError("positive exact historical prices/volume required")
        profit = side.sign * (exit_price - entry) * spec.contract_size * volume
        return await self._converter.convert(profit, spec.currency_profit, self.settings.account_currency)

    async def calculate_margin(self, symbol, side, volume, entry):
        spec = self._specs.get(symbol)
        if spec is None or not isinstance(side, Side) or volume <= ZERO or entry <= ZERO:
            raise BrokerError("unsupported historical margin contract")
        units = spec.contract_size * volume / Decimal(self.settings.mock_leverage)
        currency = spec.currency_base
        if spec.margin_model == "notional":
            units *= entry
            currency = spec.currency_profit
        # A liability conversion uses the conservative funding side of bid/ask.
        return -(await self._converter.convert(-units, currency, self.settings.account_currency))
