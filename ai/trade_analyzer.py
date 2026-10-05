"""Deterministic cost-inclusive closed-trade reports; comments/LLM P&L are never inputs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from core.database import Database
from core.models import BrokerDeal, OrderIntent, Trade
from core.security import sha256_json
from core.settings import Settings
from trading.types import Clock, TradingDisabled, aware_utc


class TradeAnalyzer:
    def __init__(self, database: Database, settings: Settings, clock: Clock):
        self.database, self.settings, self.clock = database, settings, clock

    def summary(
        self, account_key: str, *, start: datetime | None = None, end: datetime | None = None
    ) -> dict:
        finish = aware_utc(end or self.clock.now())
        begin = aware_utc(start) if start is not None else None
        if finish > self.clock.now() or begin is not None and begin >= finish:
            raise TradingDisabled("bounded nonfuture reporting interval required")
        with self.database.session() as session:
            query = select(Trade).where(
                Trade.account_key == account_key, Trade.mode == self.settings.mode.value
            )
            rows = session.scalars(
                query.order_by(Trade.id).limit(self.settings.model_max_dataset_rows + 1)
            ).all()
            if len(rows) > self.settings.model_max_dataset_rows:
                raise TradingDisabled("report exceeds cohort bound")
            ledger = session.scalars(
                select(BrokerDeal)
                .where(
                    BrokerDeal.account_key == account_key,
                    BrokerDeal.mode == self.settings.mode.value,
                )
                .order_by(BrokerDeal.time, BrokerDeal.ticket)
                .limit(100001)
            ).all()
            if len(ledger) > 100000:
                raise TradingDisabled("report ledger exceeds bound")
            grouped = defaultdict(list)
            for d in ledger:
                grouped[d.position_identifier].append(d)
            verified = []
            unverified = 0
            for row in rows:
                if (
                    row.status != "closed"
                    or row.close_time is None
                    or row.close_time > finish
                    or begin is not None
                    and row.close_time < begin
                ):
                    continue
                try:
                    meta = row.features_json["execution"]
                    intent = session.get(OrderIntent, row.order_intent_id)
                    legs = grouped[row.position_identifier]
                    entries = [d for d in legs if d.entry == "in" and d.type in {"buy", "sell"}]
                    exits = [d for d in legs if d.entry in {"out", "out_by"} and d.type in {"buy", "sell"}]
                    volume = Decimal(meta["original_volume"])
                    net = sum((d.profit + d.commission + d.swap + d.fee for d in legs), Decimal(0))
                    if (
                        intent is None
                        or intent.state != "reconciled"
                        or intent.account_key != account_key
                        or not entries
                        or not exits
                        or {d.ticket for d in entries} != set(meta["entry_deal_tickets"])
                        or sum((d.volume for d in entries), Decimal(0)) != volume
                        or sum((d.volume for d in exits), Decimal(0)) != volume
                        or net != row.profit
                        or max(d.time for d in exits) != row.close_time
                        or any(
                            d.currency != row.currency
                            or d.symbol != row.symbol
                            or d.magic != self.settings.mt5_magic_number
                            or d.type.startswith("unknown")
                            or d.entry == "inout"
                            for d in legs
                        )
                    ):
                        raise ValueError
                    verified.append(row)
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    unverified += 1
            currencies = {r.currency for r in verified}
            if len(currencies) > 1:
                raise TradingDisabled("mixed account currencies; no invented aggregate USD")
            currency = next(iter(currencies), self.settings.account_currency)
            net = sum((r.profit for r in verified), Decimal(0))
            wins = [r for r in verified if r.profit > 0]
            losses = [r for r in verified if r.profit < 0]
            gross_win = sum((r.profit for r in wins), Decimal(0))
            gross_loss = sum((-r.profit for r in losses), Decimal(0))
            segments = defaultdict(list)
            for row in verified:
                segments[(row.symbol, row.strategy)].append(row)
            detail = [
                {
                    "symbol": symbol,
                    "strategy": strategy,
                    "closed": len(items),
                    "net_account": str(sum((r.profit for r in items), Decimal(0))),
                    "win_rate": sum(r.profit > 0 for r in items) / len(items),
                }
                for (symbol, strategy), items in sorted(segments.items())
            ]
            charges = [
                d
                for d in grouped[None]
                if d.type not in {"balance", "credit"}
                and d.time <= finish
                and (begin is None or d.time >= begin)
            ]
            unallocated = sum((d.profit + d.commission + d.swap + d.fee for d in charges), Decimal(0))
            usd_verified = currency == "USD" and all(
                r.features_json["execution"].get("profit_usd_verified") is True for r in verified
            )
            return {
                "format": "reflex-trade-summary-v1",
                "account_scope_hash": sha256_json(account_key),
                "as_of": finish.isoformat(),
                "mode": self.settings.mode.value,
                "currency": currency,
                "closed": len(verified),
                "wins": len(wins),
                "losses": len(losses),
                "breakeven": len(verified) - len(wins) - len(losses),
                "unproven_closed": unverified,
                "open_or_unknown": sum(r.status != "closed" for r in rows),
                "net_account": str(net),
                "net_usd": str(net) if usd_verified else None,
                "profit_factor": str(gross_win / gross_loss) if gross_loss > 0 else None,
                "win_rate": len(wins) / len(verified) if verified else None,
                "unallocated_net_account": str(unallocated),
                "segments": detail[:30],
                "sampled_equity_drawdown": None,
                "not_stage_evidence": True,
                "warning": (
                    "Selected reconciled closed trades only; no future labels/LLM profits/unsampled drawdown."
                ),
            }
