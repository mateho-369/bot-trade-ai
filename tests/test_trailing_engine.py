from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.models import Trade
from tests.risk_helpers import OWNER, D, make_engine, open_one, safe_context
from trading.order_calculator import OrderCalculator
from trading.position_manager import PositionManager
from trading.risk_types import PositionReview
from trading.trailing_engine import TrailingEngine
from trading.types import BrokerError, Side, SourceKind


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    await open_one(result)
    yield result
    await result.shutdown()
    result.database.close()


async def at_quote(engine, bid):
    await engine.broker.set_tick("EURUSD", D(bid), D(bid) + D("0.00012"))
    await engine.reconcile()
    return (await engine.broker.get_positions())[0], engine.logger.owned(engine.account_key)[0]


async def test_earned_30_tier_is_deferred_when_stop_distance_makes_it_unachievable(engine):
    position, owned = await at_quote(engine, "1.10115")
    current = await engine.trailing.net_at_price(position, owned, D("1.10115"), stop=False)
    assert current >= owned.target_usd * D("0.3")
    assert await engine.trailing.plan(position, owned) is None
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 0


@pytest.mark.parametrize("bid,level", [("1.10165", 30), ("1.10250", 60), ("1.10280", 90)])
async def test_highest_earned_legally_achievable_lock_is_dollar_valued_and_persisted(engine, bid, level):
    position, owned = await at_quote(engine, bid)
    plan = await engine.trailing.plan(position, owned)
    assert plan and plan.lock_level == level
    assert plan.sl > position.sl and plan.estimated_net_at_stop_usd >= owned.target_usd * D(str(level)) / 100
    await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.sl == plan.sl and trade.profit_lock_level == level
        assert (
            D(trade.features_json["execution"]["last_verified_lock_net_usd"])
            >= trade.target_profit_usd * D(str(level)) / 100
        )


async def test_progress_at_60_can_fall_back_to_achievable_30_not_falsely_claim_60(engine):
    position, owned = await at_quote(engine, "1.10190")
    current = await engine.trailing.net_at_price(position, owned, D("1.10190"), stop=False)
    assert current >= owned.target_usd * D("0.6")
    plan = await engine.trailing.plan(position, owned)
    assert plan and plan.lock_level == 30


async def test_once_confirmed_lock_never_worsens_on_retracement(engine):
    manager = PositionManager(engine)
    await at_quote(engine, "1.10165")
    await manager.cycle()
    protected = (await engine.broker.get_positions())[0]
    await engine.broker.set_tick("EURUSD", D("1.10140"), D("1.10152"))
    await manager.cycle()
    position = (await engine.broker.get_positions())[0]
    assert position.sl == protected.sl
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 30


async def test_unverified_requested_lock_level_is_never_claimed(engine):
    owned = engine.logger.owned(engine.account_key)[0]
    await engine.protect_sl(owned.ticket, owned.identifier, D("1.09900"), lock_level=90)
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.profit_lock_level == 0 and trade.sl == D("1.09900")


async def test_no_change_can_verify_a_preexisting_better_stop_without_lowering_it(engine):
    position, owned = await at_quote(engine, "1.10165")
    await engine.protect_sl(position.ticket, position.identifier, D("1.10140"))
    position = (await engine.broker.get_positions())[0]
    owned = engine.logger.owned(engine.account_key)[0]
    plan = await engine.trailing.plan(position, owned)
    assert plan.sl == position.sl and plan.lock_level == 30
    await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=30)
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 30


async def test_stop_estimate_accounts_for_paid_entry_actual_swap_closing_fee_and_slippage(engine):
    position, owned = await at_quote(engine, "1.10165")
    meta = await engine.broker.get_symbol_info(position.symbol)
    changed = replace(position, swap=D("-0.20"))
    stop = D("1.10106")
    from trading.price_rules import adverse_price

    executable = adverse_price(
        stop,
        position.side,
        meta,
        engine.settings.max_slippage_points + engine.settings.trailing_spread_buffer_points,
        entry=False,
    )
    gross = await engine.broker.calculate_profit(
        position.symbol, position.side, position.volume, position.entry_price, executable
    )
    estimated = await engine.trailing.net_at_price(changed, owned, stop)
    assert estimated == gross + owned.entry_costs_account - D("0.20") - D("0.07")
    assert owned.entry_costs_account == D("-0.07")  # Never charge opening estimate twice.


async def test_lock_does_not_guarantee_a_gap_fill(engine):
    await at_quote(engine, "1.10165")
    await PositionManager(engine).cycle()
    position = (await engine.broker.get_positions())[0]
    await engine.broker.set_tick("EURUSD", position.sl - D("0.00100"), position.sl - D("0.00088"))
    await engine.reconcile()
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.status == "closed" and trade.profit < trade.target_profit_usd * D("0.3")
        assert trade.profit_lock_level == 30  # Historical confirmed estimate, NOT a guaranteed payout.


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_signed_actual_net_offset_solver_handles_both_sides(engine, side):
    calculator = OrderCalculator(engine.broker, engine.settings)
    entry = D("1.10000")
    target = D("1.50")
    price = await calculator.price_for_profit_usd(
        "EURUSD", side, D("0.02"), entry, target, net_offset_account=D("-0.30"), exit_slippage_points=2
    )
    from trading.price_rules import adverse_price

    meta = await engine.broker.get_symbol_info("EURUSD")
    executable = adverse_price(price, side, meta, 2, entry=False)
    gross = await engine.broker.calculate_profit("EURUSD", side, D("0.02"), entry, executable)
    assert gross - D("0.30") >= target and price % meta.tick_size == 0
    worse = price - side.sign * meta.tick_size
    executable = adverse_price(worse, side, meta, 2, entry=False)
    assert (
        await engine.broker.calculate_profit("EURUSD", side, D("0.02"), entry, executable) - D("0.30")
        < target
    )


async def test_sell_stop_only_moves_down_while_locked_net_moves_up(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        await open_one(engine, side=Side.SELL)
        await engine.broker.set_tick("EURUSD", D("1.09835"), D("1.09847"))
        await engine.reconcile()
        owned = engine.logger.owned(engine.account_key)[0]
        position = (await engine.broker.get_positions())[0]
        plan = await engine.trailing.plan(position, owned)
        assert plan and plan.sl < position.sl and plan.lock_level == 30
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=30)
        assert (await engine.broker.get_positions())[0].sl == plan.sl
    finally:
        await engine.shutdown()
        engine.database.close()


def atr_frame(clock, *, future=False):
    instants = [clock.now() - timedelta(minutes=5 * (15 - index)) for index in range(15)]
    frame = pd.DataFrame(
        {
            "time": instants,
            "close_time": [value + timedelta(minutes=5) for value in instants],
            "open": 1.10150,
            "high": 1.10155,
            "low": 1.10145,
            "close": 1.10150,
            "tick_volume": 10,
            "spread": 12,
            "real_volume": 0,
        }
    )
    if future:
        frame.loc[len(frame) - 1, "close_time"] = clock.now() + timedelta(minutes=5)
    return frame


def test_atr_is_a_closed_chronological_true_range(engine):
    frame = atr_frame(engine.clock)
    assert TrailingEngine.closed_atr(frame) == D("0.00010")
    frame.loc[0, "high"] = float("nan")
    with pytest.raises(BrokerError):
        TrailingEngine.closed_atr(frame)


@pytest.mark.parametrize("fault", ["short", "duplicate", "inverted", "negative"])
def test_invalid_atr_is_never_an_approval(engine, fault):
    frame = atr_frame(engine.clock)
    if fault == "short":
        frame = frame.iloc[:10]
    if fault == "duplicate":
        frame.loc[1, "close_time"] = frame.loc[0, "close_time"]
    if fault == "inverted":
        frame.loc[1, "high"] = 1.0
    if fault == "negative":
        frame.loc[1, "low"] = -1
    with pytest.raises(BrokerError):
        TrailingEngine.closed_atr(frame)


async def test_future_atr_candle_is_not_used_to_tighten_protection(tmp_path):
    engine = await make_engine(tmp_path, atr_trailing_enabled=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10060")  # Too early for a lock.
        assert (
            await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock, future=True)) is None
        )
        plan = await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock))
        assert plan and plan.reason == "closed_bar_atr" and plan.sl > position.sl
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_atr_can_beat_profit_tier_but_only_if_verified_net_is_sufficient(tmp_path):
    engine = await make_engine(tmp_path, atr_trailing_enabled=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10165")
        plan = await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock))
        assert plan and plan.reason == "closed_bar_atr" and plan.lock_level == 30
        assert plan.sl > D("1.10106")
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_freeze_zone_defers_modification_without_claiming_lock(engine):
    position, owned = await at_quote(engine, "1.10280")
    meta = engine.broker.market._symbols["EURUSD"]
    engine.broker.market._symbols["EURUSD"] = replace(meta, freeze_level=20)
    assert await engine.trailing.plan(position, owned) is None  # Existing TP only 11 points away.


async def test_extension_defaults_disabled_and_owner_policy_is_not_an_ai_override(engine):
    position, owned = await at_quote(engine, "1.10280")
    review = PositionReview(
        engine.clock.now(), SourceKind.SYNTHETIC, 99, True, True, safe_context(engine.clock).news
    )
    assert await engine.trailing.extension(position, owned, review) is None
    with pytest.raises(BrokerError):
        await engine.extend_tp(position.ticket, position.identifier, position.tp + D("0.00050"), review)
    assert (await engine.broker.get_positions())[0].sl == position.sl


@pytest.mark.parametrize("fault", ["ai", "momentum", "volatility", "news", "source", "stale"])
async def test_extension_review_must_be_fresh_high_quality_in_all_dimensions(tmp_path, fault):
    engine = await make_engine(tmp_path, allow_tp_extension=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10280")
        review = PositionReview(
            engine.clock.now(), SourceKind.SYNTHETIC, 90, True, True, safe_context(engine.clock).news
        )
        change = {
            "ai": {"ai_confidence": 0},
            "momentum": {"momentum_continues": False},
            "volatility": {"volatility_safe": False},
            "news": {"news": replace(review.news, known=False)},
            "source": {"source": SourceKind.MT5},
            "stale": {"observed_at": engine.clock.now() - timedelta(seconds=31)},
        }[fault]
        assert await engine.trailing.extension(position, owned, replace(review, **change)) is None
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_120_percent_extension_preserves_sl_and_immutable_original_target(tmp_path):
    engine = await make_engine(tmp_path, allow_tp_extension=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10280")
        plan = await engine.trailing.plan(position, owned)
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
        position = (await engine.broker.get_positions())[0]
        review = PositionReview(
            engine.clock.now(), SourceKind.SYNTHETIC, 90, True, True, safe_context(engine.clock).news
        )
        tp = await engine.trailing.extension(position, owned, review)
        assert tp and tp > position.tp
        assert abs(tp - position.entry_price) <= abs(owned.original_tp - position.entry_price) * D("1.2")
        engine.control.kill(OWNER)  # Live-entry permission is irrelevant to protective maintenance.
        await engine.extend_tp(position.ticket, position.identifier, tp, review)
        after = (await engine.broker.get_positions())[0]
        assert after.sl == position.sl and after.tp == tp
        with engine.database.session() as session:
            row = session.scalar(select(Trade))
            assert (
                row.target_profit_usd == owned.target_usd
                and D(row.features_json["execution"]["original_tp"]) == owned.original_tp
            )
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_non_usd_locks_use_verified_signed_fx_not_usd_parity(tmp_path):
    engine = await make_engine(tmp_path, account_currency="EUR", account_to_usd_symbols={"EUR": "EURUSD"})
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10165")
        plan = await engine.trailing.plan(position, owned)
        assert plan and plan.estimated_net_at_stop_usd >= owned.target_usd * D("0.3")
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
        with engine.database.session() as session:
            row = session.scalar(select(Trade))
            assert row.currency == "EUR" and not row.features_json["execution"]["profit_usd_verified"]
            assert row.features_json["execution"]["historical_fx_required"]
    finally:
        await engine.shutdown()
        engine.database.close()
