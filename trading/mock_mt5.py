"""Deterministic synthetic market + simulated execution; no MT5 import/network."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from core.settings import TIMEFRAME_MINUTES, Settings
from trading.candles import validated_candles
from trading.currency import CurrencyConverter
from trading.simulation import SimulatedBroker
from trading.types import (
    ZERO,
    AccountInfo,
    AccountKind,
    BrokerError,
    Clock,
    Side,
    SourceKind,
    SymbolInfo,
    SystemClock,
    Tick,
    UnsupportedSymbol,
    aware_utc,
)

D = Decimal


def synthetic_catalogue() -> tuple[dict[str, SymbolInfo], dict[str, tuple[Decimal, Decimal]]]:
    # EXAMPLES ONLY. Never use this table as broker contract specifications.
    definitions = {
        "XAUUSD": ("0.01", "100", "2610.00", "0.20", "XAU", "USD", "0.01", 2),
        "BTCUSD": ("0.01", "1", "60000.00", "20.00", "BTC", "USD", "0.01", 2),
        "ETHUSD": ("0.01", "1", "3000.00", "2.00", "ETH", "USD", "0.01", 2),
        "EURUSD": ("0.00001", "100000", "1.10000", "0.00012", "EUR", "USD", "0.01", 5),
        "GBPUSD": ("0.00001", "100000", "1.25000", "0.00012", "GBP", "USD", "0.01", 5),
        "USDJPY": ("0.001", "100000", "150.000", "0.020", "USD", "JPY", "0.01", 3),
        "US30": ("0.1", "1", "40000.0", "1.0", "USD", "USD", "0.1", 1),
        "NAS100": ("0.1", "1", "18000.0", "1.0", "USD", "USD", "0.1", 1),
    }
    symbols, quotes = {}, {}
    for name, (point, contract, bid, spread, base, quote, minimum, digits) in definitions.items():
        symbols[name] = SymbolInfo(
            name,
            D(point),
            D(point),
            D(point) * D(contract),
            D(point) * D(contract),
            D(contract),
            D(minimum),
            D("100"),
            D(minimum),
            digits,
            base,
            quote,
            stops_level=10,
        )
        quotes[name] = (D(bid), D(bid) + D(spread))
    return symbols, quotes


class MockMarketData:
    source_kind = SourceKind.SYNTHETIC

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        symbols: dict[str, SymbolInfo] | None = None,
        quotes: dict[str, tuple[Decimal, Decimal]] | None = None,
    ) -> None:
        self.settings, self.clock = settings, clock or SystemClock()
        self._symbols, self._quotes = synthetic_catalogue()
        self._symbols.update(symbols or {})
        self._quotes.update(quotes or {})
        for logical, alias in settings.symbol_aliases.items():
            if logical in self._symbols and alias not in self._symbols:
                self._symbols[alias] = replace(self._symbols[logical], name=alias)
                self._quotes[alias] = self._quotes[logical]
        self._overrides: dict[str, Tick] = {}
        self._initialized = False
        self._converter = CurrencyConverter(self, settings)

    async def initialize(self) -> None:
        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False

    def _ready(self) -> None:
        if not self._initialized:
            raise BrokerError("mock market is not initialized")

    async def get_account_info(self) -> AccountInfo:
        self._ready()
        balance = self.settings.paper_initial_balance
        return AccountInfo(
            0,
            "synthetic-valuation-only",
            self.settings.account_currency,
            AccountKind.SIMULATED,
            SourceKind.SYNTHETIC,
            balance,
            balance,
            ZERO,
            balance,
            leverage=self.settings.mock_leverage,
        )

    async def get_symbols(self) -> tuple[str, ...]:
        self._ready()
        return tuple(self._symbols)

    async def select_symbol(self, symbol: str) -> SymbolInfo:
        return await self.get_symbol_info(symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        self._ready()
        if symbol not in self._symbols:
            raise UnsupportedSymbol("unknown synthetic symbol; supply explicit test metadata/quotes")
        meta = self._symbols[symbol]
        if meta.currency_profit != self.settings.account_currency:
            # Unknown snapshot tick values are zero, never mislabeled quote
            # currency as account currency. Native-style profit valuation uses
            # the explicit FX conversion path instead of tick-value shortcuts.
            return replace(meta, tick_value_profit=ZERO, tick_value_loss=ZERO)
        return meta

    async def get_tick(self, symbol: str) -> Tick:
        await self.get_symbol_info(symbol)
        if symbol in self._overrides:
            return self._overrides[symbol]
        if symbol not in self._quotes:
            raise UnsupportedSymbol("synthetic symbol has no configured quote")
        bid, ask = self._quotes[symbol]
        return Tick(symbol, bid, ask, self.clock.now())

    async def set_tick(
        self, symbol: str, bid: Decimal, ask: Decimal, *, timestamp: datetime | None = None
    ) -> None:
        meta = await self.get_symbol_info(symbol)
        if bid % meta.tick_size or ask % meta.tick_size:
            raise BrokerError("synthetic quote is off the tick grid")
        self._overrides[symbol] = Tick(symbol, bid, ask, timestamp or self.clock.now())

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 300, *, as_of: datetime | None = None
    ) -> pd.DataFrame:
        meta = await self.get_symbol_info(symbol)
        if timeframe not in TIMEFRAME_MINUTES or not 1 <= count <= 10000:
            raise BrokerError("invalid synthetic timeframe/count")
        cutoff = aware_utc(as_of or self.clock.now())
        if cutoff > self.clock.now():
            raise BrokerError("synthetic data cannot peek beyond its clock")
        seconds = TIMEFRAME_MINUTES[timeframe] * 60
        last_close = int(cutoff.timestamp()) // seconds * seconds
        anchor = self._quotes[symbol][0]
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 1000
        quantum = Decimal(1).scaleb(-meta.digits)
        amplitude = min(anchor / D("100"), meta.point * 80)

        # Prices are a function of ABSOLUTE bar index, never current/future quote.
        def price(index: int) -> Decimal:
            value = anchor + amplitude * D(
                str(math.sin((index + seed) / 7) + 0.3 * math.cos((index + seed) / 19))
            )
            return value.quantize(quantum)

        rows = []
        for offset in range(count):
            start = last_close - (count - offset) * seconds
            index = start // seconds
            opening, closing = price(index), price(index + 1)
            rows.append(
                {
                    "time": datetime.fromtimestamp(start, cutoff.tzinfo),
                    "open": float(opening),
                    "high": float(max(opening, closing) + meta.point * 5),
                    "low": float(min(opening, closing) - meta.point * 5),
                    "close": float(closing),
                    "tick_volume": 100 + (index + seed) % 100,
                    "spread": int((self._quotes[symbol][1] - anchor) / meta.point),
                    "real_volume": 0,
                }
            )
        return validated_candles(pd.DataFrame(rows), timeframe, cutoff)

    async def calculate_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal:
        meta = await self.get_symbol_info(symbol)
        if min(volume, entry, exit_price) <= ZERO:
            raise BrokerError("invalid mock profit inputs")
        gross_quote = side.sign * (exit_price - entry) * meta.contract_size * volume
        return await self._converter.convert(
            gross_quote, meta.currency_profit, self.settings.account_currency
        )

    async def calculate_margin(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal:
        meta = await self.get_symbol_info(symbol)
        if min(volume, entry) <= ZERO:
            raise BrokerError("invalid mock margin inputs")
        amount = entry * meta.contract_size * volume / self.settings.mock_leverage
        return -(await self._converter.convert(-amount, meta.currency_profit, self.settings.account_currency))


class MockMT5Client(SimulatedBroker):
    """Same async broker API, purely synthetic. Cannot be instantiated as live/demo."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        symbols: dict[str, SymbolInfo] | None = None,
        quotes: dict[str, tuple[Decimal, Decimal]] | None = None,
        ledger_id: str = "mock-main",
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        if settings.mt5_backend != "mock":
            raise BrokerError("MockMT5Client requires MT5_BACKEND=mock")
        market = MockMarketData(settings, clock=clock, symbols=symbols, quotes=quotes)
        super().__init__(
            market,
            settings,
            source_kind=SourceKind.SYNTHETIC,
            ledger_id=ledger_id,
            restored_state=restored_state,
        )

    async def set_tick(
        self, symbol: str, bid: Decimal, ask: Decimal, *, timestamp: datetime | None = None
    ) -> None:
        async with self._lock:
            await self.market.set_tick(symbol, bid, ask, timestamp=timestamp)
            await self._refresh()
