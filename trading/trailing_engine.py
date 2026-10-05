"""Achievable, monotonic net-USD profit locks and closed-bar ATR trailing.

A lock is an ESTIMATE at a nominal stop, not insurance against gaps, FX moves,
fees or rejected broker modifications. Never claim an infeasible earned tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from core.settings import Settings
from trading.order_calculator import OrderCalculator
from trading.price_rules import adverse_price, protection_prices, snap
from trading.risk_types import OwnedTrade, PositionReview
from trading.types import (
    ZERO,
    BrokerCommand,
    BrokerError,
    MarketData,
    Operation,
    Position,
    RiskViolation,
    Side,
    SourceKind,
)


class LockRegressionError(RiskViolation):
    """An attempt to remove or loosen an existing protective lock. Always a hard error."""


def assert_never_loosens(side: Side, current_sl: Decimal, proposed_sl: Decimal) -> None:
    """Monotonic lock invariant shared by the mechanical and AI-adaptive paths.

    A BUY stop may only move UP, a SELL stop only DOWN. Removing a stop (0) is loosening too.
    """
    if not isinstance(proposed_sl, Decimal) or not proposed_sl.is_finite():
        raise LockRegressionError("proposed stop must be a finite Decimal")
    if current_sl > ZERO and (proposed_sl <= ZERO or side.sign * (proposed_sl - current_sl) < ZERO):
        raise LockRegressionError("an existing profit lock can never be removed or loosened")


@dataclass(frozen=True, slots=True)
class TrailingPlan:
    sl: Decimal
    lock_level: float
    estimated_net_at_stop_usd: Decimal
    reason: str


class TrailingEngine:
    def __init__(self, broker: MarketData, settings: Settings, source: SourceKind):
        self.broker, self.settings, self.clock, self.source = broker, settings, broker.clock, source
        self.calculator = OrderCalculator(broker, settings)

    @staticmethod
    def closed_atr(candles: pd.DataFrame, *, period: int = 14) -> Decimal:
        if type(period) is not int or period < 2 or len(candles) < period + 1:
            raise RiskViolation("ATR requires enough finalized observations")
        needed = {"time", "close_time", "high", "low", "close"}
        if (
            not needed.issubset(candles.columns)
            or not candles["close_time"].is_monotonic_increasing
            or candles["close_time"].duplicated().any()
        ):
            raise RiskViolation("ATR candle chronology/schema is invalid")
        highs, lows, closes = (
            [Decimal(str(value)) for value in candles[name]] for name in ("high", "low", "close")
        )
        if any(
            not value.is_finite() or value <= ZERO for values in (highs, lows, closes) for value in values
        ):
            raise RiskViolation("ATR prices are invalid")
        if any(high < low for high, low in zip(highs, lows, strict=True)):
            raise RiskViolation("ATR candle high/low are inverted")
        ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(candles))
        ]
        return sum(ranges[-period:], ZERO) / period

    async def lock_targets_account(self, target_usd: Decimal) -> dict[str, Decimal]:
        """USD target and 30/60/90 lock goals expressed in ACCOUNT currency.

        Locks are solved in net USD; on a cent account (USC) a $2 target is
        exactly 200 USC and the tiers are 60/120/180 USC. Owner-facing only.
        """
        if not isinstance(target_usd, Decimal) or not target_usd.is_finite() or target_usd <= ZERO:
            raise RiskViolation("lock target must be a positive Decimal")
        currency = self.calculator.currency
        result = {"target": await currency.usd_to_account(target_usd)}
        for _, lock in self.settings.trailing_levels:
            goal = target_usd * Decimal(str(lock)) / 100
            result[f"lock_{lock:g}"] = await currency.usd_to_account(goal)
        return result

    async def _offset(self, position: Position, owned: OwnedTrade) -> Decimal:
        if (
            owned.identifier != position.identifier
            or owned.ticket != position.ticket
            or owned.volume != position.volume
            or owned.symbol != position.symbol
            or owned.direction != position.side.value
            or owned.target_usd <= ZERO
        ):
            raise RiskViolation("trailing inputs do not match the reconciled owned fill")
        # Actual paid entry/partial-exit amounts + current remaining swap, NOT
        # an estimated opening fee charged a second time.
        closing_usd = self.settings.commission_round_turn_usd_per_lot * position.volume / 2
        closing = -(await self.calculator.currency.usd_to_account(-closing_usd))
        return owned.entry_costs_account + position.swap - closing

    async def net_at_price(
        self, position: Position, owned: OwnedTrade, price: Decimal, *, stop: bool = True
    ) -> Decimal:
        meta = await self.broker.get_symbol_info(position.symbol)
        points = self.settings.max_slippage_points + (
            self.settings.trailing_spread_buffer_points if stop else 0
        )
        executable = adverse_price(price, position.side, meta, points, entry=False)
        if executable <= ZERO:
            raise RiskViolation("nonpositive stop valuation")
        value = await self.broker.calculate_profit(
            position.symbol, position.side, position.volume, position.entry_price, executable
        )
        return await self.calculator.currency.to_usd(value + await self._offset(position, owned))

    async def plan(
        self, position: Position, owned: OwnedTrade, *, candles: pd.DataFrame | None = None
    ) -> TrailingPlan | None:
        meta, tick = (
            await self.broker.get_symbol_info(position.symbol),
            await self.broker.get_tick(position.symbol),
        )
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        offset = await self._offset(position, owned)
        current_net = await self.net_at_price(position, owned, tick.exit(position.side), stop=False)
        progress = current_net * 100 / owned.target_usd
        candidates = []
        levels = self.settings.trailing_levels
        for trigger, lock in reversed(levels):
            if progress < Decimal(str(trigger)) or lock <= owned.lock_level:
                continue
            goal = owned.target_usd * Decimal(str(lock)) / 100
            try:
                sl = await self.calculator.price_for_profit_usd(
                    position.symbol,
                    position.side,
                    position.volume,
                    position.entry_price,
                    goal,
                    net_offset_account=offset,
                    exit_slippage_points=self.settings.max_slippage_points
                    + self.settings.trailing_spread_buffer_points,
                )
                if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
                    sl = position.sl  # Verify an already-better stop without loosening it.
                command = BrokerCommand(
                    Operation.PROTECT,
                    "1" * 64,
                    self.clock.now(),
                    ticket=position.ticket,
                    position_identifier=position.identifier,
                    sl=sl,
                )
                protection_prices(command, position, meta, tick, self.settings)
                net = await self.net_at_price(position, owned, sl)
                if net >= goal:
                    candidates.append((sl, lock, net, "profit_lock"))
                    break  # Highest earned AND legally achievable tier only.
            except BrokerError:
                continue  # Defer, do not clamp below the promised dollar lock.
        if candles is not None and self.settings.atr_trailing_enabled and current_net > ZERO:
            try:
                closes = pd.to_datetime(candles["close_time"], utc=True)
                if (
                    any(closes > self.clock.now())
                    or (self.clock.now() - closes.iloc[-1].to_pydatetime()).total_seconds()
                    > self.settings.max_candle_age_seconds
                ):
                    raise RiskViolation("ATR must use fresh CLOSED candles only")
                atr = self.closed_atr(candles)
                if atr > ZERO:
                    distance = atr * self.settings.atr_trailing_multiplier
                    sl = snap(
                        tick.exit(position.side) - position.side.sign * distance,
                        meta.tick_size,
                        up=position.side == Side.SELL,
                    )
                    if position.side.sign * (sl - position.sl) > ZERO:
                        command = BrokerCommand(
                            Operation.PROTECT,
                            "2" * 64,
                            self.clock.now(),
                            ticket=position.ticket,
                            position_identifier=position.identifier,
                            sl=sl,
                        )
                        protection_prices(command, position, meta, tick, self.settings)
                        net = await self.net_at_price(position, owned, sl)
                        verified = owned.lock_level
                        for trigger, lock in levels:
                            if (
                                progress >= Decimal(str(trigger))
                                and net >= owned.target_usd * Decimal(str(lock)) / 100
                            ):
                                verified = max(verified, lock)
                        candidates.append((sl, verified, net, "closed_bar_atr"))
            except (BrokerError, KeyError, ValueError, TypeError):
                pass  # ATR unavailable is not permission to loosen a stop.
        if not candidates:
            return None
        sl, level, net, reason = max(candidates, key=lambda row: position.side.sign * row[0])
        if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
            return None
        return TrailingPlan(sl, level, net, reason)

    async def extension(
        self, position: Position, owned: OwnedTrade, review: PositionReview | None
    ) -> Decimal | None:
        if review is None or not review.allows_extension(
            self.settings, self.clock.now(), self.source, position=position
        ):
            return None
        tick, meta = (
            await self.broker.get_tick(position.symbol),
            await self.broker.get_symbol_info(position.symbol),
        )
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        if await self.net_at_price(
            position, owned, tick.exit(position.side), stop=False
        ) < owned.target_usd * Decimal("0.9"):
            return None
        try:
            goal = owned.target_usd * self.settings.tp_extension_factor
            price = await self.calculator.price_for_profit_usd(
                position.symbol,
                position.side,
                position.volume,
                position.entry_price,
                goal,
                net_offset_account=await self._offset(position, owned),
                exit_slippage_points=self.settings.max_slippage_points,
            )
            cap = abs(owned.original_tp - position.entry_price) * self.settings.tp_extension_factor
            if abs(price - position.entry_price) > cap or position.side.sign * (price - position.tp) <= ZERO:
                return None
            command = BrokerCommand(
                Operation.PROTECT,
                "3" * 64,
                self.clock.now(),
                ticket=position.ticket,
                position_identifier=position.identifier,
                tp=price,
            )
            protection_prices(command, position, meta, tick, self.settings)
            return price
        except BrokerError:
            return None

    async def tighten(self, position: Position, owned: OwnedTrade, *, fraction: Decimal) -> Decimal | None:
        """A strictly TIGHTER legal stop: move ``fraction`` of the way from the current SL to price.

        Used only by the AI ``tighten_lock`` decision AFTER the mechanical lock. Returns None when the
        move is not strictly tighter, not legal (stops/freeze level) or would not keep the verified
        net lock. Never loosens: the result always passes ``assert_never_loosens``.
        """
        if not isinstance(fraction, Decimal) or not ZERO < fraction < 1 or position.sl <= ZERO:
            return None
        meta, tick = (
            await self.broker.get_symbol_info(position.symbol),
            await self.broker.get_tick(position.symbol),
        )
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        price = tick.exit(position.side)
        gap = position.side.sign * (price - position.sl)
        if gap <= ZERO:
            return None
        sl = snap(
            position.sl + position.side.sign * gap * fraction, meta.tick_size, up=position.side == Side.SELL
        )
        if position.side.sign * (sl - position.sl) <= ZERO:
            return None
        assert_never_loosens(position.side, position.sl, sl)
        command = BrokerCommand(
            Operation.PROTECT,
            "4" * 64,
            self.clock.now(),
            ticket=position.ticket,
            position_identifier=position.identifier,
            sl=sl,
        )
        try:
            protection_prices(command, position, meta, tick, self.settings)
        except BrokerError:
            return None
        if owned.lock_level > 0:
            goal = owned.target_usd * Decimal(str(owned.lock_level)) / 100
            if await self.net_at_price(position, owned, sl) < goal:
                return None
        return sl
