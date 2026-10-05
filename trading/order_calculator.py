"""Native broker valuation, downward sizing, bounded tick-grid target solving."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from core.security import sha256_json
from core.settings import Settings
from trading.ai_controls import broker_ceilings
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price, floor_volume, snap, validate_entry, validate_volume
from trading.types import ZERO, Clock, InvalidOrder, MarketData, MarketOrder, RiskViolation, Side


@dataclass(frozen=True, slots=True)
class LotCalculation:
    volume: Decimal
    risk_budget_account: Decimal
    worst_loss_account: Decimal
    margin_account: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class OrderPlan:
    order: MarketOrder
    risk_budget_account: Decimal
    worst_loss_account: Decimal
    margin_account: Decimal
    target_profit_usd: Decimal
    expected_net_profit_usd: Decimal
    reward_risk: Decimal


class OrderCalculator:
    def __init__(self, broker: MarketData, settings: Settings, clock: Clock | None = None) -> None:
        self.broker, self.settings = broker, settings
        self.clock = clock or broker.clock
        self.currency = CurrencyConverter(broker, settings, self.clock)

    def costs_usd(self, volume: Decimal, held_days: Decimal = ZERO) -> Decimal:
        if not isinstance(volume, Decimal) or volume <= ZERO or held_days < ZERO or not held_days.is_finite():
            raise InvalidOrder("invalid volume/holding-cost inputs")
        return volume * (
            self.settings.commission_round_turn_usd_per_lot
            + self.settings.estimated_swap_usd_per_lot_per_day * held_days
        )

    async def costs_account(self, volume: Decimal, held_days: Decimal = ZERO) -> Decimal:
        # A cost is a liability: use the adverse conversion side.
        return -(await self.currency.usd_to_account(-self.costs_usd(volume, held_days)))

    async def net_profit_account(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        exit_price: Decimal,
        *,
        include_costs: bool = True,
        held_days: Decimal = ZERO,
    ) -> Decimal:
        result = await self.broker.calculate_profit(symbol, side, volume, entry, exit_price)
        if not result.is_finite():
            raise RiskViolation("native profit calculator returned non-finite data")
        return result - (await self.costs_account(volume, held_days) if include_costs else ZERO)

    async def calculate_profit_usd(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        exit_price: Decimal,
        *,
        include_costs: bool = False,
        held_days: Decimal = ZERO,
    ) -> Decimal:
        result = await self.net_profit_account(
            symbol, side, volume, entry, exit_price, include_costs=include_costs, held_days=held_days
        )
        return await self.currency.to_usd(result)

    async def calculate_lot_size(
        self, symbol: str, side: Side, entry: Decimal, sl: Decimal, *, risk_percent: Decimal | None = None
    ) -> LotCalculation:
        meta = await self.broker.get_symbol_info(symbol)
        account = await self.broker.get_account_info()
        percent = self.settings.effective_risk_percent if risk_percent is None else risk_percent
        if (
            not isinstance(percent, Decimal)
            or not percent.is_finite()
            or not ZERO < percent <= broker_ceilings(self.settings).risk_percent
        ):
            raise RiskViolation("requested risk exceeds the configured effective cap")
        if side.sign * (entry - sl) <= ZERO:
            raise InvalidOrder("SL must be on the loss side")
        budget = account.risk_capital * percent / Decimal("100")
        available_margin = max(
            ZERO,
            min(
                account.margin_free,
                account.risk_capital * self.settings.max_margin_usage_percent / Decimal("100")
                - account.margin,
            ),
        )
        worst_entry = adverse_price(entry, side, meta, self.settings.max_slippage_points, entry=True)
        worst_exit = adverse_price(sl, side, meta, self.settings.max_slippage_points, entry=False)
        if min(worst_entry, worst_exit) <= ZERO:
            raise InvalidOrder("slippage bound would cross nonpositive prices")
        probe = meta.volume_min
        loss = -(await self.net_profit_account(symbol, side, probe, worst_entry, worst_exit))
        margin = await self.broker.calculate_margin(symbol, side, probe, worst_entry)
        if loss <= ZERO or margin < ZERO or not margin.is_finite():
            raise RiskViolation("invalid native loss/margin valuation")
        limit = min(meta.volume_max, probe * budget / loss)
        if margin > ZERO:
            limit = min(limit, probe * available_margin / margin)
        if meta.volume_limit > ZERO:
            limit = min(limit, meta.volume_limit)
        volume = floor_volume(limit, meta)
        if volume == ZERO:
            return LotCalculation(ZERO, budget, ZERO, ZERO, "broker minimum lot exceeds risk/margin budget")
        # Revalue the actual candidate. Do not assume a tiered native calculator
        # scales linearly. Bounded downward adjustment never rounds volume up.
        for _ in range(32):
            final_loss = -(await self.net_profit_account(symbol, side, volume, worst_entry, worst_exit))
            final_margin = await self.broker.calculate_margin(symbol, side, volume, worst_entry)
            if final_loss > ZERO and ZERO <= final_margin <= available_margin and final_loss <= budget:
                return LotCalculation(
                    volume, budget, final_loss, final_margin, "within nominal risk and margin caps"
                )
            if final_loss <= ZERO or final_margin < ZERO or not final_margin.is_finite():
                raise RiskViolation("invalid native candidate valuation")
            ratio = min(
                Decimal("1"),
                budget / final_loss,
                available_margin / final_margin if final_margin else Decimal("1"),
            )
            smaller = floor_volume(min(volume - meta.volume_step, volume * ratio), meta)
            if smaller == ZERO:
                break
            volume = smaller
        return LotCalculation(ZERO, budget, ZERO, ZERO, "no legal lot within native risk/margin caps")

    async def price_for_profit_usd(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        target_usd: Decimal,
        *,
        include_costs: bool = False,
        held_days: Decimal = ZERO,
        exit_slippage_points: int = 0,
        net_offset_account: Decimal = ZERO,
    ) -> Decimal:
        if not isinstance(target_usd, Decimal) or not target_usd.is_finite() or target_usd < ZERO:
            raise InvalidOrder("target must be a finite nonnegative USD amount")
        if not isinstance(net_offset_account, Decimal) or not net_offset_account.is_finite():
            raise InvalidOrder("net offset must be a finite signed account-currency Decimal")
        meta = await self.broker.get_symbol_info(symbol)
        validate_volume(volume, meta)
        if not isinstance(entry, Decimal) or not entry.is_finite() or entry <= ZERO:
            raise InvalidOrder("invalid entry price")
        # A partial-fill VWAP entry can be OFF-grid; only executable targets
        # must be on the grid. Keep the true entry for native valuation.
        anchor = snap(entry, meta.tick_size, up=side == Side.BUY)
        costs = await self.costs_account(volume, held_days) if include_costs else ZERO

        async def evaluate(ticks: int) -> Decimal:
            price = anchor + side.sign * meta.tick_size * ticks
            executable = adverse_price(price, side, meta, exit_slippage_points, entry=False)
            if min(price, executable) <= ZERO:
                raise RiskViolation("target solver reached a nonpositive price")
            value = await self.broker.calculate_profit(symbol, side, volume, entry, executable)
            return await self.currency.to_usd(value - costs + net_offset_account)

        low, high = 0, 1
        if await evaluate(low) >= target_usd:
            return anchor
        # Integer tick search: bounded, BUY/SELL-aware, smallest sufficient target.
        # SELL prices have a physical positive lower bound. Clamp the bracket
        # rather than rejecting a reachable target when doubling crosses zero.
        ceiling = int(anchor / meta.tick_size) - 1 if side == Side.SELL else 2**25
        if ceiling < 1:
            raise RiskViolation("target has no positive executable price")
        high = min(high, ceiling)
        for _ in range(25):
            if await evaluate(high) >= target_usd:
                break
            if high == ceiling:
                raise RiskViolation("target exceeds the bounded positive-price range")
            low, high = high, min(high * 2, ceiling)
        else:
            raise RiskViolation("USD target cannot be bracketed within the solver bound")
        while high - low > 1:
            middle = (low + high) // 2
            if await evaluate(middle) >= target_usd:
                high = middle
            else:
                low = middle
        if await evaluate(high) < target_usd:
            raise RiskViolation("valuation changed during target solving; replan with fresh quotes")
        return anchor + side.sign * meta.tick_size * high

    async def calculate_price_distance_from_usd_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, target_usd: Decimal, **kwargs
    ) -> Decimal:
        price = await self.price_for_profit_usd(symbol, side, volume, entry, target_usd, **kwargs)
        return abs(price - entry)

    async def plan_market_order(
        self,
        symbol: str,
        side: Side,
        sl: Decimal,
        *,
        strategy: str,
        idempotency_key: str | None = None,
        risk_percent: Decimal | None = None,
        target_usd: Decimal | None = None,
        created_at: datetime | None = None,
    ) -> OrderPlan | None:
        meta = await self.broker.get_symbol_info(symbol)
        tick = await self.broker.get_tick(symbol)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        entry = tick.entry(side)
        sl = snap(sl, meta.tick_size, up=side == Side.SELL)
        sized = await self.calculate_lot_size(symbol, side, entry, sl, risk_percent=risk_percent)
        if sized.volume == ZERO:
            return None
        goal = self.settings.target_profit_usd_per_trade if target_usd is None else target_usd
        if not isinstance(goal, Decimal) or not goal.is_finite() or goal <= ZERO:
            raise InvalidOrder("profit objective must be a positive Decimal")
        if self.settings.use_dynamic_target:
            loss_usd = -(await self.currency.to_usd(-sized.worst_loss_account))
            goal = max(goal, loss_usd * self.settings.target_r_multiple)
        worst_entry = adverse_price(entry, side, meta, self.settings.max_slippage_points, entry=True)
        tp = await self.price_for_profit_usd(
            symbol,
            side,
            sized.volume,
            worst_entry,
            goal,
            include_costs=True,
            exit_slippage_points=self.settings.max_slippage_points,
        )
        net_account = await self.net_profit_account(
            symbol,
            side,
            sized.volume,
            worst_entry,
            adverse_price(tp, side, meta, self.settings.max_slippage_points, entry=False),
        )
        reward_risk = net_account / sized.worst_loss_account
        if reward_risk < self.settings.min_net_reward_risk:
            raise RiskViolation("profit objective fails the net reward/risk floor; skip, do not enlarge risk")
        order = MarketOrder(
            symbol,
            side,
            sized.volume,
            entry,
            sl,
            tp,
            idempotency_key or sha256_json({"nonce": str(uuid4()), "symbol": symbol}),
            created_at or self.clock.now(),
            strategy,
        )
        validate_entry(order, meta, tick, self.settings, self.clock)
        net_usd = await self.currency.to_usd(net_account)
        return OrderPlan(
            order,
            sized.risk_budget_account,
            sized.worst_loss_account,
            sized.margin_account,
            goal,
            net_usd,
            reward_risk,
        )
