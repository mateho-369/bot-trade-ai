"""Bid/ask-aware cash conversion. No silent cent-account/USDC/USD relabeling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.settings import Settings
from trading.types import ZERO, Clock, MarketData, RiskViolation, SymbolInfo, Tick


@dataclass(frozen=True, slots=True)
class ConversionQuote:
    symbol: SymbolInfo
    tick: Tick

    def convert(self, amount: Decimal, source: str, target: str) -> Decimal:
        if source == target:
            return amount
        base, quote = self.symbol.currency_base, self.symbol.currency_profit
        if (source, target) == (base, quote):
            # Asset proceeds sell at bid; funding a liability buys at ask.
            return amount * (self.tick.bid if amount >= ZERO else self.tick.ask)
        if (source, target) == (quote, base):
            return amount / (self.tick.ask if amount >= ZERO else self.tick.bid)
        raise RiskViolation("configured conversion symbol has the wrong currency pair")


class CurrencyConverter:
    def __init__(self, market: MarketData, settings: Settings, clock: Clock | None = None) -> None:
        self.market, self.settings = market, settings
        self.clock = clock or market.clock

    async def convert(self, amount: Decimal, source: str, target: str) -> Decimal:
        if not isinstance(amount, Decimal) or not amount.is_finite():
            raise RiskViolation("invalid cash conversion amount")
        if source == target:
            return amount
        if "USD" not in (source, target):
            # Two verified legs; conservatively includes each conversion spread.
            dollars = await self.convert(amount, source, "USD")
            return await self.convert(dollars, "USD", target)
        currency = target if source == "USD" else source
        name = self.settings.account_to_usd_symbols.get(currency)
        if not name:
            raise RiskViolation("non-USD currency requires an explicit conversion-symbol mapping")
        meta = await self.market.get_symbol_info(name)
        tick = await self.market.get_tick(name)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        return ConversionQuote(meta, tick).convert(amount, source, target)

    async def to_usd(self, amount: Decimal) -> Decimal:
        account = await self.market.get_account_info()
        return await self.convert(amount, account.currency, "USD")

    async def usd_to_account(self, amount: Decimal) -> Decimal:
        account = await self.market.get_account_info()
        return await self.convert(amount, "USD", account.currency)
