"""Explicit currency exposure + conservative title/ticker mapping; aliases never guessed."""

from __future__ import annotations

import re

from core.settings import Settings
from news.types import Headline

CURRENCIES = {
    "USD": (
        r"\b(?:usd|u\.?s\.? dollar|dollars?|federal reserve|fomc|united states|"
        r"u\.?s\.? cpi|u\.?s\.? payrolls|fed)\b"
    ),
    "EUR": r"\b(?:eur|euro|euros|ecb|european central bank|eurozone)\b",
    "GBP": r"\b(?:gbp|sterling|british pound|bank of england|boe|united kingdom|uk inflation)\b",
    "JPY": r"\b(?:jpy|yen|bank of japan|boj)\b",
    "AUD": r"\b(?:aud|australian dollar|reserve bank of australia|rba)\b",
    "CAD": r"\b(?:cad|canadian dollar|bank of canada)\b",
    "CHF": r"\b(?:chf|swiss franc|swiss national bank)\b",
    "NZD": r"\b(?:nzd|new zealand dollar|rbnz)\b",
    "CNY": r"\b(?:cny|yuan|renminbi|pboc)\b",
}
TOKENS = {
    "BTC": r"\b(?:btc|bitcoin)\b",
    "ETH": r"\b(?:eth|ethereum|ether)\b",
    "XAU": r"\b(?:xau|gold)\b",
    "XAG": r"\b(?:xag|silver)\b",
    "US30": r"\b(?:dow jones|us30)\b",
    "NAS100": r"\b(?:nasdaq|nas100)\b",
}
GLOBAL = r"\b(?:war|invasion|missile|pandemic|global banking crisis|global capital controls)\b"


class ExposureMapper:
    def __init__(self, settings: Settings):
        self.settings = settings

    def affected(self, item: Headline, *, source_symbols: tuple[str, ...] = (), unknown_high=False):
        text = (item.title + " " + item.summary).casefold()
        currencies = set(item.currencies)
        currencies.update(code for code, pattern in CURRENCIES.items() if re.search(pattern, text))
        assets = set(item.instruments)
        assets.update(code for code, pattern in TOKENS.items() if re.search(pattern, text))
        symbols = set()
        for logical in self.settings.symbols:
            if currencies.intersection(self.settings.symbol_news_currencies.get(logical, ())):
                symbols.add(logical)
            if any(logical == asset or logical.startswith(asset) for asset in assets):
                symbols.add(logical)
        if re.search(GLOBAL, text):
            symbols.update(self.settings.symbols)
        if unknown_high and not symbols:
            symbols.update(source_symbols or self.settings.symbols)
        return tuple(sorted(symbols))

    def calendar_symbols(self, currency):
        return tuple(
            name
            for name in self.settings.symbols
            if currency in self.settings.symbol_news_currencies.get(name, ())
        )
