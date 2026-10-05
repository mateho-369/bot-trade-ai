import asyncio
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import AuditLog, BrokerDeal, OrderIntent, RiskEvent, RiskState, Trade
from tests.risk_helpers import OWNER, D, config, make_engine, open_one, safe_context
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import NewsWindow
from trading.types import BrokerCommand, Operation, ResultStatus, Side, TradingDisabled, UncertainExecution


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    yield result
    await result.shutdown()
    result.database.close()


async def test_paused_engine_rejects_and_logs_without_a_fill(engine):
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(TradingDisabled, match="paused"):
        await engine.execute(plan, safe_context(engine.clock))
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "rejected" and not row.request["reserved"] and not row.request["counted"]
        assert session.scalar(select(RiskEvent)).event == "entry_vetoed"
        assert any(row.action == "intent.authority_veto" for row in session.scalars(select(AuditLog)))


@pytest.mark.parametrize("context", ["unknown_news", "unsafe_news", "low_ai", "low_technical", "stale"])
async def test_quality_veto_never_forces_the_daily_target(engine, context):
    engine.control.resume(OWNER, account_key=engine.account_key)
    values = {
        "unknown_news": {"news": NewsWindow()},
        "unsafe_news": {"news": NewsWindow(known=True, safe=False)},
        "low_ai": {"ai_confidence": 0},
        "low_technical": {"signal_score": 0},
        "stale": {"observed_at": engine.clock.now() - timedelta(seconds=31)},
    }[context]
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(TradingDisabled):
        await engine.execute(plan, safe_context(engine.clock, **values))
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        assert session.scalar(select(RiskState)).accepted_entries_today == 0


async def test_exact_durable_duplicate_sends_once_under_concurrent_callers(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order(
        "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="1" * 64
    )
    results = await asyncio.gather(*(engine.execute(plan, safe_context(engine.clock)) for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert len(await engine.broker.get_positions()) == 1
    with engine.database.session() as session:
        assert len(session.scalars(select(OrderIntent)).all()) == 1
        assert len(session.scalars(select(BrokerDeal)).all()) == 1
        assert session.scalar(select(RiskState)).accepted_entries_today == 1
        trade = session.scalar(select(Trade))
        assert trade.ticket == 100001 and trade.position_identifier == 500001
        assert trade.ticket != results[0].order_ticket != trade.position_identifier


async def test_idempotency_payload_conflict_never_resends(engine):
    plan, _ = await open_one(engine)
    changed = replace(plan, order=replace(plan.order, sl=plan.order.sl - D("0.00001")))
    with pytest.raises(TradingDisabled, match="payload"):
        await engine.execute(changed, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1


async def test_restart_restores_equity_ownership_counters_and_cached_fill(engine):
    plan, filled = await open_one(engine)
    account = await engine.broker.get_account_info()
    before = engine.control.session_id
    await engine.shutdown()
    broker = MockMT5Client(engine.settings, clock=engine.clock)
    restarted = ExecutionEngine(broker, engine.database, engine.settings)
    try:
        await restarted.initialize()
        assert restarted.control.session_id != before and restarted.database.status()["state"] == "paused"
        assert (await broker.get_account_info()).balance == account.balance
        duplicate = await restarted.execute(plan, safe_context(engine.clock))
        assert duplicate == filled
        assert len(await broker.get_positions()) == 1
        with engine.database.session() as session:
            assert session.scalar(select(RiskState)).accepted_entries_today == 1
    finally:
        await restarted.shutdown()


async def test_missing_checkpoint_with_existing_database_is_not_a_capital_reset(engine):
    await open_one(engine)
    await engine.shutdown()
    engine.store.path.unlink()
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    with pytest.raises(TradingDisabled, match="NEVER reset"):
        await restarted.initialize()
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "open"


async def test_corrupt_checkpoint_refuses_restart_without_writing_over_it(engine):
    await open_one(engine)
    await engine.shutdown()
    engine.store.path.write_text("corrupt", encoding="utf-8")
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    with pytest.raises(Exception, match="corrupt"):
        await restarted.initialize()
    assert engine.store.path.read_text() == "corrupt"


@pytest.mark.parametrize("control", ["pause", "kill"])
async def test_maintenance_remains_enabled_while_paused_or_killed(engine, control):
    _, _ = await open_one(engine)
    owned = engine.logger.owned(engine.account_key)[0]
    getattr(engine.control, control)(OWNER)
    result = await engine.protect_sl(owned.ticket, owned.identifier, D("1.09900"))
    assert result.status == ResultStatus.FILLED
    closed = await PositionManager(engine).close(owned.identifier, owner_id=OWNER)
    assert closed.status == ResultStatus.FILLED
    assert await engine.broker.get_positions() == ()
    assert engine.database.status()["state"] in {"paused", "killed"}
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.status == "closed"
        rows = session.scalars(select(BrokerDeal)).all()
        assert trade.profit == sum(row.profit + row.commission + row.swap + row.fee for row in rows)
        assert trade.commission == D("-0.14")


async def test_manually_tagged_position_is_never_adopted_or_closed(engine):
    # Deliberate TEST-only foreign activity using a different standalone shadow ledger.
    foreign = MockMT5Client(engine.settings, clock=engine.clock)
    await foreign.initialize()
    try:
        plan = await foreign._calculator().plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="MANUAL_TEST"
        )
        await foreign.open_market_buy(plan.order)
        position = (await foreign.get_positions())[0]
        engine.broker._positions[position.identifier] = position
        engine.broker._margins[position.identifier] = D("22")
        engine.broker._original_tps[position.identifier] = position.tp
        summary = await engine.reconcile()
        assert summary["ledger_mismatch"] and not engine.logger.owned(engine.account_key)
        with pytest.raises(TradingDisabled):
            await PositionManager(engine).close(position.identifier, owner_id=OWNER)
    finally:
        await foreign.shutdown()


async def test_owner_close_requires_authenticated_owner_and_proved_ids(engine):
    await open_one(engine)
    owned = engine.logger.owned(engine.account_key)[0]
    with pytest.raises(TradingDisabled):
        await PositionManager(engine).close(owned.identifier, owner_id=OWNER + 1)
    with pytest.raises(TradingDisabled):
        await engine.close_owned(owned.ticket + 1, owned.identifier)
    assert len(await engine.broker.get_positions()) == 1


async def test_cancellation_retains_reservation_and_never_blindly_retries(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order(
        "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="b" * 64
    )
    entered, release = Event(), Event()
    original = engine.authority.before_send

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(engine.authority, "before_send", blocked)
    task = asyncio.create_task(engine.execute(plan, safe_context(engine.clock)))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.broker.health()["writes_quarantined"]
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "unknown" and row.request["reserved"] and row.request["counted"]
        assert session.scalar(select(RiskState)).reserved_risk_usd > 0
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))


async def test_checkpoint_failure_after_mutation_quarantines_and_retains_risk(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)
    real_save = engine.store.save

    def fail_after_entry(state):
        if state["positions"]:
            raise OSError("TEST disk full password=do_not_log")
        return real_save(state)

    monkeypatch.setattr(engine.store, "save", fail_after_entry)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))
    assert engine.broker.health()["writes_quarantined"]
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "unknown" and row.request["reserved"]
        assert session.scalar(select(Trade)) is None
    assert engine.store.load()["positions"] == []


async def test_snapshot_precedes_ack_and_recovers_a_db_callback_crash(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)

    def crash(*args):
        raise RuntimeError("TEST callback crash")

    monkeypatch.setattr(engine.authority, "on_result", crash)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))
    checkpoint = engine.store.load()
    assert checkpoint["positions"] and checkpoint["cache"]
    await engine.shutdown()
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    try:
        summary = await restarted.initialize()
        assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
        assert len(restarted.logger.owned(restarted.account_key)) == 1
        assert restarted.database.status()["state"] == "paused"
        restarted.control.acknowledge_recovery(
            OWNER, account_key=restarted.account_key, broker_writes_quarantined=False
        )
        restarted.control.resume(OWNER, account_key=restarted.account_key)
        result = await restarted.execute(plan, safe_context(engine.clock))
        assert result.status == ResultStatus.FILLED and len(await restarted.broker.get_positions()) == 1
    finally:
        await restarted.shutdown()


async def test_automatic_tp_is_persisted_even_when_triggered_by_read(engine):
    plan, _ = await open_one(engine)
    await engine.broker.market.set_tick("EURUSD", plan.order.tp, plan.order.tp + D("0.00012"))
    assert await engine.broker.get_positions() == ()
    checkpoint = engine.store.load()
    assert checkpoint["positions"] == [] and len(checkpoint["deals"]) == 2
    await engine.reconcile()
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "closed"
    assert await engine.execute(
        plan, safe_context(engine.clock)
    )  # Old ID still cannot reopen a closed position.
    assert await engine.broker.get_positions() == ()


async def test_post_authorization_owner_pause_veto_is_committed_and_releases_unsent_risk(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order)
    from trading.snapshots import broker_snapshot

    snapshot = await broker_snapshot(
        engine.broker.market,
        engine.settings,
        await engine.broker.get_account_info(),
        await engine.broker.get_symbol_info("EURUSD"),
        await engine.broker.get_tick("EURUSD"),
        (),
        plan.worst_loss_account,
        plan.margin_account,
        D("5.36"),
        durable_simulation=True,
    )
    engine.authority.stage(
        command, snapshot.account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
    )
    grant = engine.authority.authorize(command, snapshot)
    engine.control.pause(OWNER)
    with pytest.raises(TradingDisabled, match="changed before send"):
        engine.authority.before_send(command, snapshot, grant)
    from trading.types import ExecutionResult

    engine.authority.on_result(
        command,
        ExecutionResult(
            Operation.OPEN,
            command.idempotency_key,
            engine.account_key,
            ResultStatus.REJECTED,
            reason="TEST no send",
        ),
    )
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "rejected"
        assert session.scalar(select(RiskState)).reserved_risk_usd == 0
        assert session.scalar(select(RiskState)).accepted_entries_today == 0


async def test_one_daily_entry_is_a_limit_not_a_quota(tmp_path):
    engine = await make_engine(tmp_path, max_daily_trades=1)
    try:
        await open_one(engine)
        owned = engine.logger.owned(engine.account_key)[0]
        await engine.close_owned(owned.ticket, owned.identifier)
        plan = await engine.calculator.plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="2" * 64
        )
        with pytest.raises(Exception, match="daily|count"):
            await engine.execute(plan, safe_context(engine.clock))
        assert await engine.broker.get_positions() == ()
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_unachievable_minimum_lot_is_skipped_never_rounded_up(tmp_path):
    engine = await make_engine(tmp_path, paper_initial_balance=D("10"))
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        result = await engine.open(
            "EURUSD", Side.BUY, D("1.09000"), safe_context(engine.clock), strategy="TEST"
        )
        assert result is None and await engine.broker.get_positions() == ()
    finally:
        await engine.shutdown()
        engine.database.close()


def test_managed_execution_requires_persistent_database(tmp_path):
    cfg = config(tmp_path, database_url="sqlite:///:memory:")
    db = Database(cfg)
    db.initialize()
    engine = ExecutionEngine(MockMT5Client(cfg), db, cfg)
    try:
        with pytest.raises(TradingDisabled, match="persistent"):
            asyncio.run(engine.initialize())
    finally:
        db.close()


async def test_post_ack_reconciliation_failure_halts_and_preserves_durable_fill(engine, monkeypatch):
    from core.models import BotState
    from trading.types import ConnectionUnavailable

    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    real_reconcile, calls = engine._reconcile, 0

    async def fail_after_ack():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionUnavailable("TEST post-ack history unavailable")
        return await real_reconcile()

    monkeypatch.setattr(engine, "_reconcile", fail_after_ack)
    with pytest.raises(ConnectionUnavailable, match="post-ack"):
        await engine.execute(plan, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1
    assert engine.store.load()["positions"] and engine.store.load()["cache"]
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "acknowledged" and row.request["result"]["status"] == "filled"
        assert row.request["reserved"] and session.scalar(select(RiskState)).reserved_risk_usd > 0
        assert session.get(BotState, 1).last_error == "unknown_execution"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    monkeypatch.setattr(engine, "_reconcile", real_reconcile)
    summary = await engine.reconcile()
    assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
    assert await engine.execute(plan, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
