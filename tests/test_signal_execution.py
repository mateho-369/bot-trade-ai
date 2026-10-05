"""Synthetic signal → durable risk/execution integration; never native execution."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.models import BrokerDeal, OrderIntent, RiskState
from scripts.synthetic_signal_market import EngineeredSignalMarket
from tests.risk_helpers import OWNER
from tests.signal_helpers import approved, make_signal_runtime
from trading.execution import ExecutionEngine
from trading.simulation import SimulatedBroker
from trading.types import ResultStatus, Side, SourceKind, TradingDisabled, UncertainExecution


@pytest.mark.parametrize("sign", [1, -1])
async def test_reviewed_buy_sell_use_structural_stop_and_risk_sizing(tmp_path, sign):
    signals, execution = await make_signal_runtime(tmp_path, sign=sign)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        result = await execution.execute_signal(ready.signal_id)
        assert result.status == ResultStatus.FILLED
        owned = execution.logger.owned(execution.account_key)[0]
        position = (await execution.broker.get_positions())[0]
        assert position.side == (Side.BUY if sign == 1 else Side.SELL)
        assert position.sl == ready.stop_price and owned.target_usd >= 5
        with execution.database.session() as session:
            intent = session.scalar(select(OrderIntent))
            risk = session.scalar(select(RiskState))
            assert intent.state == "reconciled" and intent.request["context"]["signal_id"] == ready.signal_id
            assert risk.accepted_entries_today == 1 and risk.reserved_risk_usd == 0
            assert Decimal(intent.request["risk_account"]) <= Decimal("5")
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_concurrent_bridge_calls_send_one_original_order(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        results = await asyncio.gather(*(execution.execute_signal(ready.signal_id) for _ in range(6)))
        assert all(result == results[0] for result in results)
        with execution.database.session() as session:
            assert len(session.scalars(select(OrderIntent)).all()) == 1
            assert len(session.scalars(select(BrokerDeal)).all()) == 1
        assert len(await execution.broker.get_positions()) == 1
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_paused_veto_is_definitive_and_resume_does_not_replay_that_signal(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        with pytest.raises(TradingDisabled):
            await execution.execute_signal(ready.signal_id)
        execution.control.resume(OWNER, account_key=execution.account_key)
        duplicate = await execution.execute_signal(ready.signal_id)
        assert duplicate.status == ResultStatus.REJECTED and await execution.broker.get_positions() == ()
        with execution.database.session() as session:
            assert session.scalar(select(RiskState)).accepted_entries_today == 0
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_restart_and_changed_quote_return_original_fill_not_a_rebuilt_payload(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    restarted = None
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        filled = await execution.execute_signal(ready.signal_id)
        await execution.shutdown()
        market = EngineeredSignalMarket(execution.settings, clock=execution.clock)
        broker = SimulatedBroker(
            market, execution.settings, source_kind=SourceKind.SYNTHETIC, ledger_id="signal-tests"
        )
        restarted = ExecutionEngine(broker, execution.database, execution.settings)
        await restarted.initialize()
        position = (await broker.get_positions())[0]
        await market.set_tick(
            "EURUSD", position.entry_price + Decimal("0.0002"), position.entry_price + Decimal("0.00032")
        )
        duplicate = await restarted.execute_signal(ready.signal_id)
        assert duplicate == filled and len(await broker.get_positions()) == 1
        assert restarted.database.status()["state"] == "paused"
    finally:
        if restarted:
            await restarted.shutdown()
        else:
            await execution.shutdown()
        execution.database.close()


async def test_closed_or_revoked_signal_duplicate_never_reopens(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        filled = await execution.execute_signal(ready.signal_id)
        owned = execution.logger.owned(execution.account_key)[0]
        await execution.close_owned(owned.ticket, owned.identifier)
        await asyncio.to_thread(signals.store.revoke, ready.signal_id)
        assert await execution.execute_signal(ready.signal_id) == filled
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_expired_context_cannot_create_first_intent(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        execution.clock.advance(timedelta(seconds=31))
        with pytest.raises(TradingDisabled, match="stale"):
            await execution.execute_signal(ready.signal_id)
        with execution.database.session() as session:
            assert session.scalar(select(OrderIntent)) is None
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_signal_entry_does_not_chase_price_beyond_closed_bar_atr(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        p = ready.payload()
        far = Decimal(p["bar_close_price"]) + Decimal(p["atr"])
        meta = await execution.broker.get_symbol_info("EURUSD")
        from trading.price_rules import snap

        far = snap(far, meta.tick_size, up=True)
        await execution.broker.market.set_tick("EURUSD", far, far + Decimal("0.00012"))
        with pytest.raises(TradingDisabled, match="drift"):
            await execution.execute_signal(ready.signal_id)
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_changed_structural_stop_is_vetoed_even_via_direct_execute(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        plan = await execution.calculator.plan_market_order(
            "EURUSD", ready.side, ready.stop_price + Decimal("0.00001"), strategy="weighted_router_v1"
        )
        assert plan is not None
        with pytest.raises(TradingDisabled, match="strategy_stop_changed"):
            await execution.execute(plan, ready.context)
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_single_signal_cannot_be_reused_with_a_different_entry_key(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        await execution.execute_signal(ready.signal_id)
        owned = execution.logger.owned(execution.account_key)[0]
        await execution.close_owned(owned.ticket, owned.identifier)
        plan = await execution.calculator.plan_market_order(
            "EURUSD", ready.side, ready.stop_price, strategy="weighted_router_v1"
        )
        with pytest.raises(TradingDisabled, match="signal_already_reserved_or_executed"):
            await execution.execute(plan, ready.context)
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_uncertain_existing_signal_intent_is_not_blindly_resubmitted(tmp_path, monkeypatch):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)

        def fail(*args):
            raise OSError("TEST checkpoint failure")

        monkeypatch.setattr(execution.store, "save", fail)
        with pytest.raises(UncertainExecution):
            await execution.execute_signal(ready.signal_id)
        with pytest.raises(UncertainExecution):
            await execution.execute_signal(ready.signal_id)
        with execution.database.session() as session:
            assert len(session.scalars(select(OrderIntent)).all()) == 1
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_below_minimum_lot_returns_none_never_increases_size(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, paper_initial_balance=Decimal("10"))
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        assert await execution.execute_signal(ready.signal_id) is None
        assert await execution.broker.get_positions() == ()
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_signal_revocation_after_grant_vetoes_before_shadow_mutation(tmp_path, monkeypatch):
    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        original = execution.authority.before_send

        def revoke_then_validate(command, snapshot, grant):
            signals.store.revoke(ready.signal_id)
            return original(command, snapshot, grant)

        monkeypatch.setattr(execution.authority, "before_send", revoke_then_validate)
        with pytest.raises(TradingDisabled, match="changed before send"):
            await execution.execute_signal(ready.signal_id)
        assert await execution.broker.get_positions() == ()
        with execution.database.session() as session:
            assert session.scalar(select(OrderIntent)).state == "rejected"
            row = session.scalar(select(RiskState))
            assert row.reserved_risk_usd == 0 and row.accepted_entries_today == 0
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_price_drift_quality_veto_is_audited_without_sensitive_bodies(tmp_path):
    from core.models import AuditLog
    from trading.price_rules import snap

    signals, execution = await make_signal_runtime(tmp_path)
    try:
        ready = await approved(signals)
        execution.control.resume(OWNER, account_key=execution.account_key)
        meta = await execution.broker.get_symbol_info("EURUSD")
        price = snap(Decimal(ready.payload()["bar_close_price"]) + Decimal("0.002"), meta.tick_size, up=True)
        await execution.broker.market.set_tick("EURUSD", price, price + Decimal("0.00012"))
        with pytest.raises(TradingDisabled):
            await execution.execute_signal(ready.signal_id)
        with execution.database.session() as session:
            assert any(row.action == "signal.entry_bridge_veto" for row in session.scalars(select(AuditLog)))
            assert session.scalar(select(OrderIntent)) is None
    finally:
        await execution.shutdown()
        execution.database.close()
