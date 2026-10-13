"""TEST ONLY exact-ID reconciliation DTOs, no native SDK/broker calls."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import BotState, BrokerDeal, OrderIntent, RiskState, Trade
from tests.risk_helpers import OWNER, D, make_engine, safe_context
from trading.snapshots import broker_snapshot
from trading.types import (
    BrokerCommand,
    Deal,
    ExecutionResult,
    Operation,
    Position,
    ResultStatus,
    Side,
    TradingDisabled,
)


@pytest.fixture
async def sample(tmp_path):
    engine = await make_engine(tmp_path)
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order)
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
        D("5.36"),
        durable_simulation=True,
    )
    engine.authority.stage(
        command, account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
    )
    engine.authority.authorize(command, snapshot)
    position = Position(
        8001,
        777777,
        "EURUSD",
        Side.BUY,
        plan.order.volume,
        D("1.10014"),
        plan.order.sl,
        plan.order.tp,
        engine.clock.now(),
        engine.settings.mt5_magic_number,
    )
    deal = Deal(
        9002,
        9001,
        position.identifier,
        "EURUSD",
        "buy",
        "in",
        engine.clock.now(),
        position.volume,
        position.entry_price,
        D("0"),
        D("-0.07"),
        D("0"),
        D("0"),
        position.magic,
        "USD",
        "TEST",
        "TEST",
    )
    account = replace(account, balance=D("999.93"), equity=D("999.93"), margin_free=D("999.93"))
    ack = ExecutionResult(
        Operation.OPEN,
        command.idempotency_key,
        account.key,
        ResultStatus.FILLED,
        9001,
        9002,
        None,
        position.volume,
        position.entry_price,
    )
    yield engine, plan, command, account, position, deal, ack
    await engine.shutdown()
    engine.database.close()


def prove(sample, *, position=None, deal=None, ack=None, settled=frozenset()):
    engine, _, command, account, original_pos, original_deal, original_ack = sample
    engine.authority.on_result(command, ack or original_ack)
    return engine.logger.reconcile(
        account, (position or original_pos,), (deal or original_deal,), settled_orders=settled
    )


def test_order_or_deal_id_is_not_mislabeled_as_position_ticket(sample):
    summary = prove(sample)
    engine, _, _, _, position, _, ack = sample
    assert summary["completed_intents"] == 1 and summary["reserved_risk_usd"] == "0"
    owned = engine.logger.owned(engine.account_key)[0]
    assert owned.ticket == position.ticket != ack.order_ticket and owned.identifier == position.identifier
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert (
            row.state == "reconciled" and row.request["result"]["position_identifier"] == position.identifier
        )
        assert session.scalar(select(RiskState)).accepted_entries_today == 1


def test_deduplication_does_not_double_fees(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    for _ in range(4):
        engine.logger.reconcile(account, (position,), (deal,))
    with engine.database.session() as session:
        assert len(session.scalars(select(BrokerDeal)).all()) == 1
        assert session.scalar(select(Trade)).profit == D("-0.07")


@pytest.mark.parametrize(
    "change", [{"profit": D("1")}, {"order_ticket": 9004}, {"position_identifier": 999}, {"magic": 1}]
)
def test_changed_existing_deal_ticket_halts_instead_of_rewriting_evidence(sample, change):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    summary = engine.logger.reconcile(account, (position,), (replace(deal, **change),))
    assert summary["ledger_mismatch"]
    with engine.database.session() as session:
        assert session.scalar(select(BrokerDeal)).profit == 0
        assert session.get(BotState, 1).last_error == "ledger_mismatch"


@pytest.mark.parametrize("kind", [ResultStatus.UNKNOWN, ResultStatus.ACCEPTED, ResultStatus.FILLED])
def test_absent_history_and_positions_is_never_a_no_fill_proof(sample, kind):
    engine, _, command, account, _, _, ack = sample
    engine.authority.on_result(command, replace(ack, status=kind))
    summary = engine.logger.reconcile(replace(account, balance=D("1000"), equity=D("1000")), (), ())
    assert summary["unsettled_intents"] == 1 and D(summary["reserved_risk_usd"]) > 0
    assert not engine.logger.owned(engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)


def test_comment_time_and_magic_alone_never_prove_unknown_ownership(sample):
    engine, _, command, account, position, deal, ack = sample
    unknown = replace(ack, status=ResultStatus.UNKNOWN, order_ticket=0, deal_ticket=0)
    engine.authority.on_result(command, unknown)
    summary = engine.logger.reconcile(account, (position,), (deal,))
    assert summary["ledger_mismatch"] and summary["unsettled_intents"] == 1
    assert not engine.logger.owned(engine.account_key)


def test_unknown_with_exact_ids_and_complete_positive_deals_can_be_reconciled(sample):
    summary = prove(sample, ack=replace(sample[6], status=ResultStatus.UNKNOWN))
    assert not summary["ledger_mismatch"] and summary["unsettled_intents"] == 0
    with sample[0].database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "reconciled" and row.request["result"]["status"] == "filled"


def test_two_second_sdk_clock_precision_allowance_does_not_replace_id_proof(sample):
    position = replace(sample[4], time=sample[4].time - timedelta(seconds=1))
    deal = replace(sample[5], time=sample[5].time - timedelta(seconds=1))
    assert prove(sample, position=position, deal=deal)["completed_intents"] == 1


@pytest.mark.parametrize(
    "field,value", [("magic", 1), ("type", "sell"), ("symbol", "GBPUSD"), ("position_identifier", 888888)]
)
def test_entry_ownership_conflicts_are_not_adopted(sample, field, value):
    summary = prove(sample, deal=replace(sample[5], **{field: value}))
    assert summary["ledger_mismatch"] and not sample[0].logger.owned(sample[0].account_key)


def test_partial_without_final_order_proof_retains_risk_and_may_still_fill(sample):
    position, deal, ack = sample[4:]
    summary = prove(
        sample,
        position=replace(position, volume=D("0.01")),
        deal=replace(deal, volume=D("0.01")),
        ack=replace(ack, status=ResultStatus.PARTIAL, filled_volume=D("0.01")),
    )
    assert summary["unsettled_intents"] == 1 and D(summary["reserved_risk_usd"]) > 0
    assert not sample[0].logger.owned(sample[0].account_key)


def test_final_order_cancellation_can_prove_a_partial_fill_without_resending(sample):
    position, deal, ack = sample[4:]
    summary = prove(
        sample,
        position=replace(position, volume=D("0.01")),
        deal=replace(deal, volume=D("0.01")),
        ack=replace(ack, status=ResultStatus.PARTIAL, filled_volume=D("0.01")),
        settled=frozenset({9001}),
    )
    assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
    owned = sample[0].logger.owned(sample[0].account_key)[0]
    assert owned.original_volume == D("0.01") and owned.target_usd == sample[1].target_profit_usd / 2


def test_manual_netting_addition_invalidates_ownership_not_adopts_more_volume(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    added = replace(deal, ticket=9010, order_ticket=9011, volume=D("0.01"))
    summary = engine.logger.reconcile(account, (replace(position, volume=D("0.03")),), (deal, added))
    assert summary["ledger_mismatch"] and not engine.logger.owned(engine.account_key)
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "unknown"


def test_rollover_position_ticket_updates_only_under_stable_identifier_proof(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    rolled = replace(position, ticket=8123)
    engine.logger.reconcile(account, (rolled,), (deal,))
    assert engine.logger.owned(engine.account_key)[0].ticket == 8123
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).position_identifier == position.identifier


def test_partial_and_final_exits_sum_whole_trade_fees_swap_and_net(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    out1 = replace(
        deal,
        ticket=9003,
        order_ticket=9004,
        type="sell",
        entry="out",
        volume=D("0.01"),
        profit=D("1"),
        commission=D("-0.035"),
        swap=D("-0.01"),
    )
    account = replace(account, balance=account.balance + D("0.955"), equity=account.equity + D("0.955"))
    engine.logger.reconcile(account, (replace(position, volume=D("0.01")),), (deal, out1))
    owned = engine.logger.owned(engine.account_key)[0]
    assert owned.volume == D("0.01") and owned.original_volume == D("0.02")
    assert owned.entry_costs_account == D("0.885")  # Signed realized NET, including partial exits.
    out2 = replace(out1, ticket=9005, order_ticket=9006, profit=D("2"), swap=D("-0.02"))
    account = replace(account, balance=account.balance + D("1.945"), equity=account.equity + D("1.945"))
    summary = engine.logger.reconcile(account, (), (deal, out1, out2))
    assert not summary["ledger_mismatch"] and summary["closed_trades"] == 1
    with engine.database.session() as session:
        row = session.scalar(select(Trade))
        assert row.profit == D("2.83") and row.profit_usd == D("2.83")
        assert row.commission == D("-0.14") and row.swap == D("-0.03")


def test_missing_position_without_complete_exit_legs_halts_but_does_not_fake_close(sample):
    prove(sample)
    engine, _, _, account, _, deal, _ = sample
    summary = engine.logger.reconcile(account, (), (deal,))
    assert summary["ledger_mismatch"] and summary["closed_trades"] == 0
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "open"


def test_late_unknown_callback_cannot_downgrade_reconciled_fill(sample):
    prove(sample)
    engine, _, command, _, _, _, ack = sample
    engine.authority.on_result(command, replace(ack, status=ResultStatus.UNKNOWN))
    engine.authority.on_uncertain(command, "TEST late timeout")
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "reconciled" and row.request["result"]["status"] == "filled"
        assert session.scalar(select(RiskState)).reserved_risk_usd == 0


def test_wrong_result_key_or_operation_is_not_a_fill_proof(sample):
    engine, _, command, _, _, _, ack = sample
    for result in (replace(ack, idempotency_key="f" * 64), replace(ack, operation=Operation.CLOSE)):
        with pytest.raises(TradingDisabled):
            engine.authority.on_result(command, result)
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "submitting"


def test_late_known_fill_can_update_unknown_halt_but_owner_review_remains_required(sample):
    engine, _, command, account, position, deal, ack = sample
    engine.authority.on_uncertain(command, "TEST timeout")
    engine.authority.on_result(command, ack)
    summary = engine.logger.reconcile(account, (position,), (deal,))
    assert summary["unsettled_intents"] == 0
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    engine.control.acknowledge_recovery(
        OWNER, account_key=engine.account_key, broker_writes_quarantined=False
    )
    assert engine.database.status()["state"] == "paused"


def test_positive_acknowledgement_cannot_be_erased_by_a_contradictory_rejection(sample):
    engine, _, command, _, _, _, ack = sample
    engine.authority.on_result(command, ack)
    engine.authority.on_result(command, replace(ack, status=ResultStatus.REJECTED))
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "acknowledged" and row.request["reserved"] and row.request["counted"]
        assert row.request["result"]["status"] == "filled"
        assert session.get(BotState, 1).last_error == "ledger_mismatch"


@pytest.mark.parametrize("unknown_result", [False, True])
def test_definitive_rejection_after_unknown_releases_once_but_does_not_auto_resume(sample, unknown_result):
    engine, _, command, account, _, _, ack = sample
    if unknown_result:
        engine.authority.on_result(
            command,
            replace(ack, status=ResultStatus.UNKNOWN, order_ticket=0, deal_ticket=0, filled_volume=D("0")),
        )
    engine.authority.on_uncertain(command, "TEST timeout before definitive unsent result")
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "unknown"
        assert session.scalar(select(RiskState)).reserved_risk_usd > 0
    rejected = ExecutionResult(
        Operation.OPEN,
        command.idempotency_key,
        account.key,
        ResultStatus.REJECTED,
        reason="TEST definitely unsent",
    )
    for _ in range(3):
        engine.authority.on_result(command, rejected)
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        risk = session.scalar(select(RiskState))
        assert row.state == "rejected" and not row.request["reserved"] and not row.request["counted"]
        assert risk.reserved_risk_usd == 0 and risk.accepted_entries_today == 0
        assert session.get(BotState, 1).last_error == "unknown_execution"
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled, match="designated_halt"):
        engine.control.resume(OWNER, account_key=engine.account_key)
