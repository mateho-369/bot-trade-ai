"""Exact cost-inclusive metrics. Sampled equity drawdown is not an intratick worst-case guarantee."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import mean, stdev
from zoneinfo import ZoneInfo

from trading.types import ZERO


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: datetime
    balance: Decimal
    equity: Decimal
    credit: Decimal = ZERO
    cash_flow_total: Decimal = ZERO
    quotes_stale: bool = False

    def __post_init__(self):
        if self.time.tzinfo is None or type(self.quotes_stale) is not bool:
            raise ValueError("aware, explicit equity observation required")
        if (
            any(
                not isinstance(value, Decimal) or not value.is_finite()
                for value in (self.balance, self.equity, self.credit, self.cash_flow_total)
            )
            or self.credit < ZERO
        ):
            raise ValueError("finite exact equity observation required")

    def to_dict(self):
        return {
            "time": self.time.isoformat(),
            "balance": str(self.balance),
            "equity": str(self.equity),
            "credit": str(self.credit),
            "cash_flow_total": str(self.cash_flow_total),
            "quotes_stale": self.quotes_stale,
        }


def sampled_drawdown(points):
    peak, drawdown, absolute, previous_cash, previous_time = ZERO, ZERO, ZERO, None, None
    for point in points:
        if previous_time is not None and point.time < previous_time:
            raise ValueError("equity observations cannot move backwards")
        effective = point.equity - point.credit
        if previous_cash is None:
            previous_cash = point.cash_flow_total
        peak = max(effective, peak + point.cash_flow_total - previous_cash)
        loss = max(ZERO, peak - effective)
        absolute = max(absolute, loss)
        if peak > ZERO:
            drawdown = max(drawdown, loss * 100 / peak)
        previous_cash, previous_time = point.cash_flow_total, point.time
    return drawdown, absolute


def summarize(trades: list[dict], equity: list[EquityPoint], *, currency, timezone="UTC", gaps=0):
    if len(trades) > 200000 or len(equity) > 1000000 or type(gaps) is not int or gaps < 0:
        raise ValueError("bounded metric inputs required")
    closed = sorted(
        (row for row in trades if row["status"] == "closed"),
        key=lambda row: row.get("close_time") or row["open_time"],
    )
    if any(row["currency"] != currency for row in trades):
        raise ValueError("mixed-currency returns cannot be aggregated")
    pnls = [Decimal(row["net_profit_account"]) for row in closed]
    if any(not value.is_finite() for value in pnls):
        raise ValueError("finite exact trade returns required")
    gains = sum((max(ZERO, value) for value in pnls), ZERO)
    losses = sum((max(ZERO, -value) for value in pnls), ZERO)
    total = gains - losses
    wins, losing = sum(value > ZERO for value in pnls), sum(value < ZERO for value in pnls)
    drawdown, absolute = sampled_drawdown(equity)
    zone = ZoneInfo(timezone)
    days, previous_cash, previous_effective = {}, None, None
    for point in equity:
        effective = point.equity - point.credit
        adjusted = effective
        if previous_cash is not None:
            adjusted -= point.cash_flow_total - previous_cash
        # Compounded observed returns exclude external cash and credit; no fabricated weekend observations.
        if previous_effective is not None and previous_effective > ZERO:
            day = point.time.astimezone(zone).date().isoformat()
            days[day] = days.get(day, Decimal("1")) * adjusted / previous_effective
        previous_cash, previous_effective = point.cash_flow_total, effective
    returns = [float(value - 1) for value in days.values()]
    daily_ratio = None
    if len(returns) >= 20 and stdev(returns) > 0:
        daily_ratio = mean(returns) / stdev(returns)  # Deliberately NOT annualized or a forecast.
    streak, max_streak = 0, 0
    for value in pnls:
        streak = streak + 1 if value < ZERO else 0
        max_streak = max(max_streak, streak)
    entries_by_day = {}
    for row in trades:
        day = datetime.fromisoformat(row["open_time"]).astimezone(zone).date().isoformat()
        entries_by_day[day] = entries_by_day.get(day, 0) + 1
    return {
        "currency": currency,
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "winning_trades": wins,
        "losing_trades": losing,
        "breakeven_trades": len(closed) - wins - losing,
        "net_profit_account": str(total),
        "net_profit_usd": str(total) if currency == "USD" else None,
        "profit_usd_verified": currency == "USD",
        "gross_net_wins_account": str(gains),
        "gross_net_losses_account": str(losses),
        "profit_factor": str(gains / losses) if losses > ZERO else None,
        "profit_factor_defined": losses > ZERO,
        "win_rate_percent": str(Decimal(wins) * 100 / len(closed)) if closed else None,
        "expectancy_account": str(total / len(closed)) if closed else None,
        "commission_account": str(sum((Decimal(row["commission_account"]) for row in closed), ZERO)),
        "swap_account": str(sum((Decimal(row["swap_account"]) for row in closed), ZERO)),
        "max_drawdown_percent": str(drawdown),
        "max_drawdown_account": str(absolute),
        "drawdown_kind": "sampled_cash_credit_adjusted_equity",
        "max_consecutive_losses": max_streak,
        "unexplained_gaps": gaps,
        "equity_samples": len(equity),
        "stale_equity_samples": sum(point.quotes_stale for point in equity),
        "initial_equity_account": str(equity[0].equity) if equity else None,
        "final_equity_account": str(equity[-1].equity) if equity else None,
        "observed_daily_return_ratio_unannualized": daily_ratio,
        "observed_return_days": len(days),
        "entries_by_day": entries_by_day,
        "costs_included": True,
        "lookahead_free": True,
        "lookahead_claim_scope": "validated declared availability; not independent vendor authenticity proof",
    }
