"""Reuse durable simulation; OHLC adds pessimistic server-stop resolution, not a guessed tick path."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from zoneinfo import ZoneInfo

from backtesting.dataset import Bar
from backtesting.market import HistoricalMarket
from core.settings import OperatingMode
from trading.simulation import SimulatedBroker
from trading.types import ZERO, Side, SourceKind, TradingDisabled


class ReplayBroker(SimulatedBroker):
    def __init__(self, market: HistoricalMarket, settings, *, ledger_id):
        if settings.mode != OperatingMode.BACKTEST or market.source_kind != SourceKind.HISTORICAL:
            raise TradingDisabled("ReplayBroker is BACKTEST-only; no native/paper credential execution")
        super().__init__(market, settings, source_kind=SourceKind.PAPER, ledger_id=ledger_id)
        self.ohlc_ambiguities = 0
        self.ohlc_resolved_exits = 0
        self.resolutions = []

    async def resolve_closed_bars(self, bars: tuple[Bar, ...]):
        if self.market.dataset.manifest.quote_mode != "ohlc_conservative" or not bars:
            return
        async with self._lock:
            self._ready()
            if self._quarantined or self._persistence_failed:
                raise TradingDisabled("quarantined replay cannot resolve another bar")
            # Preserve the pre-gap/pre-swap day baseline BEFORE any midnight range exit.
            day = self.clock.now().astimezone(ZoneInfo(self.settings.trading_day_timezone)).date()
            if day != self._day:
                self._day, self._day_start = day, self._account().equity
                self._entries_today, self._daily_latched = 0, False
            mapping = {bar.symbol: bar for bar in bars}
            for position in tuple(self._positions.values()):
                bar = mapping.get(position.symbol)
                if bar is None or position.time > bar.time or bar.available_at > self.clock.now():
                    continue  # A newly entered position NEVER sees the range before it existed.
                point = (await self.market.get_symbol_info(position.symbol)).point
                if position.side == Side.BUY:
                    stop = position.sl > ZERO and bar.low <= position.sl
                    target = position.tp > ZERO and bar.high >= position.tp
                    stop_reference = min(position.sl, bar.open)
                else:
                    # Max spread is intentionally pessimistic for sell SL/TP checks.
                    high_ask = bar.high + point * bar.spread_max_points
                    low_ask = bar.low + point * bar.spread_max_points
                    open_ask = bar.open + point * bar.spread_open_points
                    stop = position.sl > ZERO and high_ask >= position.sl
                    target = position.tp > ZERO and low_ask <= position.tp
                    stop_reference = max(position.sl, open_ask)
                if not (stop or target):
                    continue
                days = Decimal(str((self.clock.now() - position.time).total_seconds())) / Decimal("86400")
                swap_usd = (
                    position.volume * self.settings.estimated_swap_usd_per_lot_per_day * max(ZERO, days)
                )
                swap = await self._currency.convert(-swap_usd, "USD", self.settings.account_currency)
                position = replace(position, swap=swap)
                ambiguous = bool(stop and target)
                self.ohlc_ambiguities += int(ambiguous)
                self.ohlc_resolved_exits += 1
                reason = "ohlc_stop_first" if ambiguous else ("ohlc_sl" if stop else "ohlc_tp")
                result = await self._close_at(position, stop_reference if stop else position.tp, reason)
                self.resolutions.append(
                    {
                        "position_identifier": position.identifier,
                        "bar_time": bar.time.isoformat(),
                        "resolved_at": self.clock.now().isoformat(),
                        "reason": reason,
                        "price": str(result.filled_price),
                        "exact_fill_time_known": False,
                    }
                )
