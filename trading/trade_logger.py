"""Positive-proof ownership, deduplicated fill legs and whole-trade accounting.

An order/deal number is NOT a position ticket. Empty history is NOT a no-fill
proof. Unexplained additions, reversals, missing exits or ID conflicts halt entry.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from core.database import Database
from core.models import BotState, BrokerDeal, OrderIntent, RiskEvent, Trade
from core.security import sanitize_text
from core.settings import Settings
from trading.execution_authority import execution_dict, execution_from_dict
from trading.risk_engine import RiskEngine
from trading.risk_types import OwnedTrade, RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.types import ZERO, AccountInfo, Clock, Deal, Position, ResultStatus, RiskViolation


class TradeLogger:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        control: RuntimeControl,
        profile: RuntimeProfile,
    ):
        self.database, self.settings, self.clock, self.control, self.profile = (
            database,
            settings,
            clock,
            control,
            profile,
        )
        self.risk = RiskEngine(database, settings, clock, profile)

    def history_start(self, account_key: str):
        with self.database.session() as session:
            intents = session.scalars(
                select(OrderIntent.time).where(
                    OrderIntent.account_key == account_key, OrderIntent.mode == self.settings.mode.value
                )
            ).all()
        zone = self.risk.zone
        local = self.clock.now().astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return min([midnight, *intents])

    @staticmethod
    def _deal_matches(row: BrokerDeal, deal: Deal) -> bool:
        raw = asdict(deal)
        raw.pop("reason")
        raw["position_identifier"] = raw["position_identifier"] or None
        raw["order_ticket"] = raw["order_ticket"] or None
        raw["comment"] = row.comment  # Sanitized text is NEVER identity evidence.
        for name, value in raw.items():
            if isinstance(value, Decimal):
                value = value.quantize(Decimal("1e-12") if name == "price" else Decimal("1e-8"))
            if getattr(row, name) != value:
                return False
        return True

    def reconcile(
        self,
        account: AccountInfo,
        positions: tuple[Position, ...],
        deals: tuple[Deal, ...],
        *,
        shadow_cache: dict | None = None,
        stop_net_usd: dict[int, Decimal] | None = None,
        settled_orders: frozenset[int] = frozenset(),
        history_complete: bool = True,
    ) -> dict:
        cfg, now = self.settings, self.clock.now()
        if len({position.identifier for position in positions}) != len(positions):
            raise RiskViolation("duplicate position identifiers in broker snapshot")
        if len({deal.ticket for deal in deals}) != len(deals) or len(deals) > 100000:
            raise RiskViolation("duplicate/oversized broker deal response")
        if any(deal.currency != account.currency or deal.time > now for deal in deals):
            raise RiskViolation("future or mixed-currency broker deals")
        mismatch = False
        completed = 0
        with self.database.locked_session() as session:
            self.control.check(session.get(BotState, 1))
            existing = {
                row.ticket: row
                for row in session.scalars(
                    select(BrokerDeal).where(
                        BrokerDeal.account_key == account.key, BrokerDeal.mode == cfg.mode.value
                    )
                ).all()
            }
            for deal in deals:
                if deal.ticket in existing:
                    if not self._deal_matches(existing[deal.ticket], deal):
                        mismatch = True
                    continue
                values = asdict(deal)
                values.pop("reason")
                values["comment"] = sanitize_text(values["comment"], self.database.secrets)[:128]
                values["position_identifier"] = values["position_identifier"] or None
                values["order_ticket"] = values["order_ticket"] or None
                # Bind FIRST ingestion to this service's trusted clock, not
                # SQLAlchemy's wall-clock default. Native clock is SystemClock;
                # controlled synthetic clocks must not acquire future labels
                # merely because the host wall time has passed their fixture.
                # Existing ingestion time is NEVER refreshed on repeat reads.
                record = BrokerDeal(**values, account_key=account.key, mode=cfg.mode.value, ingested_at=now)
                session.add(record)
                existing[deal.ticket] = record
            session.flush()
            ledger = list(existing.values())
            live = {position.identifier: position for position in positions}
            intents = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == account.key, OrderIntent.mode == cfg.mode.value
                )
            ).all()
            trades = session.scalars(
                select(Trade).where(Trade.account_key == account.key, Trade.mode == cfg.mode.value)
            ).all()
            by_intent = {trade.order_intent_id: trade for trade in trades}
            if shadow_cache:
                for intent in intents:
                    hint = shadow_cache.get(intent.idempotency_key)
                    if hint and intent.state not in {"reconciled", "rejected", "canceled"}:
                        if hint[0] != intent.request.get("request_hash"):
                            mismatch = True
                            continue
                        result = execution_from_dict(hint[1])
                        if result.account_key != account.key:
                            mismatch = True
                            continue
                        payload = dict(intent.request)
                        payload["result"] = execution_dict(result)
                        intent.request = payload
                        intent.state = (
                            "rejected" if result.status == ResultStatus.REJECTED else "acknowledged"
                        )
                        self.database.add_audit(
                            session,
                            "shadow.durable_ack_recovered",
                            "reconcile",
                            {"key": intent.idempotency_key},
                        )
            # Create entries only from positive broker acknowledgement + exact deal/order IDs.
            for intent in intents:
                if intent.request.get("command", {}).get("operation") != "open" or intent.id in by_intent:
                    continue
                data = intent.request.get("result")
                if data is None or intent.state in {"rejected", "canceled"}:
                    continue
                result = execution_from_dict(data)
                if result.status not in {
                    ResultStatus.FILLED,
                    ResultStatus.PARTIAL,
                    ResultStatus.ACCEPTED,
                    ResultStatus.UNKNOWN,
                }:
                    continue
                candidates = [
                    row
                    for row in ledger
                    if row.type in {"buy", "sell"}
                    and row.entry == "in"
                    and (
                        (result.order_ticket > 0 and row.order_ticket == result.order_ticket)
                        or (result.deal_ticket > 0 and row.ticket == result.deal_ticket)
                    )
                ]
                if not candidates:
                    continue
                order_ids = {row.order_ticket for row in candidates}
                if len(order_ids) != 1 or None in order_ids:
                    mismatch = True
                    continue
                order_id = next(iter(order_ids))
                candidates = [
                    row
                    for row in ledger
                    if row.order_ticket == order_id and row.entry == "in" and row.type in {"buy", "sell"}
                ]
                ids = {row.position_identifier for row in candidates}
                order = intent.request["command"]["order"]
                expected = Decimal(order["volume"])
                volume = sum((row.volume for row in candidates), ZERO)
                if (
                    len(ids) != 1
                    or None in ids
                    or volume <= ZERO
                    or volume > expected
                    or any(
                        row.magic != cfg.mt5_magic_number
                        or row.symbol != intent.symbol
                        or row.type != intent.direction
                        or row.time < intent.time - timedelta(seconds=2)
                        for row in candidates
                    )
                    or (result.deal_ticket and result.deal_ticket not in {row.ticket for row in candidates})
                ):
                    mismatch = True
                    continue
                final = volume == expected or order_id in settled_orders
                if not final:
                    continue  # A partial/pending order could STILL acquire exposure.
                identifier = next(iter(ids))
                position = live.get(identifier)
                exits = [
                    row
                    for row in ledger
                    if row.position_identifier == identifier
                    and row.entry in {"out", "out_by"}
                    and row.type in {"buy", "sell"}
                ]
                exited = sum((row.volume for row in exits), ZERO)
                if position is None and (not history_complete or exited != volume):
                    continue  # Never use absence alone as a fill/close proof.
                if position and (
                    position.volume != volume - exited
                    or position.symbol != intent.symbol
                    or position.side.value != intent.direction
                    or position.magic != cfg.mt5_magic_number
                ):
                    mismatch = True
                    continue
                if any(trade.position_identifier == identifier for trade in trades):
                    mismatch = True
                    continue
                vwap = sum((row.price * row.volume for row in candidates), ZERO) / volume
                context = intent.request["context"]
                execution = {
                    "version": 1,
                    "magic": cfg.mt5_magic_number,
                    "intent_key": intent.idempotency_key,
                    "order_ticket": order_id,
                    "entry_deal_tickets": [row.ticket for row in candidates],
                    "original_volume": str(volume),
                    "original_sl": order["sl"],
                    "original_tp": order["tp"],
                    "planned_volume": str(expected),
                    "original_entry_vwap": str(vwap),
                    "realized_net_account": "0",
                    "code_hash": intent.request["code_hash"],
                    "model_sha256": intent.request["model_sha256"],
                    "data_source": intent.request.get("data_source"),
                    "strategy_config_hash": intent.request.get("strategy_config_hash"),
                    "profit_usd_verified": account.currency == "USD",
                    "sampled_peak_only": True,
                    "risk_is_authorization_estimate": True,
                }
                trade = Trade(
                    account_key=account.key,
                    mode=cfg.mode.value,
                    currency=account.currency,
                    ticket=position.ticket if position else None,
                    position_identifier=identifier,
                    order_intent_id=intent.id,
                    symbol=intent.symbol,
                    direction=intent.direction,
                    volume=position.volume if position else volume,
                    entry_price=position.entry_price if position else vwap,
                    sl=position.sl if position else Decimal(order["sl"]),
                    tp=position.tp if position else Decimal(order["tp"]),
                    open_time=min(row.time for row in candidates),
                    initial_risk_usd=Decimal(intent.request["reserved_risk_usd"]) * volume / expected,
                    target_profit_usd=Decimal(intent.request["target_usd"]) * volume / expected,
                    profit_lock_level=0,
                    strategy=order["strategy"],
                    signal_score=context["signal_score"],
                    ai_score=context["ai_confidence"],
                    config_hash=intent.config_hash,
                    features_json={"decision": context, "execution": execution},
                    status="open",
                )
                session.add(trade)
                session.flush()
                by_intent[intent.id] = trade
                trades.append(trade)
                payload = dict(intent.request)
                payload["reserved"] = False
                payload["result"] = execution_dict(
                    replace(
                        result,
                        status=ResultStatus.FILLED,
                        position_identifier=identifier,
                        filled_volume=volume,
                        filled_price=position.entry_price if position else vwap,
                        reason=result.reason
                        if result.status == ResultStatus.FILLED
                        else "positive_exact_deals_reconciled",
                    )
                )
                intent.request, intent.state = payload, "reconciled"
                completed += 1
                self.database.add_audit(
                    session,
                    "entry.ownership_proved",
                    "reconcile",
                    {
                        "key": intent.idempotency_key,
                        "position_identifier": identifier,
                        "actual_position_ticket": trade.ticket,
                        "fill_volume": str(volume),
                    },
                )
            # Whole-trade P&L sums ALL legs, not the closing deal alone.
            for trade in trades:
                if trade.status == "unknown":
                    continue
                meta = dict(trade.features_json.get("execution", {}))
                if meta.get("version") != 1:
                    mismatch = True
                    continue
                legs = [row for row in ledger if row.position_identifier == trade.position_identifier]
                entries = [row for row in legs if row.entry == "in" and row.type in {"buy", "sell"}]
                exits = [
                    row for row in legs if row.entry in {"out", "out_by"} and row.type in {"buy", "sell"}
                ]
                expected = Decimal(meta["original_volume"])
                exited = sum((row.volume for row in exits), ZERO)
                if (
                    {row.ticket for row in entries} != set(meta["entry_deal_tickets"])
                    or any(row.entry == "inout" or row.type.startswith("unknown") for row in legs)
                    or exited > expected
                ):
                    trade.status, mismatch = "unknown", True
                    continue
                net = sum((row.profit + row.commission + row.swap + row.fee for row in legs), ZERO)
                trade.profit, trade.commission = net, sum((row.commission + row.fee for row in legs), ZERO)
                trade.swap = sum((row.swap for row in legs), ZERO)
                trade.profit_usd = net if trade.currency == "USD" else ZERO
                meta.update(
                    realized_net_account=str(net),
                    profit_usd_verified=trade.currency == "USD",
                    historical_fx_required=trade.currency != "USD",
                )
                position = live.get(trade.position_identifier)
                if position:
                    if (
                        position.symbol != trade.symbol
                        or position.side.value != trade.direction
                        or position.magic != cfg.mt5_magic_number
                        or position.volume != expected - exited
                        or trade.status == "closed"
                    ):
                        trade.status, mismatch = "unknown", True
                    else:
                        trade.ticket, trade.volume, trade.sl, trade.tp = (
                            position.ticket,
                            position.volume,
                            position.sl,
                            position.tp,
                        )
                        trade.entry_price = position.entry_price  # True broker VWAP may be off-grid.
                elif exited == expected and exits and history_complete:
                    if trade.status != "closed":
                        trade.status, trade.close_time = "closed", max(row.time for row in exits)
                        trade.close_reason = "broker_reconciled_exit"
                        self.database.add_audit(
                            session,
                            "trade.closed_all_legs",
                            "reconcile",
                            {
                                "trade_id": trade.id,
                                "net_account": str(net),
                                "currency": trade.currency,
                                "usd_verified": meta["profit_usd_verified"],
                            },
                        )
                elif trade.status == "open":
                    mismatch = True
                features = dict(trade.features_json)
                features["execution"] = meta
                trade.features_json = features
            # Maintenance goal must be verified against actual owned state/exit legs.
            for intent in intents:
                if intent.state not in {"acknowledged", "unknown", "submitting"}:
                    continue
                command = intent.request.get("command", {})
                operation = command.get("operation")
                if operation == "open":
                    continue
                trade = next(
                    (
                        item
                        for item in trades
                        if item.position_identifier == command.get("position_identifier")
                    ),
                    None,
                )
                if trade is None or trade.status == "unknown":
                    continue
                position = live.get(trade.position_identifier)
                data = intent.request.get("result")
                positive = data and data["status"] in {"filled", "no_change"}
                verified = False
                if (
                    operation == "close"
                    and trade.status == "closed"
                    and trade.close_time >= intent.time - timedelta(seconds=2)
                ):
                    verified = True  # Complete exit legs, even if a server SL beat the close request.
                if operation == "protect" and positive and position:
                    wanted_sl, wanted_tp = command.get("sl"), command.get("tp")
                    verified = (wanted_sl is None or position.sl == Decimal(wanted_sl)) and (
                        wanted_tp is None or position.tp == Decimal(wanted_tp)
                    )
                    level = intent.request.get("lock_level", 0)
                    estimate = (stop_net_usd or {}).get(position.identifier)
                    if verified and level > trade.profit_lock_level:
                        if (
                            estimate is not None
                            and estimate >= trade.target_profit_usd * Decimal(str(level)) / 100
                        ):
                            trade.profit_lock_level = level
                            meta = dict(trade.features_json["execution"])
                            meta.update(
                                last_verified_lock_net_usd=str(estimate),
                                last_verified_lock_at=now.isoformat(),
                            )
                            trade.features_json = {**trade.features_json, "execution": meta}
                        else:
                            # Price changed, but do NOT claim an unverified dollar lock.
                            self.database.add_audit(
                                session, "trailing.lock_not_claimed", "reconcile", {"trade_id": trade.id}
                            )
                if verified:
                    intent.state = "reconciled"
                    completed += 1
                    self.database.add_audit(
                        session,
                        "maintenance.goal_verified",
                        "reconcile",
                        {
                            "key": intent.idempotency_key,
                            "operation": operation,
                            "broker_execution_verified": bool(positive),
                        },
                    )
            row = self.risk.observe(session, account, positions, intraday_history_complete=history_complete)
            unsettled = [item for item in intents if item.state in {"submitting", "acknowledged", "unknown"}]
            known = {trade.position_identifier for trade in trades if trade.status == "open"}
            if any(position.identifier not in known for position in positions):
                mismatch = True
            if unsettled and not mismatch:
                state = session.get(BotState, 1)
                if not state.kill_switch_active:
                    state.desired_state = "paused"
                if state.last_error is None:
                    state.last_error, state.revision = "unknown_execution", state.revision + 1
                    self.database.add_audit(
                        session, "reconcile.unsettled_halt", "reconcile", {"account": account.key}
                    )
            if mismatch:
                state = session.get(BotState, 1)
                if not state.kill_switch_active:
                    state.desired_state = "paused"
                state.last_error, state.revision = "ledger_mismatch", state.revision + 1
                session.add(
                    RiskEvent(
                        time=now,
                        event="ledger_mismatch",
                        details={"unsettled_count": len(unsettled)},
                        account_key=account.key,
                        mode=cfg.mode.value,
                    )
                )
                self.database.add_audit(
                    session, "reconcile.ledger_halt", "reconcile", {"account": account.key}
                )
            summary = {
                "completed_intents": completed,
                "unsettled_intents": len(unsettled),
                "ledger_mismatch": mismatch,
                "reserved_risk_usd": str(row.reserved_risk_usd),
                "closed_trades": sum(trade.status == "closed" for trade in trades),
            }
        return summary

    def owned(self, account_key: str) -> tuple[OwnedTrade, ...]:
        result = []
        with self.database.session() as session:
            rows = session.scalars(
                select(Trade).where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status == "open",
                )
            ).all()
            for row in rows:
                meta = row.features_json.get("execution", {})
                if meta.get("version") == 1 and row.ticket:
                    result.append(
                        OwnedTrade(
                            row.id,
                            row.ticket,
                            row.position_identifier,
                            row.symbol,
                            row.direction,
                            row.volume,
                            Decimal(meta["original_volume"]),
                            row.entry_price,
                            Decimal(meta["original_tp"]),
                            row.target_profit_usd,
                            Decimal(meta["realized_net_account"]),
                            row.profit_lock_level,
                            meta["intent_key"],
                        )
                    )
        return tuple(result)
