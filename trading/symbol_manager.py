"""Resolve explicit logical/native names and validate available broker metadata."""

from dataclasses import dataclass

from core.settings import Settings
from trading.types import MarketData, SymbolInfo, UnsupportedSymbol


@dataclass(frozen=True, slots=True)
class ManagedSymbol:
    logical: str
    native: str
    info: SymbolInfo
    news_currencies: tuple[str, ...]
    spread_limit_points: int


class SymbolManager:
    def __init__(self, market: MarketData, settings: Settings) -> None:
        self.market, self.settings = market, settings
        self._symbols: dict[str, ManagedSymbol] = {}
        self._errors: dict[str, str] = {}

    async def initialize(self) -> None:
        found: dict[str, ManagedSymbol] = {}
        errors: dict[str, str] = {}
        native_names = set()
        for logical in self.settings.symbols:
            native = self.settings.symbol_aliases.get(logical, logical)
            try:
                info = await self.market.select_symbol(native)
                if info.name != native or native in native_names:
                    raise UnsupportedSymbol("broker alias mismatch or duplicate actual symbol")
                native_names.add(native)
                found[logical] = ManagedSymbol(
                    logical,
                    native,
                    info,
                    self.settings.symbol_news_currencies.get(logical, ()),
                    self.settings.symbol_spread_limits.get(
                        logical,
                        self.settings.symbol_spread_limits.get(native, self.settings.max_spread_points),
                    ),
                )
            except UnsupportedSymbol:
                errors[logical] = "unavailable/invalid broker metadata; configure an explicit alias"
        self._symbols, self._errors = found, errors

    def enabled(self) -> tuple[ManagedSymbol, ...]:
        return tuple(self._symbols.values())

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def resolve(self, logical: str) -> ManagedSymbol:
        if logical not in self._symbols:
            raise UnsupportedSymbol("logical symbol is disabled/unavailable")
        return self._symbols[logical]

    def news_exposure_known(self, logical: str) -> bool:
        # Unknown exposure is never guessed safe. NewsFilter in Part 8 vetoes it.
        return bool(self.resolve(logical).news_currencies)
