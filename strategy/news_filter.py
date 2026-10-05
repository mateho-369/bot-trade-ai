"""Fresh per-symbol headline AND complete-calendar proof; native requires latest SQL epoch."""

from core.settings import OperatingMode, Settings
from strategy.volatility_filter import FilterDecision
from trading.risk_types import NewsWindow
from trading.types import Clock, SourceKind


class NewsFilter:
    def __init__(self, settings: Settings, clock: Clock, database=None, profile=None):
        self.settings, self.clock, self.database, self.profile = settings, clock, database, profile

    def evaluate(self, logical_symbol: str, news: NewsWindow, *, session=None) -> FilterDecision:
        if (
            self.profile is not None
            and self.profile.data_source == SourceKind.HISTORICAL
            and self.settings.mode != OperatingMode.BACKTEST
        ):
            return FilterDecision(False, ("historical_news_backtest_scope",))
        if logical_symbol not in self.settings.symbols or not self.settings.symbol_news_currencies.get(
            logical_symbol
        ):
            return FilterDecision(False, ("unknown_news_exposure",))
        if not isinstance(news, NewsWindow):
            return FilterDecision(False, ("invalid_news_review",))
        # More conservative than the risk flag: this real-time signal publisher
        # never manufactures approved AI/news reviews, even in provider-free paper.
        if not news.allows(self.settings, self.clock.now(), logical_symbol):
            return FilterDecision(False, ("unknown_stale_or_unsafe_news",))
        if news.managed or self.profile is not None and self.profile.data_source == SourceKind.MT5:
            if self.database is None or self.profile is None:
                return FilterDecision(False, ("managed_news_requires_durable_scope",))
            from news.evidence import verify_window

            if session is not None:
                valid = verify_window(
                    session,
                    news,
                    logical_symbol=logical_symbol,
                    settings=self.settings,
                    profile=self.profile,
                    now=self.clock.now(),
                )
            else:
                with self.database.session() as reader:
                    valid = verify_window(
                        reader,
                        news,
                        logical_symbol=logical_symbol,
                        settings=self.settings,
                        profile=self.profile,
                        now=self.clock.now(),
                    )
            if not valid:
                return FilterDecision(False, ("unbound_revoked_or_fixture_news",))
        return FilterDecision(True, ())
