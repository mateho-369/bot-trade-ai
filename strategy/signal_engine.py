"""Read-only analysis → immutable pending Signal → bound AI/news review.

No provider/network/trading daemon is created here. Call ExecutionEngine's
execute_signal explicitly after a finalized review; risk/owner/stage vetoes apply.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from core.database import Database
from core.settings import OperatingMode, Settings
from strategy.base_strategy import (
    AIEntryReview,
    EntryReviewer,
    FeatureBundle,
    SignalResult,
    TechnicalDecision,
)
from strategy.feature_engine import FeatureEngine
from strategy.rule_fallback import rule_fallback_review
from strategy.signal_store import SignalStore
from strategy.strategy_router import StrategyRouter
from strategy.volatility_filter import VolatilityFilter
from trading.ai_controls import fallback_mode
from trading.price_rules import snap
from trading.risk_types import NewsWindow, RuntimeProfile
from trading.simulation import SimulatedBroker
from trading.symbol_manager import SymbolManager
from trading.types import BrokerError, MarketData, Side, SourceKind, TradingDisabled


class SignalEngine:
    def __init__(
        self,
        market: MarketData,
        database: Database,
        settings: Settings,
        *,
        profile: RuntimeProfile | None = None,
        router: StrategyRouter | None = None,
    ):
        # Never call a paper broker's mutating get_positions/get_account_info for signals.
        self.market = market.market if isinstance(market, SimulatedBroker) else market
        self.database, self.settings, self.clock = database, settings, self.market.clock
        if self.market.source_kind == SourceKind.HISTORICAL and settings.mode != OperatingMode.BACKTEST:
            raise TradingDisabled("historical signals are BACKTEST-only")
        self.profile = profile or RuntimeProfile.current(settings, self.market.source_kind)
        if self.profile.data_source != self.market.source_kind:
            raise TradingDisabled("signal profile cannot relabel actual market provenance")
        if (
            self.market.settings.safety_fingerprint() != settings.safety_fingerprint()
            or database.settings.safety_fingerprint() != settings.safety_fingerprint()
        ):
            raise TradingDisabled("signal market/database configuration diverges")
        self.router = router or StrategyRouter(settings)
        if self.router.settings.safety_fingerprint() != settings.safety_fingerprint():
            raise TradingDisabled("strategy router configuration diverges")
        self.features = FeatureEngine(settings)
        self.volatility = VolatilityFilter(settings, self.clock)
        self.store = SignalStore(database, settings, self.clock, self.profile)
        self.symbols = SymbolManager(self.market, settings)
        self._initialized, self._init_lock = False, asyncio.Lock()
        self._review_slots = asyncio.Semaphore(settings.ai_max_concurrent)

    async def initialize(self) -> None:
        """Caller must explicitly initialize its market/broker first; no hidden login."""
        async with self._init_lock:
            await asyncio.to_thread(self.database.verify_schema)
            await self.symbols.initialize()
            self._initialized = True

    def _ready(self):
        if not self._initialized:
            raise TradingDisabled("initialize the read-only signal service first")

    def _technical(self, bundle: FeatureBundle) -> tuple[TechnicalDecision, Decimal | None, tuple[str, ...]]:
        technical = self.router.evaluate(bundle)
        quality = self.volatility.evaluate(bundle)
        reasons = list(quality.reasons)
        stop = None
        if technical.side is not None:
            reasons.extend(self.volatility.entry_distance(bundle, technical.side).reasons)
            if not reasons:
                side, primary, meta = technical.side, bundle.primary, bundle.info
                atr = Decimal(str(primary.value("atr")))
                # OHLC is bid-based. SELL reference/candidate includes current executable ask.
                distance = max(
                    atr * self.settings.strategy_stop_atr_multiplier,
                    (max(meta.stops_level, meta.freeze_level) + 1) * meta.point,
                    meta.tick_size,
                )
                buffer = atr * Decimal("0.1")
                if side == Side.BUY:
                    stop = min(bundle.tick.bid - distance, Decimal(str(primary.value("swing_low"))) - buffer)
                else:
                    stop = max(
                        bundle.tick.ask + distance,
                        Decimal(str(primary.value("swing_high")))
                        + (bundle.tick.ask - bundle.tick.bid)
                        + buffer,
                    )
                # Round OUTWARD so protection is not made deceptively tighter than structure.
                stop = snap(stop, meta.tick_size, up=side == Side.SELL)
                if stop <= 0:
                    reasons.append("nonpositive_structural_stop")
                    stop = None
        return technical, stop, tuple(dict.fromkeys(reasons))

    async def analyze(self, logical_symbol: str) -> SignalResult:
        self._ready()
        try:
            managed = self.symbols.resolve(logical_symbol)
        except BrokerError:
            return await asyncio.to_thread(
                self.store.data_failure, logical_symbol, "disabled_or_unavailable_symbol"
            )
        try:
            info = await self.market.get_symbol_info(managed.native)
            cutoff = self.clock.now()
            frames = {}
            # One MT5 worker serializes these calls. No recursive broker calls under its worker lock.
            for timeframe in dict.fromkeys(
                (
                    self.settings.primary_timeframe,
                    self.settings.higher_timeframe,
                    self.settings.trend_timeframe,
                )
            ):
                frames[timeframe] = await self.market.get_candles(
                    managed.native, timeframe, self.settings.candle_lookback, as_of=cutoff
                )
            tick = await self.market.get_tick(managed.native)
            observed = self.clock.now()
            if (observed - cutoff).total_seconds() > self.settings.order_max_age_seconds:
                raise BrokerError("analysis read deadline exceeded")
            bundle = await asyncio.to_thread(
                self.features.build,
                frames,
                logical_symbol=logical_symbol,
                info=info,
                tick=tick,
                source=self.market.source_kind,
                observed_at=observed,
            )
            technical, stop, reasons = await asyncio.to_thread(self._technical, bundle)
        except asyncio.CancelledError:
            raise
        except (BrokerError, ValueError, TypeError, KeyError, OverflowError):
            # No raw exception/provider body/credentials and no fabricated bar time.
            return await asyncio.to_thread(
                self.store.data_failure, managed.native, "market_data_or_features_unavailable"
            )
        return await asyncio.to_thread(self.store.record, bundle, technical, stop_price=stop, vetoes=reasons)

    async def get(self, signal_id: int) -> SignalResult:
        self._ready()
        return await asyncio.to_thread(self.store.get, signal_id)

    async def finalize(
        self, signal_id: int, *, review: AIEntryReview | None = None, news: NewsWindow | None = None
    ) -> SignalResult:
        self._ready()
        return await asyncio.to_thread(
            self.store.finalize, signal_id, review=review, news=news if news is not None else NewsWindow()
        )

    async def evaluate(
        self, logical_symbol: str, *, reviewer: EntryReviewer | None = None, news: NewsWindow | None = None
    ) -> SignalResult:
        """Convenience path. Unsafe/unknown news or an absent reviewer is a final veto.

        A reviewer TIMEOUT uses the deterministic rule-based fallback when policy permits (see
        strategy.rule_fallback); provider failures inside AISupervisor are handled there. Any other
        reviewer exception remains a fail-closed veto. Finalization re-verifies every review.
        """
        news = news if news is not None else NewsWindow()
        proposal = await self.analyze(logical_symbol)
        if proposal.state != "pending":
            return proposal
        review = None
        if reviewer is not None and self.store.news.evaluate(logical_symbol, news).allowed:
            try:
                async with self._review_slots:
                    async with asyncio.timeout(self.settings.ai_timeout_seconds):
                        review = await reviewer.review(proposal, news)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                review = await self._timeout_fallback(proposal, news)
            except Exception:
                await asyncio.to_thread(
                    self.database.audit,
                    "signal.ai_review_unavailable",
                    "signals",
                    {"signal_id": proposal.signal_id, "reason": "provider_error"},
                )
        return await self.finalize(proposal.signal_id, review=review, news=news)

    async def _timeout_fallback(self, proposal: SignalResult, news: NewsWindow):
        # An interrupted review cannot prove an enabled learning filter passed: stay vetoed then.
        review = None
        if not self.settings.model_filter_enabled:
            review = rule_fallback_review(
                proposal,
                news,
                settings=self.settings,
                profile=self.profile,
                now=self.clock.now(),
                fallback_mode=fallback_mode(self.database, self.settings),
            )
        await asyncio.to_thread(
            self.database.audit,
            "signal.ai_review_unavailable",
            "signals",
            {
                "signal_id": proposal.signal_id,
                "reason": "provider_timeout",
                "rule_fallback": review is not None,
                "decision": review.decision if review is not None else None,
            },
        )
        return review

    async def evaluate_many(
        self, *, reviewer: EntryReviewer | None = None, news_by_symbol: dict[str, NewsWindow] | None = None
    ) -> tuple[SignalResult, ...]:
        self._ready()
        reviews = news_by_symbol or {}
        # Bounded symbols (≤30) and review concurrency (≤4); errors don't become opportunities.
        return tuple(
            await asyncio.gather(
                *(
                    self.evaluate(symbol, reviewer=reviewer, news=reviews.get(symbol, NewsWindow()))
                    for symbol in self.settings.symbols
                )
            )
        )
