from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.local_operator import LocalOperator
from core.models import BotState, BrokerDeal, RiskState
from tests.risk_helpers import MOMENT, D, config, safe_context
from trading.authorization import BrokerSnapshot
from trading.mock_mt5 import MockMT5Client
from trading.risk_engine import RiskEngine
from trading.risk_types import RuntimeProfile
from trading.types import (
    BrokerCommand,
    ManualClock,
    Operation,
    Side,
    SourceKind,
    TradingDisabled,
)


@pytest.fixture
async def sample(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    broker = MockMT5Client(cfg, clock=clock)
    await broker.initialize()
    account = await broker.get_account_info()
    meta, tick = await broker.get_symbol_info("EURUSD"), await broker.get_tick("EURUSD")
    plan = (
        await broker._calculator().plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        if hasattr(broker, "_calculator")
        else None
    )
    from trading.order_calculator import OrderCalculator

    if plan is None:
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="TEST"
        )
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, clock.now(), order=plan.order)
    snapshot = BrokerSnapshot(
        account,
        meta,
        tick,
        (),
        D("4.86"),
        D("22"),
        D("5.36"),
        clock.now(),
        (),
        D("1"),
        D("1"),
        SourceKind.SYNTHETIC,
        True,
    )
    risk = RiskEngine(db, cfg, clock, RuntimeProfile.current(cfg, SourceKind.SYNTHETIC))
    with db.locked_session() as session:
        session.get(BotState, 1).desired_state = "running"  # TEST ONLY, never an execution permit.
        risk.observe(session, account, (), intraday_history_complete=True)
    yield cfg, db, clock, risk, snapshot, command
    await broker.shutdown()
    db.close()


def evaluate(sample, snapshot=None, context=None):
    cfg, db, clock, risk, original, command = sample
    with db.locked_session() as session:
        state = session.scalar(select(RiskState))
        return risk.evaluate(session, command, snapshot or original, context or safe_context(clock), state)


def test_quality_not_minimum_trade_quota(sample):
    assert evaluate(sample).approved
    denied = evaluate(sample, context=safe_context(sample[2], signal_score=0))
    assert not denied.approved and "low_signal_score" in denied.reasons


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"signal_score": 69}, "low_signal_score"),
        ({"ai_confidence": 69}, "low_ai_confidence"),
        ({"source": SourceKind.MT5}, "data_provenance"),
        ({"risk_percent": D("0.6")}, "risk_escalation"),
        ({"risk_percent": D("0.1")}, "entry_risk_cap"),
    ],
)
def test_context_vetoes(sample, change, reason):
    decision = evaluate(sample, context=safe_context(sample[2], **change))
    assert not decision.approved and reason in decision.reasons


@pytest.mark.parametrize(
    "field,delta,reason",
    [
        ("observed_at", -31, "stale_signal"),
        ("observed_at", 3, "stale_signal"),
        ("bar_closed_at", 1, "unfinished_or_stale_candle"),
        ("bar_closed_at", -421, "unfinished_or_stale_candle"),
    ],
)
def test_signal_chronology(sample, field, delta, reason):
    context = safe_context(sample[2], **{field: MOMENT + timedelta(seconds=delta)})
    assert reason in evaluate(sample, context=context).reasons


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("worst_loss_account", D("0"), "entry_risk_cap"),
        ("worst_loss_account", D("5.01"), "entry_risk_cap"),
        ("required_margin_account", D("301"), "margin_cap"),
        ("required_margin_account", D("-1"), "margin_cap"),
        ("expected_reward_account", D("4"), "net_reward_risk"),
        ("data_source", SourceKind.TEST_SDK, "data_provenance"),
    ],
)
def test_fresh_financial_vetoes(sample, field, value, reason):
    assert reason in evaluate(sample, snapshot=replace(sample[4], **{field: value})).reasons


@pytest.mark.parametrize("age", [-3, 11])
def test_snapshot_freshness(sample, age):
    snapshot = replace(sample[4], observed_at=MOMENT - timedelta(seconds=age))
    assert "stale_broker_snapshot" in evaluate(sample, snapshot=snapshot).reasons


def test_paused_and_killed_are_independent(sample):
    with sample[1].session() as session:
        state = session.get(BotState, 1)
        state.desired_state = "paused"
        state.kill_switch_active = True
    result = evaluate(sample)
    assert {"paused", "kill_switch"}.issubset(result.reasons)


def test_existing_unsettled_reservation_is_not_free_capital(sample):
    with sample[1].session() as session:
        session.scalar(select(RiskState)).reserved_risk_usd = D("11")
    decision = evaluate(sample)
    assert {"aggregate_risk_cap", "unsettled_risk_reservation"}.issubset(decision.reasons)


def test_last_permitted_count_before_send_excludes_only_own_reservation(sample):
    cfg, db, clock, risk, snapshot, command = sample
    with db.locked_session() as session:
        state = session.scalar(select(RiskState))
        state.accepted_entries_today = cfg.max_daily_trades
        state.reserved_risk_usd = D("4.86")
        denied = risk.evaluate(session, command, snapshot, safe_context(clock), state)
        allowed = risk.evaluate(
            session,
            command,
            snapshot,
            safe_context(clock),
            state,
            self_reservation=D("4.86"),
            self_counted=True,
        )
        assert "daily_entry_count" in denied.reasons and allowed.approved


def test_new_day_uses_pre_gap_equity_and_preserves_drawdown(sample):
    cfg, db, clock, risk, snapshot, _ = sample
    clock.advance(timedelta(days=1))
    account = replace(snapshot.account, equity=D("850"), margin_free=D("850"))
    with db.locked_session() as session:
        row = risk.observe(session, account, ())
        assert row.day_start_equity == D("1000")
        assert row.equity_high_water == D("1000")
        assert row.daily_loss_latched and row.drawdown_latched
    reopened = Database(cfg)
    try:
        reopened.verify_schema()
        with reopened.session() as session:
            assert session.scalar(select(RiskState)).drawdown_latched
    finally:
        reopened.close()


def test_daily_resets_do_not_reset_lifetime_high_water(sample):
    _, db, clock, risk, snapshot, _ = sample
    with db.locked_session() as session:
        risk.observe(session, replace(snapshot.account, equity=D("1200")), ())
    clock.advance(timedelta(days=1))
    with db.locked_session() as session:
        row = risk.observe(session, replace(snapshot.account, equity=D("1120")), ())
        assert row.day_start_equity == D("1200") and row.equity_high_water == D("1200")
        assert row.daily_loss_latched and not row.drawdown_latched


@pytest.mark.parametrize("cash", [D("500"), D("-200")])
def test_signed_external_balance_cash_adjusts_baselines_not_profit(sample, cash):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=99,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=cash,
            )
        )
        session.flush()
        account = replace(snapshot.account, balance=D("1000") + cash, equity=D("1000") + cash)
        row = risk.observe(session, account, ())
        assert row.day_start_equity == D("1000") + cash
        assert row.equity_high_water == D("1000") + cash
        assert row.net_realized_today == 0
        assert not row.daily_loss_latched and not row.drawdown_latched


def test_credit_is_excluded_from_risk_and_drawdown(sample):
    _, db, _, risk, snapshot, _ = sample
    funded = replace(snapshot.account, equity=D("1500"), credit=D("500"))
    with db.locked_session() as session:
        row = risk.observe(session, funded, ())
        assert row.equity_high_water == 1000 and not row.drawdown_latched
    assert funded.risk_capital == 1000
    assert (
        "entry_risk_cap"
        in evaluate(sample, snapshot=replace(snapshot, account=funded, worst_loss_account=D("5.01"))).reasons
    )


@pytest.mark.parametrize("kind", ["correction", "bonus", "unknown_42"])
def test_unclassified_cash_is_a_persistent_veto(sample, kind):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=98,
                time=MOMENT,
                type=kind,
                entry="cash",
                currency="USD",
                profit=D("0"),
            )
        )
        session.flush()
        risk.observe(session, snapshot.account, ())
    assert "unreviewed_observation_or_cash" in evaluate(sample).reasons


def test_native_observation_gap_requires_review(sample):
    cfg, db, clock, _, snapshot, _ = sample
    risk = RiskEngine(db, cfg, clock, RuntimeProfile.current(cfg, SourceKind.MT5))
    clock.advance(timedelta(seconds=121))
    with db.locked_session() as session:
        row = risk.observe(session, snapshot.account, ())
        assert row.metadata_json["observation_gap"] is True


def test_dollars_use_signed_verified_fx(sample):
    snapshot = replace(
        sample[4],
        account=replace(sample[4].account, currency="EUR"),
        usd_asset_rate=D("1.10"),
        usd_liability_rate=D("1.11"),
    )
    assert snapshot.dollars(D("5")) == D("5.50")
    assert snapshot.dollars(D("-5")) == D("-5.55")
    with pytest.raises(TradingDisabled):
        replace(snapshot, usd_asset_rate=None).dollars(D("5"))


def test_daily_remaining_headroom_counts_all_risk(sample):
    with sample[1].session() as session:
        row = session.scalar(select(RiskState))
        row.day_start_equity = D("970") / D("0.974")  # Remaining loss headroom < new trade risk.
    assert (
        "daily_loss_headroom"
        in evaluate(
            sample, snapshot=replace(sample[4], account=replace(sample[4].account, equity=D("970")))
        ).reasons
    )


@pytest.mark.parametrize("cash", [D("500"), D("-200")])
def test_unreported_balance_change_is_not_profit_or_a_fake_high_water(sample, cash):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        changed = replace(snapshot.account, balance=D("1000") + cash, equity=D("1000") + cash)
        row = risk.observe(session, changed, ())
        assert row.metadata_json["unexplained_balance_change"]
        assert not row.metadata_json["balance_continuity_verified"]
        assert not row.metadata_json["baseline_verified"]
        assert row.equity_high_water == 1000
        assert (
            not row.daily_loss_latched and not row.drawdown_latched
        )  # Unknown cash is NOT classified as trading P&L.


def test_delayed_deposit_ledger_does_not_double_adjust_peak_and_requires_review(sample):
    cfg, db, clock, risk, snapshot, _ = sample
    changed = replace(snapshot.account, balance=D("1500"), equity=D("1500"))
    with db.locked_session() as session:
        risk.observe(session, changed, ())
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=999,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=D("500"),
            )
        )
        session.flush()
        row = risk.observe(session, changed, (), intraday_history_complete=True)
        assert row.metadata_json["balance_continuity_verified"]
        assert row.equity_high_water == 1500 and row.day_start_equity == 1500
        assert not row.metadata_json["baseline_verified"]
    from trading.runtime_state import RuntimeControl

    control = RuntimeControl(db, cfg, clock)
    control.claim()
    operator = LocalOperator.current()
    control.review_flat_baseline(
        operator, account_key=snapshot.account.key, confirm="REVIEW_SAMPLED_BASELINE"
    )
    control.acknowledge_recovery(operator, account_key=snapshot.account.key, broker_writes_quarantined=False)
    control.resume(operator, account_key=snapshot.account.key)
    control.release()


def test_deposit_fee_is_account_cost_not_external_capital(sample):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=997,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=D("500"),
                fee=D("-5"),
            )
        )
        session.flush()
        changed = replace(snapshot.account, balance=D("1495"), equity=D("1495"))
        row = risk.observe(session, changed, ())
        assert row.cash_flow_total == 500 and row.net_realized_today == -5
        assert row.day_start_equity == 1500 and row.metadata_json["balance_continuity_verified"]


def test_same_day_and_new_day_reservations_do_not_vanish_with_the_counter_reset(sample):
    _, db, clock, risk, snapshot, command = sample
    from core.models import OrderIntent

    with db.locked_session() as session:
        session.add(
            OrderIntent(
                idempotency_key=command.idempotency_key,
                time=MOMENT,
                expires_at=MOMENT + timedelta(seconds=30),
                mode="paper",
                account_key=snapshot.account.key,
                symbol="EURUSD",
                direction="buy",
                state="unknown",
                config_hash="a" * 64,
                request={
                    "reserved": True,
                    "counted": True,
                    "count_day": MOMENT.date().isoformat(),
                    "reserved_risk_usd": "4.86",
                },
            )
        )
        session.flush()
        row = risk.observe(session, snapshot.account, ())
        assert row.accepted_entries_today == 1 and row.reserved_risk_usd == D("4.86")
    clock.advance(timedelta(days=1))
    with db.locked_session() as session:
        row = risk.observe(session, snapshot.account, ())
        assert row.accepted_entries_today == 0 and row.reserved_risk_usd == D("4.86")
