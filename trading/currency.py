"""Bid/ask-aware cash conversion.

Cent accounts (Exness ``USC``/``EUC``) are an EXACT fixed-ratio denomination of
their parent currency: 100 USC == 1 USD, 100 EUC == 1 EUR. That ratio is applied
without a spread; any further parent-currency leg still needs a verified quote.
USDC (a stablecoin) or any other code is never relabelled as USD.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.settings import Settings
from trading.types import ZERO, Clock, MarketData, RiskViolation, SymbolInfo, Tick

CENT_FACTOR = Decimal("100")
# Account currency -> parent currency. Only these exact codes are cent accounts.
CENT_CURRENCIES: dict[str, str] = {"USC": "USD", "EUC": "EUR"}


def cent_parent(currency: str) -> str | None:
    """Parent currency of a cent-account code (USC -> USD), else None."""
    return CENT_CURRENCIES.get(currency)


def is_cent_currency(currency: str) -> bool:
    return currency in CENT_CURRENCIES


def denomination(currency: str) -> tuple[str, Decimal]:
    """(parent currency, units per parent unit): USC -> (USD, 100), USD -> (USD, 1)."""
    parent = CENT_CURRENCIES.get(currency)
    return (parent, CENT_FACTOR) if parent else (currency, Decimal("1"))


def account_currency_profile(currency: str) -> dict[str, object]:
    """Owner-facing description; amounts in a cent account are 1/100 of the parent."""
    parent, factor = denomination(currency)
    return {
        "account_currency": currency,
        "cent_account": factor != 1,
        "parent_currency": parent,
        "units_per_parent": str(factor),
    }


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
        # Exact cent denominations first: USC -> USD is /100, never a quote.
        parent = cent_parent(source)
        if parent is not None:
            return await self.convert(amount / CENT_FACTOR, parent, target)
        parent = cent_parent(target)
        if parent is not None:
            return (await self.convert(amount, source, parent)) * CENT_FACTOR
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
