from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from core.settings import Settings
from tests.test_trading_contracts import NOW
from trading.currency import CurrencyConverter
from trading.mock_mt5 import MockMT5Client, synthetic_catalogue
from trading.order_calculator import OrderCalculator
from trading.price_rules import adverse_price
from trading.types import InvalidOrder, ManualClock, RiskViolation, Side, StaleData

D = Decimal


@pytest.mark.parametrize(
    "symbol,side,sl",
    [
        ("XAUUSD", Side.BUY, "2608"),
        ("XAUUSD", Side.SELL, "2612.5"),
        ("EURUSD", Side.BUY, "1.099"),
        ("EURUSD", Side.SELL, "1.1012"),
    ],
)
async def test_sizing_and_cost_aware_plan(symbol, side, sl):
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        calculator = OrderCalculator(broker, cfg)
        plan = await calculator.plan_market_order(symbol, side, D(sl), strategy="test")
        assert plan is not None and plan.order.volume > 0
        assert 0 < plan.worst_loss_account <= plan.risk_budget_account
        assert plan.expected_net_profit_usd >= plan.target_profit_usd >= D("5")
        assert plan.reward_risk >= cfg.min_net_reward_risk
        assert plan.order.volume % (await broker.get_symbol_info(symbol)).volume_step == 0


async def test_below_minimum_lot_returns_skip_not_enlarged_risk():
    cfg = Settings(_env_file=None, paper_initial_balance=D("10"))
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "XAUUSD", Side.BUY, D("2608"), strategy="test"
        )
        assert plan is None


async def test_requested_risk_cannot_raise_configured_cap():
    # Without AI-dynamic limits the broker ceiling IS the owner's configured risk (0.5 %).
    cfg = Settings(_env_file=None, ai_dynamic_limits_enabled=False)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).calculate_lot_size(
                "XAUUSD", Side.BUY, D("2610.2"), D("2608"), risk_percent=D("1")
            )


async def test_requested_risk_cannot_exceed_the_hard_cap_with_dynamic_limits():
    # With AI-dynamic limits the broker ceiling is the 1.0 % hard cap, never more.
    cfg = Settings(_env_file=None, ai_dynamic_limits_enabled=True)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).calculate_lot_size(
                "XAUUSD", Side.BUY, D("2610.2"), D("2608"), risk_percent=D("1.01")
            )


async def test_margin_can_reduce_the_risk_sized_lot():
    cfg = Settings(_env_file=None, mock_leverage=1)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        lot = await OrderCalculator(broker, cfg).calculate_lot_size(
            "XAUUSD", Side.BUY, D("2610.2"), D("2608")
        )
        assert lot.volume == 0  # $2,610+ minimum-lot margin exceeds $300 allowance.


async def test_non_linear_native_valuation_is_rechecked_downward():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        original = broker.calculate_profit

        async def nonlinear(symbol, side, volume, entry, exit_price):
            result = await original(symbol, side, volume, entry, exit_price)
            return result * (D("2") if volume > D("0.01") else D("1"))

        broker.calculate_profit = nonlinear
        lot = await OrderCalculator(broker, cfg).calculate_lot_size(
            "XAUUSD", Side.BUY, D("2610.2"), D("2608")
        )
        assert lot.volume == D("0.01") and lot.worst_loss_account <= D("5")


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_target_on_large_native_tick_includes_slippage_and_vwap_entry(side):
    cfg = Settings(_env_file=None)
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], tick_size=D("0.25"))
    async with MockMT5Client(
        cfg, clock=ManualClock(NOW), symbols={"XAUUSD": meta}, quotes={"XAUUSD": (D("2610"), D("2610.25"))}
    ) as broker:
        calc = OrderCalculator(broker, cfg)
        entry = D("2610.375")  # A legal weighted execution entry, NOT a legal TP grid price.
        price = await calc.price_for_profit_usd(
            "XAUUSD", side, D("0.01"), entry, D("5"), include_costs=True, exit_slippage_points=10
        )
        assert price % meta.tick_size == 0
        actual = adverse_price(price, side, meta, 10, entry=False)
        assert (
            await calc.calculate_profit_usd("XAUUSD", side, D("0.01"), entry, actual, include_costs=True) >= 5
        )
        neighbor = adverse_price(price - side.sign * meta.tick_size, side, meta, 10, entry=False)
        assert (
            await calc.calculate_profit_usd("XAUUSD", side, D("0.01"), entry, neighbor, include_costs=True)
            < 5
        )
        plan = await calc.plan_market_order(
            "XAUUSD", side, D("2608") if side == Side.BUY else D("2612.5"), strategy="large_tick"
        )
        assert plan is not None and plan.order.tp % meta.tick_size == 0


async def test_net_reward_floor_rejects_small_fixed_objective_without_risk_escalation():
    cfg = Settings(_env_file=None, use_dynamic_target=False, target_profit_usd_per_trade=D("0.5"))
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).plan_market_order(
                "XAUUSD", Side.BUY, D("2608"), strategy="test"
            )


async def test_target_solver_is_bounded_on_flat_valuation():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        calls = 0

        async def flat(*args):
            nonlocal calls
            calls += 1
            return D("0")

        broker.calculate_profit = flat
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).price_for_profit_usd(
                "XAUUSD", Side.BUY, D("0.01"), D("2610"), D("5")
            )
        assert calls <= 27


async def test_eur_account_signed_bid_ask_conversion_and_net_usd_target():
    cfg = Settings(_env_file=None, account_currency="EUR", account_to_usd_symbols={"EUR": "EURUSD"})
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        converter = CurrencyConverter(broker, cfg)
        assert await converter.to_usd(D("10")) == D("11")
        assert await converter.to_usd(D("-10")) == D("-11.00120")
        assert await converter.usd_to_account(D("10")) == D("10") / D("1.10012")
        assert await converter.usd_to_account(D("-10")) == D("-10") / D("1.10000")
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "XAUUSD", Side.BUY, D("2608"), strategy="fx"
        )
        assert plan is not None and plan.expected_net_profit_usd >= plan.target_profit_usd


@pytest.mark.parametrize("mapping", [{}, {"EUR": "GBPUSD"}])
async def test_missing_or_wrong_conversion_route_fails_closed(mapping):
    cfg = Settings(_env_file=None, account_currency="EUR", account_to_usd_symbols=mapping)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await CurrencyConverter(broker, cfg).to_usd(D("5"))


async def test_cross_currency_via_two_signed_usd_legs_and_stale_fx_veto():
    cfg = Settings(_env_file=None, account_to_usd_symbols={"EUR": "EURUSD", "JPY": "USDJPY"})
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        converter = CurrencyConverter(broker, cfg)
        assert await converter.convert(D("10"), "EUR", "JPY") == D("11") * D("150")
        assert await converter.convert(D("-10"), "EUR", "JPY") == D("-11.00120") * D("150.020")
        await broker.set_tick("EURUSD", D("1.10"), D("1.10012"), timestamp=NOW - timedelta(seconds=11))
        with pytest.raises(StaleData):
            await converter.convert(D("5"), "EUR", "USD")


async def test_zero_or_nonfinite_target_rejected_by_planner():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        for target in (D("0"), D("NaN"), D("-1")):
            with pytest.raises(InvalidOrder):
                await OrderCalculator(broker, cfg).plan_market_order(
                    "XAUUSD", Side.BUY, D("2608"), strategy="test", target_usd=target
                )


async def test_sell_solver_clamps_positive_bound_instead_of_overshooting_a_reachable_target():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        price = await OrderCalculator(broker, cfg).price_for_profit_usd(
            "XAUUSD", Side.SELL, D("0.01"), D("100"), D("80")
        )
        assert price == D("20")
