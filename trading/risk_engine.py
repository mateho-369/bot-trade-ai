"""Deterministic risk vetoes and durable cash-adjusted daily/drawdown baselines."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import Database
from core.models import (
    AccountSnapshot,
    BotState,
    BrokerDeal,
    OrderIntent,
    RiskEvent,
    RiskState,
    Signal,
    Trade,
)
from core.settings import TIMEFRAME_MINUTES, OperatingMode, Settings
from trading.ai_controls import effective_limits
from trading.authorization import BrokerSnapshot
from trading.risk_types import DecisionContext, RiskDecision, RuntimeProfile
from trading.types import ZERO, AccountInfo, Clock, Position, SourceKind, TradingDisabled, aware_utc


class RiskEngine:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        self.zone = ZoneInfo(settings.trading_day_timezone)

    def observe(
        self,
        session: Session,
        account: AccountInfo,
        positions: tuple[Position, ...],
        *,
        intraday_history_complete: bool = False,
    ) -> RiskState:
        now, cfg = self.clock.now(), self.settings
        day = now.astimezone(self.zone).date()
        effective = account.equity - account.credit
        ledger = session.scalars(
            select(BrokerDeal).where(BrokerDeal.account_key == account.key, BrokerDeal.mode == cfg.mode.value)
        ).all()
        if len(ledger) > 200000:
            raise TradingDisabled("ledger exceeds the bounded risk observation; archive under review")
        if any(row.currency != account.currency for row in ledger):
            raise TradingDisabled("mixed-currency account ledger is invalid")
        cash_total = sum(
            (row.profit for row in ledger if row.type == "balance" and row.entry == "cash"), ZERO
        )

        def realized(deal):
            return (
                (deal.profit if deal.type not in {"balance", "credit"} else ZERO)
                + deal.commission
                + deal.swap
                + deal.fee
            )

        realized_total = sum((realized(deal) for deal in ledger), ZERO)
        pnl_today = sum(
            (realized(deal) for deal in ledger if deal.time.astimezone(self.zone).date() == day), ZERO
        )
        unsafe_cash = any(
            row.type in {"correction", "bonus"} or row.type.startswith("unknown") for row in ledger
        )
        row = session.scalar(
            select(RiskState).where(RiskState.account_key == account.key, RiskState.mode == cfg.mode.value)
        )
        if row is None:
            verified = (
                self.profile.data_source in {SourceKind.SYNTHETIC, SourceKind.TEST_SDK}
                or (intraday_history_complete and not positions)
            ) and not unsafe_cash
            baseline = effective - pnl_today
            row = RiskState(
                account_key=account.key,
                mode=cfg.mode.value,
                day=day,
                day_start_equity=max(ZERO, baseline),
                equity_high_water=max(ZERO, effective, baseline),
                cash_flow_total=cash_total,
                net_realized_today=pnl_today,
                accepted_entries_today=0,
                reserved_risk_usd=ZERO,
                daily_loss_latched=False,
                drawdown_latched=False,
                revision=0,
                metadata_json={
                    "baseline_verified": verified and baseline > ZERO,
                    "version": 1,
                    "last_effective_equity": str(effective),
                    "last_observed_at": now.isoformat(),
                    "currency": account.currency,
                    "last_balance": str(account.balance),
                    "balance_anchor_cash": str(cash_total),
                    "balance_anchor_realized": str(realized_total),
                    "balance_continuity_verified": True,
                },
            )
            session.add(row)
            session.flush()
            self.database.add_audit(
                session,
                "risk.baseline_created",
                "risk",
                {
                    "account": account.key,
                    "verified": verified,
                    "sampled_baseline": True,
                    "historical_peak_reconstructed": False,
                },
            )
        metadata = dict(row.metadata_json)
        prior_daily, prior_drawdown = row.daily_loss_latched, row.drawdown_latched
        if metadata.get("currency") not in (None, account.currency):
            raise TradingDisabled("risk baseline account currency changed")
        if day < row.day:
            raise TradingDisabled("trading clock/day moved backwards")
        last_when = aware_utc(datetime.fromisoformat(metadata.get("last_observed_at", now.isoformat())))
        if now < last_when:
            raise TradingDisabled("risk observation clock moved backwards")
        if (
            self.profile.data_source == SourceKind.MT5
            and (now - last_when).total_seconds() > cfg.risk_observation_max_age_seconds * 4
        ):
            metadata["observation_gap"] = True
        if unsafe_cash:
            metadata["unclassified_cash_flow"] = True
        if "last_balance" not in metadata:
            # Legacy baseline stays unverified pending explicit flat owner review.
            metadata.update(
                last_balance=str(account.balance),
                balance_anchor_cash=str(cash_total),
                balance_anchor_realized=str(realized_total),
            )
        expected_balance = (
            Decimal(metadata["last_balance"])
            + cash_total
            - Decimal(metadata["balance_anchor_cash"])
            + realized_total
            - Decimal(metadata["balance_anchor_realized"])
        )
        consistent = abs(account.balance - expected_balance) <= Decimal("0.00001")
        metadata["balance_continuity_verified"] = consistent
        if consistent:
            metadata.update(
                last_balance=str(account.balance),
                balance_anchor_cash=str(cash_total),
                balance_anchor_realized=str(realized_total),
            )
        else:
            if not metadata.get("unexplained_balance_change"):
                self.database.add_audit(
                    session, "risk.balance_continuity_halt", "risk", {"account": account.key}
                )
                session.add(
                    RiskEvent(
                        time=now,
                        event="balance_continuity_halt",
                        details={},
                        account_key=account.key,
                        mode=cfg.mode.value,
                    )
                )
            metadata.update(unexplained_balance_change=True, baseline_verified=False)
            state = session.get(BotState, 1)
            if state is not None:
                if not state.kill_switch_active:
                    state.desired_state = "paused"
                state.last_error, state.revision = "ledger_mismatch", state.revision + 1
        delta_cash = cash_total - row.cash_flow_total
        row.equity_high_water = max(ZERO, row.equity_high_water + delta_cash)
        row.day_start_equity = max(ZERO, row.day_start_equity + delta_cash)
        if day != row.day:
            # Carry the last observed value BEFORE new-day marks/fees; do not
            # reset to post-loss equity and erase an overnight gap.
            row.day = day
            row.day_start_equity = max(
                ZERO, Decimal(metadata.get("last_effective_equity", str(effective))) + delta_cash
            )
            row.daily_loss_latched = False
        if consistent:
            row.equity_high_water = max(row.equity_high_water, effective)
            if (
                row.day_start_equity <= ZERO
                or (row.day_start_equity - effective) * 100 / row.day_start_equity
                >= cfg.max_daily_loss_percent
            ):
                row.daily_loss_latched = True
            if (
                row.equity_high_water <= ZERO
                or (row.equity_high_water - effective) * 100 / row.equity_high_water
                >= cfg.max_drawdown_percent
            ):
                row.drawdown_latched = True
        intents = session.scalars(
            select(OrderIntent).where(
                OrderIntent.account_key == account.key, OrderIntent.mode == cfg.mode.value
            )
        ).all()
        row.accepted_entries_today = sum(
            1
            for item in intents
            if item.request.get("counted") is True and item.request.get("count_day") == day.isoformat()
        )
        row.reserved_risk_usd = sum(
            (
                Decimal(item.request.get("reserved_risk_usd", "0"))
                for item in intents
                if item.request.get("reserved") is True
            ),
            ZERO,
        )
        row.cash_flow_total, row.net_realized_today = cash_total, pnl_today
        row.revision += 1
        metadata.update(last_observed_at=now.isoformat(), currency=account.currency)
        if consistent:
            metadata["last_effective_equity"] = str(effective)
        if intraday_history_complete:
            metadata["history_complete_through"] = now.isoformat()
        state = session.get(BotState, 1)
        if row.daily_loss_latched or row.drawdown_latched:
            if state is not None and state.desired_state == "running":
                state.desired_state, state.revision = "paused", state.revision + 1
            for flag, previous, event in (
                (row.daily_loss_latched, prior_daily, "daily_loss_latched"),
                (row.drawdown_latched, prior_drawdown, "drawdown_latched"),
            ):
                if flag and not previous:
                    session.add(
                        RiskEvent(
                            time=now,
                            event=event,
                            details={"sampled_peak_only": True},
                            account_key=account.key,
                            mode=cfg.mode.value,
                        )
                    )
                    self.database.add_audit(session, "risk." + event, "risk", {"account": account.key})

        metadata["observed_position_count"] = len(positions)
        row.metadata_json = metadata
        session.add(
            AccountSnapshot(
                time=now,
                account_key=account.key,
                mode=cfg.mode.value,
                currency=account.currency,
                balance=account.balance,
                equity=account.equity,
                margin=account.margin,
                free_margin=account.margin_free,
                cash_flow_total=cash_total,
                metadata_json={
                    "version": 1,
                    "credit": str(account.credit),
                    "source": self.profile.data_source.value,
                    "code_hash": self.profile.code_hash,
                    "model_sha256": self.profile.model_sha256,
                    "strategy_config_hash": cfg.strategy_fingerprint(),
                },
            )
        )
        session.flush()
        return row

    def evaluate(
        self,
        session: Session,
        command,
        snapshot: BrokerSnapshot,
        context: DecisionContext,
        row: RiskState,
        *,
        self_reservation: Decimal = ZERO,
        self_counted: bool = False,
    ) -> RiskDecision:
        cfg, now, account = self.settings, self.clock.now(), snapshot.account
        if not isinstance(context, DecisionContext):
            return RiskDecision(False, ("invalid_decision_context",))
        reasons = []
        state = session.get(BotState, 1)
        if state is None or state.desired_state != "running":
            reasons.append("paused")
        if state is None or state.kill_switch_active:
            reasons.append("kill_switch")
        if state is not None and state.last_error:
            reasons.append("recovery_halt")
        if row.daily_loss_latched:
            reasons.append("daily_loss_latch")
        if row.drawdown_latched:
            reasons.append("drawdown_latch")
        if not row.metadata_json.get("baseline_verified"):
            reasons.append("unknown_baseline")
        if (
            row.metadata_json.get("observation_gap")
            or row.metadata_json.get("unclassified_cash_flow")
            or row.metadata_json.get("unexplained_balance_change")
            or not row.metadata_json.get("balance_continuity_verified")
        ):
            reasons.append("unreviewed_observation_or_cash")
        if account.quotes_stale:
            reasons.append("stale_held_exposure")
        if account.currency != cfg.account_currency:
            reasons.append("account_currency")
        if self.profile.data_source == SourceKind.HISTORICAL and cfg.mode != OperatingMode.BACKTEST:
            reasons.append("historical_backtest_scope")
        if snapshot.data_source != self.profile.data_source or context.source != self.profile.data_source:
            reasons.append("data_provenance")
        if (
            self.profile.data_source == SourceKind.MT5
            and cfg.mode == OperatingMode.PAPER
            and not snapshot.durable_simulation
        ):
            reasons.append("non_durable_paper")
        if not -2 <= (now - snapshot.observed_at).total_seconds() <= cfg.max_tick_age_seconds:
            reasons.append("stale_broker_snapshot")
        if not -2 <= (now - context.observed_at).total_seconds() <= cfg.order_max_age_seconds:
            reasons.append("stale_signal")
        if not 0 <= (now - context.bar_closed_at).total_seconds() <= cfg.max_candle_age_seconds:
            reasons.append("unfinished_or_stale_candle")
        if context.signal_score < cfg.min_signal_score:
            reasons.append("low_signal_score")
        if context.ai_confidence < cfg.ai_confidence_threshold:
            reasons.append("low_ai_confidence")
        order = command.order
        if order is None:
            raise TradingDisabled("risk evaluation requires an entry order")
        logical = next(
            (name for name in cfg.symbols if cfg.symbol_aliases.get(name, name) == order.symbol), None
        )
        if logical is None:
            reasons.append("owner_disabled_symbol")
        elif not cfg.symbol_news_currencies.get(logical):
            reasons.append("unknown_news_exposure")
        if cfg.news_required_for_entry and not context.news.allows(cfg, now, logical):
            reasons.append("unknown_stale_or_unsafe_news")
        if logical is not None and (context.news.managed or self.profile.data_source == SourceKind.MT5):
            from news.evidence import verify_window

            if not verify_window(
                session, context.news, logical_symbol=logical, settings=cfg, profile=self.profile, now=now
            ):
                reasons.append("unbound_revoked_or_fixture_news")
        if (
            self.profile.data_source in {SourceKind.MT5, SourceKind.HISTORICAL}
            or context.signal_id is not None
        ):
            signal = session.get(Signal, context.signal_id) if context.signal_id else None
            if (
                signal is None
                or signal.mode != cfg.mode.value
                or signal.symbol != order.symbol
                or signal.direction != order.side.value
                or signal.config_hash != cfg.safety_fingerprint()
                or signal.final_decision != "approved"
                or signal.strategy != order.strategy
                or signal.timeframe != cfg.primary_timeframe
                or abs((signal.time - context.observed_at).total_seconds()) > 2
                or signal.bar_time > context.bar_closed_at
                or (context.bar_closed_at - signal.bar_time).total_seconds()
                < TIMEFRAME_MINUTES[cfg.primary_timeframe] * 60
                or signal.features_json.get("bar_closed_at") != context.bar_closed_at.isoformat()
                or signal.features_json.get("code_hash") != self.profile.code_hash
                or signal.features_json.get("model_sha256") != self.profile.model_sha256
                or signal.features_json.get("news_hash") != context.news.evidence_hash
                or signal.score != context.signal_score
                or signal.ai_score != context.ai_confidence
                or signal.features_json.get("source") != self.profile.data_source.value
                or signal.features_json.get("decision_digest") != context.digest
            ):
                reasons.append("unbound_persisted_signal")
            if signal is not None and signal.features_json.get("signal_format") == "reflex-signal-v1":
                # Generated strategies cannot change their structural stop, chase a
                # different regime or outlive a newly stale AI/news review pre-send.
                try:
                    from strategy.signal_store import SignalStore

                    store = SignalStore(self.database, cfg, self.clock, self.profile)
                    store.approved_context(signal)
                    values = signal.features_json
                    reviewed_at = datetime.fromisoformat(values["ai_review"]["observed_at"])
                    if not -2 <= (now - reviewed_at).total_seconds() <= cfg.order_max_age_seconds:
                        reasons.append("stale_bound_ai_review")
                    atr = Decimal(values["atr"])
                    close = Decimal(values["bar_close_price"])
                    if not atr.is_finite() or not close.is_finite() or min(atr, close) <= ZERO:
                        raise ValueError
                    if order.sl != Decimal(values["stop_price"]):
                        reasons.append("strategy_stop_changed")
                    if abs(snapshot.tick.entry(order.side) - close) > atr * cfg.strategy_max_entry_drift_atr:
                        reasons.append("strategy_entry_price_chasing")
                    if (snapshot.tick.ask - snapshot.tick.bid) / atr > cfg.volatility_max_spread_atr_ratio:
                        reasons.append("strategy_spread_to_atr")
                except (TradingDisabled, KeyError, ValueError, TypeError, ArithmeticError):
                    reasons.append("unbound_strategy_proposal")
            elif self.profile.data_source in {SourceKind.MT5, SourceKind.HISTORICAL}:
                reasons.append("unbound_persisted_signal")
            prior = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == account.key,
                    OrderIntent.mode == cfg.mode.value,
                    OrderIntent.idempotency_key != command.idempotency_key,
                    OrderIntent.state.not_in(("rejected", "canceled")),
                )
            ).all()
            if any(
                item.request.get("authorized") is True
                and item.request.get("context", {}).get("signal_id") == context.signal_id
                for item in prior
                if item.request.get("context")
            ):
                reasons.append("signal_already_reserved_or_executed")
        # Layer-1 AI-dynamic limits (owner defaults unless the AI is available) inside layer-2 caps.
        limits = effective_limits(self.database, cfg, self.clock, session=session)
        percentage = context.risk_percent if context.risk_percent is not None else limits.risk_percent
        if percentage > max(cfg.effective_risk_percent, limits.risk_percent):
            reasons.append("risk_escalation")
        budget = account.risk_capital * min(percentage, limits.risk_percent) / Decimal("100")
        risk, margin = snapshot.worst_loss_account, snapshot.required_margin_account
        if risk <= ZERO or risk > budget:
            reasons.append("entry_risk_cap")
        if margin < ZERO or margin > min(
            account.margin_free, account.risk_capital * cfg.max_margin_usage_percent / 100 - account.margin
        ):
            reasons.append("margin_cap")
        if risk > ZERO and snapshot.expected_reward_account / risk < cfg.min_net_reward_risk:
            reasons.append("net_reward_risk")
        if row.accepted_entries_today - int(self_counted) >= limits.max_daily_trades:
            reasons.append("daily_entry_count")
        if len(snapshot.positions) >= limits.max_open_positions:
            reasons.append("position_cap")
        if any(position.symbol == order.symbol for position in snapshot.positions):
            reasons.append("no_averaging")
        tracked = {
            trade.position_identifier: trade
            for trade in session.scalars(
                select(Trade).where(
                    Trade.account_key == account.key, Trade.mode == cfg.mode.value, Trade.status == "open"
                )
            ).all()
        }
        for position in snapshot.positions:
            trade = tracked.get(position.identifier)
            if (
                trade is None
                or trade.ticket != position.ticket
                or trade.volume != position.volume
                or trade.symbol != position.symbol
                or trade.direction != position.side.value
                or position.magic != cfg.mt5_magic_number
                or trade.features_json.get("execution", {}).get("magic") != cfg.mt5_magic_number
                or position.sl <= ZERO
            ):
                reasons.append("foreign_untracked_or_unprotected_position")
                break
        if set(tracked) != {position.identifier for position in snapshot.positions}:
            reasons.append("unreconciled_position_ledger")
        values = {value.identifier: value.loss_account for value in snapshot.position_risks}
        if set(values) != {position.identifier for position in snapshot.positions}:
            reasons.append("missing_position_risk")
        usd_asset = snapshot.usd_asset_rate if account.currency != "USD" else Decimal("1")
        if account.currency != "USD" and (usd_asset is None or usd_asset <= ZERO):
            reasons.append("missing_usd_conversion")
            reserved_account = ZERO
        else:
            reserved_account = max(ZERO, row.reserved_risk_usd - self_reservation) / usd_asset
        aggregate = risk + sum(values.values(), ZERO) + reserved_account
        if aggregate > account.risk_capital * cfg.max_total_open_risk_percent / 100:
            reasons.append("aggregate_risk_cap")
        effective = account.equity - account.credit
        remaining_day_loss = max(
            ZERO,
            row.day_start_equity * cfg.max_daily_loss_percent / 100
            - max(ZERO, row.day_start_equity - effective),
        )
        if aggregate > remaining_day_loss:
            reasons.append("daily_loss_headroom")
        if row.reserved_risk_usd > self_reservation:
            reasons.append("unsettled_risk_reservation")
        return RiskDecision(not reasons, tuple(dict.fromkeys(reasons)), risk, aggregate, budget)

    def record_decision(
        self, session: Session, account_key: str, decision: RiskDecision, request_hash: str
    ) -> None:
        details = {
            "approved": decision.approved,
            "reasons": list(decision.reasons),
            "request_hash": request_hash,
            "risk_account": str(decision.risk_account),
            "aggregate_risk_account": str(decision.aggregate_risk_account),
        }
        session.add(
            RiskEvent(
                time=self.clock.now(),
                event="entry_approved" if decision.approved else "entry_vetoed",
                details=details,
                mode=self.settings.mode.value,
                account_key=account_key,
            )
        )
        self.database.add_audit(session, "risk.entry_decision", "risk", details)
