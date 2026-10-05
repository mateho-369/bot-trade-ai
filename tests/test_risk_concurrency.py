"""Cross-PROCESS reservation test: grants only; NO SDK/order submission."""

import asyncio
import multiprocessing
from dataclasses import replace

from sqlalchemy import select

from core.database import Database
from core.models import OrderIntent, RiskState
from tests.risk_helpers import MOMENT, OWNER, config, make_engine, safe_context
from trading.execution_authority import DurableWriteAuthority
from trading.risk_types import RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.snapshots import broker_snapshot
from trading.types import BrokerCommand, ManualClock, Operation, Side, SourceKind, TradingDisabled


def competing_reservation(root, session_id, command, snapshot, queue):
    # Spawned child knows only explicitly constructed TEST synthetic settings.
    settings = config(root)
    database = Database(settings)
    control = RuntimeControl(database, settings, ManualClock(MOMENT))
    control.session_id = session_id
    authority = DurableWriteAuthority(
        database, settings, control.clock, control, RuntimeProfile.current(settings, SourceKind.SYNTHETIC)
    )
    try:
        authority.authorize(command, snapshot)
        queue.put("granted")
    except TradingDisabled:
        queue.put("vetoed")
    except BaseException as exc:
        queue.put("unexpected_" + type(exc).__name__)
    finally:
        database.close()


async def test_cross_process_locked_reservations_cannot_both_use_same_free_equity(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        plan = await engine.calculator.plan_market_order(
            "EURUSD", Side.BUY, __import__("decimal").Decimal("1.09780"), strategy="TEST"
        )
        commands = [
            BrokerCommand(
                Operation.OPEN,
                key * 64,
                engine.clock.now(),
                order=replace(plan.order, idempotency_key=key * 64),
            )
            for key in ("1", "2")
        ]
        account = await engine.broker.get_account_info()
        snapshot = await broker_snapshot(
            engine.broker.market,
            engine.settings,
            account,
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            plan.worst_loss_account,
            plan.margin_account,
            plan.expected_net_profit_usd,
            durable_simulation=True,
        )
        for command in commands:
            engine.authority.stage(
                command, account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
            )
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=competing_reservation,
                args=(tmp_path, engine.control.session_id, command, snapshot, queue),
            )
            for command in commands
        ]
        for process in processes:
            process.start()
        for process in processes:
            await asyncio.to_thread(process.join, 15)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
            assert process.exitcode == 0
        outcomes = sorted(queue.get(timeout=3) for _ in processes)
        assert outcomes == ["granted", "vetoed"]
        queue.close()
        queue.join_thread()
        with engine.database.session() as session:
            rows = session.scalars(select(OrderIntent)).all()
            assert sorted(row.state for row in rows) == ["rejected", "submitting"]
            risk = session.scalar(select(RiskState))
            assert risk.accepted_entries_today == 1 and risk.reserved_risk_usd == plan.worst_loss_account
    finally:
        await engine.shutdown()
        engine.database.close()
