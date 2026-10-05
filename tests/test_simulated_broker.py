import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from core.security import sha256_json
from core.settings import Settings
from tests.test_trading_contracts import NOW
from trading.mock_mt5 import MockMarketData, MockMT5Client
from trading.order_calculator import OrderCalculator
from trading.paper_mt5 import PaperMT5Client
from trading.symbol_manager import SymbolManager
from trading.types import (
    BrokerError,
    InvalidOrder,
    ManualClock,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    TradingDisabled,
    UnsupportedSymbol,
)

D = Decimal


def key(name):
    return sha256_json({"test": name})


async def make_plan(broker, cfg, side=Side.BUY):
    return await OrderCalculator(broker, cfg).plan_market_order(
        "XAUUSD",
        side,
        D("2608") if side == Side.BUY else D("2612.5"),
        strategy="test",
        idempotency_key=key("entry"),
    )


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_bid_ask_slippage_fees_and_complete_deal_ledger(side):
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg, side)
        result = await (
            broker.open_market_buy(plan.order) if side == Side.BUY else broker.open_market_sell(plan.order)
        )
        position = (await broker.get_positions())[0]
        assert result.status == ResultStatus.FILLED
        assert position.ticket != result.order_ticket != position.identifier
        assert position.profit < 0 and position.entry_commission < 0
        assert (await broker.get_equity()) < (await broker.get_balance()) < 1000
        target = plan.order.tp
        bid, ask = (target, target + D("0.2")) if side == Side.BUY else (target - D("0.2"), target)
        clock.advance(timedelta(seconds=1))
        await broker.set_tick("XAUUSD", bid, ask)
        assert await broker.get_positions() == ()
        deals = await broker.get_deals(NOW)
        assert len(deals) == 2 and deals[0].entry == "in" and deals[1].entry == "out"
        assert sum(deal.net for deal in deals) == (await broker.get_balance()) - D("1000")
        assert sum(deal.net for deal in deals) >= plan.target_profit_usd
        assert len(await broker.get_closed_deals(NOW)) == 1


async def test_duplicate_open_is_one_fill_even_concurrently_and_changed_body_is_rejected():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        results = await asyncio.gather(*(broker.open_market_buy(plan.order) for _ in range(8)))
        assert all(result == results[0] for result in results)
        assert len(await broker.get_positions()) == 1
        assert len(await broker.get_deals(NOW)) == 1
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(plan.order, tp=plan.order.tp + D("1")))
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(plan.order, idempotency_key=key("averaging")))


async def test_stop_gap_is_not_filled_at_favorable_original_stop():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        closed = (await broker.get_closed_deals(NOW))[0]
        assert closed.price == D("2589.98") < plan.order.sl
        assert closed.net < -plan.worst_loss_account  # Nominal risk is not a gap guarantee.


async def test_stale_tick_does_not_trigger_ghost_stop_and_blocks_new_entry():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"), timestamp=NOW - timedelta(seconds=11))
        assert len(await broker.get_positions()) == 1
        assert (await broker.get_account_info()).quotes_stale
        second = await OrderCalculator(broker, cfg).plan_market_order(
            "EURUSD", Side.BUY, D("1.099"), strategy="test"
        )
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(second.order)
        assert len(await broker.get_deals(NOW)) == 1


async def test_wrong_identity_close_duplicate_close_and_sl_never_loosened():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        with pytest.raises(TradingDisabled):
            await broker.close_position(
                position.ticket, position_identifier=position.identifier + 1, idempotency_key=key("wrong")
            )
        with pytest.raises(RiskViolation):
            await broker.modify_sl(
                position.ticket,
                plan.order.sl - D("1"),
                position_identifier=position.identifier,
                idempotency_key=key("loosen"),
            )
        await broker.set_tick("XAUUSD", D("2613"), D("2613.2"))
        modified = await broker.modify_sl(
            position.ticket, D("2611"), position_identifier=position.identifier, idempotency_key=key("lock")
        )
        clock.advance(timedelta(seconds=1))
        assert modified == await broker.modify_sl(
            position.ticket, D("2611"), position_identifier=position.identifier, idempotency_key=key("lock")
        )
        assert (await broker.get_positions())[0].sl == D("2611")
        closed = await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        clock.advance(timedelta(seconds=1))
        assert closed == await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        assert len(await broker.get_closed_deals(NOW)) == 1


async def test_daily_count_is_maximum_not_minimum_target():
    cfg = Settings(_env_file=None, max_daily_trades=1, min_daily_trades_target=0)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        second = await make_plan(broker, cfg)
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(second.order, idempotency_key=key("second")))


async def test_drawdown_latch_survives_restart_and_new_day():
    cfg = Settings(_env_file=None, max_drawdown_percent=D("3"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        snapshot = await broker.export_state()
        assert snapshot["drawdown_latched"]
    clock.advance(timedelta(days=1))
    async with MockMT5Client(cfg, clock=clock, restored_state=snapshot) as restored:
        plan = await make_plan(restored, cfg)
        with pytest.raises(RiskViolation):
            await restored.open_market_buy(replace(plan.order, idempotency_key=key("newday")))


async def test_snapshot_restores_exposure_and_idempotency_not_a_fresh_paper_account():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as first:
        plan = await make_plan(first, cfg)
        result = await first.open_market_buy(plan.order)
        snapshot = await first.export_state()
    async with MockMT5Client(cfg, clock=clock, restored_state=snapshot) as restored:
        assert len(await restored.get_positions()) == 1
        assert await restored.open_market_buy(plan.order) == result
        assert len(await restored.get_deals(NOW)) == 1
        assert await restored.get_balance() == D(snapshot["balance"])
    broken = dict(snapshot, config_hash="0" * 64)
    with pytest.raises(RiskViolation):
        async with MockMT5Client(cfg, clock=clock, restored_state=broken):
            pass


async def test_held_position_swap_is_charged_on_close_and_in_equity():
    cfg = Settings(_env_file=None, estimated_swap_usd_per_lot_per_day=D("10"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        clock.advance(timedelta(days=2))
        position = (await broker.get_positions())[0]
        assert position.swap == -position.volume * D("20")
        await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("swap_close")
        )
        assert (await broker.get_closed_deals(NOW))[0].swap == position.swap


async def test_synthetic_candles_are_prefix_stable_and_never_depend_on_future_tick():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        old = await broker.get_candles("XAUUSD", "M5", 30)
        clock.advance(timedelta(minutes=5))
        await broker.set_tick("XAUUSD", D("4000"), D("4000.2"))
        future = await broker.get_candles("XAUUSD", "M5", 31)
        pd.testing.assert_frame_equal(old, future.iloc[:-1].reset_index(drop=True))
        assert future.close_time.max().to_pydatetime() <= clock.now()
        with pytest.raises(Exception, match="cannot peek"):
            await broker.get_candles("XAUUSD", "M5", as_of=clock.now() + timedelta(seconds=1))


async def test_symbol_manager_exact_aliases_disables_missing_and_never_guesses_news():
    cfg = Settings(_env_file=None, symbols=("XAUUSD", "MISSING"), symbol_aliases={"XAUUSD": "XAUUSDm"})
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        manager = SymbolManager(broker, cfg)
        await manager.initialize()
        assert manager.resolve("XAUUSD").native == "XAUUSDm"
        assert "MISSING" in manager.errors()
        assert manager.news_exposure_known("XAUUSD")
        with pytest.raises(UnsupportedSymbol):
            manager.resolve("MISSING")
    other = Settings(_env_file=None, symbols=("USDJPY",))
    async with MockMT5Client(other, clock=ManualClock(NOW)) as broker:
        manager = SymbolManager(broker, other)
        await manager.initialize()
        assert not manager.news_exposure_known("USDJPY")


async def test_paper_adapter_calls_no_source_writes_and_keeps_simulated_capital():
    cfg = Settings(_env_file=None)
    source = MockMarketData(cfg, clock=ManualClock(NOW))

    async def forbid(*args, **kwargs):
        raise AssertionError("a source write must NEVER occur")

    source.open_market_buy = source.open_market_sell = source.close_position = source.modify_sl = (
        source.modify_tp
    ) = forbid
    async with PaperMT5Client(source, cfg) as paper:
        plan = await make_plan(paper, cfg)
        await paper.open_market_buy(plan.order)
        assert paper.source_kind == SourceKind.PAPER and paper.market_source_kind == SourceKind.SYNTHETIC
        assert (await paper.get_account_info()).source == SourceKind.PAPER
        position = (await paper.get_positions())[0]
        await paper.modify_sl(
            position.ticket,
            D("2609"),
            position_identifier=position.identifier,
            idempotency_key=key("paper_sl"),
        )
        await paper.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("paper_close")
        )
        assert len(await paper.get_deals(NOW)) == 2


def test_simulator_cannot_masquerade_as_demo_execution():
    cfg = Settings(_env_file=None, paper_trading=False, mt5_backend="real")
    with pytest.raises(TradingDisabled):
        PaperMT5Client(MockMarketData(cfg), cfg)
    with pytest.raises(BrokerError):
        MockMT5Client(cfg)


async def test_tp_removal_and_unapproved_extension_are_blocked_in_paper():
    cfg = Settings(_env_file=None, allow_tp_extension=True)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        with pytest.raises(InvalidOrder):
            await broker.modify_tp(
                position.ticket,
                D("0"),
                position_identifier=position.identifier,
                idempotency_key=key("remove_tp"),
            )
        with pytest.raises(TradingDisabled):
            await broker.modify_tp(
                position.ticket,
                plan.order.tp + D("1"),
                position_identifier=position.identifier,
                idempotency_key=key("extend_tp"),
            )


async def test_daily_loss_reset_does_not_clear_drawdown_and_carry_gap_is_counted():
    cfg = Settings(_env_file=None, max_daily_loss_percent=D("1"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        clock.advance(timedelta(days=1))
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        state = await broker.export_state()
        assert state["daily_latched"] and not state["drawdown_latched"]
        clock.advance(timedelta(days=1))
        await broker.get_account_info()
        assert not (await broker.export_state())["daily_latched"]


@pytest.mark.parametrize(
    "case",
    ["source", "data_account", "negative_margin", "future_position", "counter", "cache", "original_tp"],
)
async def test_snapshot_integrity_rejects_wrong_scope_or_incoherent_accounting(case):
    import copy

    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        state = copy.deepcopy(await broker.export_state())
    if case == "source":
        state["source_kind"] = "mt5"
    if case == "data_account":
        state["data_account_key"] = "other-account"
    if case == "negative_margin":
        state["margins"] = {name: "-1" for name in state["margins"]}
    if case == "future_position":
        state["positions"][0]["time"] = (NOW + timedelta(days=1)).isoformat()
    if case == "counter":
        state["counters"][2] = 0
    if case == "cache":
        state["cache"][key("entry")][1]["account_key"] = "other-account"
    if case == "original_tp":
        state["original_tps"] = {name: "NaN" for name in state["original_tps"]}
    with pytest.raises(BrokerError):
        async with MockMT5Client(cfg, clock=clock, restored_state=state):
            pass
