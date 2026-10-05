"""Exact cost-inclusive PF, cash/credit-adjusted sampled drawdown and no fake dollars/infinity."""

from datetime import timedelta
from decimal import Decimal

import pytest

from backtesting.metrics import EquityPoint, sampled_drawdown, summarize
from tests.risk_helpers import MOMENT


def trade(pnl, *, currency="USD", status="closed", when=MOMENT):
    return {
        "status": status,
        "currency": currency,
        "net_profit_account": str(pnl),
        "commission_account": "-0.10",
        "swap_account": "-0.01",
        "open_time": when.isoformat(),
        "close_time": (when + timedelta(seconds=1)).isoformat() if status == "closed" else None,
    }


def point(value, *, cash="0", credit="0", seconds=0, stale=False):
    return EquityPoint(
        MOMENT + timedelta(seconds=seconds),
        Decimal(value),
        Decimal(value),
        Decimal(credit),
        Decimal(cash),
        stale,
    )


def test_zero_trade_is_valid_not_failure_not_infinite_pf_or_forced_minimum():
    metrics = summarize([], [point("1000")], currency="USD")
    assert metrics["closed_trades"] == 0 and metrics["profit_factor"] is None
    assert metrics["win_rate_percent"] is None and metrics["expectancy_account"] is None
    assert metrics["net_profit_usd"] == "0" and not metrics["entries_by_day"]
    assert metrics["costs_included"] and metrics["lookahead_free"]


@pytest.mark.parametrize(
    "pnls,pf,expectancy,wins,losses",
    [
        (["5", "-2", "0"], "2.5", "1", 1, 1),
        (["5", "5"], None, "5", 2, 0),
        (["-2", "-3"], "0", "-2.5", 0, 2),
        (["0"], None, "0", 0, 0),
    ],
)
def test_pnl_metrics_use_net_returns_after_costs(pnls, pf, expectancy, wins, losses):
    metrics = summarize([trade(value) for value in pnls], [point("1000")], currency="USD")
    assert metrics["profit_factor"] == pf and metrics["expectancy_account"] == expectancy
    assert metrics["winning_trades"] == wins and metrics["losing_trades"] == losses
    assert Decimal(metrics["commission_account"]) == Decimal("-0.1") * len(pnls)


def test_nonusd_account_pnl_is_never_relabelled_usd():
    metrics = summarize([trade("5", currency="EUR")], [point("1000")], currency="EUR")
    assert metrics["net_profit_account"] == "5"
    assert metrics["net_profit_usd"] is None and not metrics["profit_usd_verified"]


def test_credit_and_deposits_are_not_profit_or_a_fake_equity_high():
    points = [
        point("1000"),
        point("1100", cash="100", seconds=1),
        point("1400", cash="100", credit="300", seconds=2),
        point("1290", cash="100", credit="300", seconds=3),
    ]
    dd, absolute = sampled_drawdown(points)
    assert dd == Decimal("10") and absolute == Decimal("110")


def test_withdrawal_adjusts_peak_without_erasing_return_losses():
    points = [point("1000"), point("900", cash="-100", seconds=1), point("810", cash="-100", seconds=2)]
    assert sampled_drawdown(points) == (Decimal("10"), Decimal("90"))


def test_open_marked_profit_not_closed_trade_pf_or_realized_returns():
    metrics = summarize(
        [trade("20", status="open"), trade("-2")], [point("1000"), point("1018", seconds=1)], currency="USD"
    )
    assert metrics["closed_trades"] == 1 and metrics["open_trades"] == 1
    assert metrics["net_profit_account"] == "-2"
    assert metrics["final_equity_account"] == "1018"


def test_loss_streak_is_ordered_by_close_not_entry_time():
    rows = [
        trade("-1", when=MOMENT),
        trade("3", when=MOMENT + timedelta(seconds=1)),
        trade("-1", when=MOMENT + timedelta(seconds=2)),
    ]
    rows[0]["close_time"] = (MOMENT + timedelta(seconds=5)).isoformat()
    metrics = summarize(rows, [point("1000")], currency="USD")
    assert metrics["max_consecutive_losses"] == 2


def test_sampled_stale_equity_is_disclosed_not_imputed_native_coverage():
    metrics = summarize([], [point("1000"), point("1000", seconds=5, stale=True)], currency="USD", gaps=1)
    assert metrics["stale_equity_samples"] == 1 and metrics["unexplained_gaps"] == 1
    assert metrics["drawdown_kind"] == "sampled_cash_credit_adjusted_equity"
    assert metrics["observed_daily_return_ratio_unannualized"] is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_equity_and_returns_rejected(value):
    with pytest.raises(ValueError):
        point(value)
    with pytest.raises(ValueError):
        summarize([trade(value)], [point("1000")], currency="USD")


def test_mixed_currency_backward_equity_and_bool_gap_cannot_coerce():
    with pytest.raises(ValueError):
        summarize([trade("1", currency="EUR")], [], currency="USD")
    with pytest.raises(ValueError):
        sampled_drawdown([point("1000", seconds=1), point("1000")])
    with pytest.raises(ValueError):
        summarize([], [], currency="USD", gaps=True)
