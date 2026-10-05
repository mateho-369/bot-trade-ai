"""Durable write authority. A flag/AI/API body cannot issue a broker permit."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from core.database import Database
from core.models import BotState, OrderIntent, RiskEvent, RiskState, Trade
from core.settings import OperatingMode, Settings
from trading.authorization import BrokerSnapshot, WriteGrant
from trading.price_rules import protection_prices
from trading.risk_engine import RiskEngine
from trading.risk_types import (
    DecisionContext,
    PositionReview,
    RuntimeProfile,
    json_dict,
    position_review_hash,
)
from trading.runtime_state import TERMINAL_INTENT_STATES, RuntimeControl
from trading.stage_gate import StageGate
from trading.types import (
    ZERO,
    AccountInfo,
    BrokerCommand,
    Clock,
    ExecutionResult,
    Operation,
    ResultStatus,
    SourceKind,
    TradingDisabled,
    UncertainExecution,
)


def execution_dict(result: ExecutionResult) -> dict:
    return json_dict(asdict(result))


def execution_from_dict(data: dict) -> ExecutionResult:
    values = dict(data)
    values["operation"], values["status"] = Operation(values["operation"]), ResultStatus(values["status"])
    values["filled_volume"] = Decimal(values["filled_volume"])
    if values.get("filled_price") is not None:
        values["filled_price"] = Decimal(values["filled_price"])
    return ExecutionResult(**values)


class DurableWriteAuthority:
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
        self.stages = StageGate(database, settings, clock, profile)

    def stage(
        self,
        command: BrokerCommand,
        account: AccountInfo,
        *,
        context: DecisionContext | None = None,
        target_usd: Decimal = ZERO,
        review: PositionReview | None = None,
        lock_level: float = 0,
        expected_position_hash: str | None = None,
    ) -> ExecutionResult | None:
        if command.operation == Operation.OPEN and (
            context is None
            or not isinstance(target_usd, Decimal)
            or not target_usd.is_finite()
            or target_usd <= ZERO
        ):
            raise TradingDisabled("entry requires a complete trusted decision and original USD objective")
        if expected_position_hash is not None:
            from trading.types import valid_key

            valid_key(expected_position_hash)
            if command.operation != Operation.CLOSE:
                raise TradingDisabled("captured owner hash is only valid for a close")
        if not 0 <= lock_level <= 100:
            raise TradingDisabled("invalid desired lock level")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.control.check(state)
            previous = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if previous:
                if (
                    previous.account_key != account.key
                    or previous.mode != self.settings.mode.value
                    or previous.request.get("request_hash") != command.request_hash
                    or previous.request.get("expected_position_hash") != expected_position_hash
                ):
                    raise TradingDisabled("durable idempotency key changed account/mode/payload")
                result = previous.request.get("result")
                if result is not None:
                    return execution_from_dict(result)
                raise UncertainExecution("existing prepared/submitting intent cannot be resubmitted")
            position_trade = None
            if command.operation != Operation.OPEN:
                position_trade = session.scalar(
                    select(Trade).where(
                        Trade.account_key == account.key,
                        Trade.mode == self.settings.mode.value,
                        Trade.position_identifier == command.position_identifier,
                        Trade.ticket == command.ticket,
                        Trade.status == "open",
                    )
                )
                if position_trade is None:
                    raise TradingDisabled("maintenance must reference a durably reconciled owned trade")
            now = self.clock.now()
            order = command.order
            payload = {
                "version": 1,
                "request_hash": command.request_hash,
                "command": json_dict(asdict(command)),
                "session_id": self.control.session_id,
                "code_hash": self.profile.code_hash,
                "model_sha256": self.profile.model_sha256,
                "strategy_config_hash": self.settings.strategy_fingerprint(),
                "context": context.to_dict() if context else None,
                "target_usd": str(target_usd),
                "review": json_dict(asdict(review)) if review else None,
                "lock_level": lock_level,
                "expected_position_hash": expected_position_hash,
                "authorized": False,
                "reserved": False,
                "counted": False,
                "reserved_risk_usd": "0",
                "result": None,
            }
            row = OrderIntent(
                idempotency_key=command.idempotency_key,
                time=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self.settings.order_max_age_seconds),
                account_key=account.key,
                mode=self.settings.mode.value,
                symbol=order.symbol if order else position_trade.symbol,
                direction=order.side.value if order else position_trade.direction,
                request=payload,
                config_hash=self.settings.safety_fingerprint(),
                state="prepared",
            )
            session.add(row)
            self.database.add_audit(
                session,
                "intent.staged",
                "execution",
                {
                    "key": command.idempotency_key,
                    "operation": command.operation.value,
                    "account": account.key,
                    "request_hash": command.request_hash,
                },
            )
        return None

    def authorize(self, command: BrokerCommand, snapshot: BrokerSnapshot) -> WriteGrant:
        deny = None
        grant = None
        now, cfg = self.clock.now(), self.settings
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.control.check(state)
            intent = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if (
                intent is None
                or intent.state != "prepared"
                or intent.request.get("authorized") is True
                or intent.account_key != snapshot.account.key
                or intent.mode != cfg.mode.value
                or intent.config_hash != cfg.safety_fingerprint()
                or intent.expires_at <= now
                or intent.request.get("request_hash") != command.request_hash
                or intent.request.get("session_id") != self.control.session_id
                or intent.request.get("code_hash") != self.profile.code_hash
                or intent.request.get("model_sha256") != self.profile.model_sha256
            ):
                raise TradingDisabled("missing/expired/mismatched unique prepared intent; no broker permit")
            request = dict(intent.request)
            if snapshot.data_source != self.profile.data_source:
                deny = "broker snapshot data provenance changed"
            elif (
                snapshot.account.source in {SourceKind.SYNTHETIC, SourceKind.PAPER}
                and not snapshot.durable_simulation
            ):
                deny = "managed simulation requires atomic persistent state"
            elif command.operation == Operation.OPEN:
                risk_row = self.risk.observe(session, snapshot.account, snapshot.positions)
                context = DecisionContext.from_dict(request["context"])
                decision = self.risk.evaluate(session, command, snapshot, context, risk_row)
                if Decimal(request["target_usd"]) > snapshot.dollars(snapshot.expected_reward_account):
                    decision = replace(
                        decision, approved=False, reasons=decision.reasons + ("unachievable_original_target",)
                    )
                evidence = ()
                confirmed = False
                try:
                    evidence = self.stages.required_evidence(session, snapshot.account)
                    confirmed = cfg.mode != OperatingMode.LIVE or self.stages.live_confirmed(
                        session, snapshot.account, self.control.session_id, evidence
                    )
                    if not confirmed:
                        decision = replace(
                            decision, approved=False, reasons=decision.reasons + ("owner_live_confirmation",)
                        )
                except TradingDisabled:
                    decision = replace(
                        decision, approved=False, reasons=decision.reasons + ("stage_evidence",)
                    )
                other = session.scalar(
                    select(OrderIntent.id)
                    .where(
                        OrderIntent.account_key == snapshot.account.key,
                        OrderIntent.mode == cfg.mode.value,
                        OrderIntent.id != intent.id,
                        OrderIntent.state.in_(("submitting", "acknowledged", "unknown")),
                    )
                    .limit(1)
                )
                if other:
                    decision = replace(
                        decision, approved=False, reasons=decision.reasons + ("unsettled_execution",)
                    )
                self.risk.record_decision(session, snapshot.account.key, decision, command.request_hash)
                if not decision.approved:
                    deny = "risk/stage veto: " + ",".join(decision.reasons)
                else:
                    reserved_usd = -snapshot.dollars(-snapshot.worst_loss_account)
                    request.update(
                        authorized=True,
                        reserved=True,
                        counted=True,
                        reserved_risk_usd=str(reserved_usd),
                        count_day=now.astimezone(self.risk.zone).date().isoformat(),
                        risk_account=str(snapshot.worst_loss_account),
                        authorized_at=now.isoformat(),
                        evidence_ids=list(evidence),
                        account_currency=snapshot.account.currency,
                        data_source=self.profile.data_source.value,
                    )
                    risk_row.reserved_risk_usd += reserved_usd
                    risk_row.accepted_entries_today += 1
                    grant = WriteGrant(
                        snapshot.account.key,
                        command.request_hash,
                        cfg.safety_fingerprint(),
                        min(intent.expires_at, now + timedelta(seconds=cfg.order_max_age_seconds)),
                        command.order.volume,
                        snapshot.worst_loss_account,
                        snapshot.required_margin_account,
                        entry_gates_verified=True,
                        owner_live_confirmed=confirmed,
                    )
            else:
                matching = [
                    position
                    for position in snapshot.positions
                    if position.ticket == command.ticket
                    and position.identifier == command.position_identifier
                ]
                trade = session.scalar(
                    select(Trade).where(
                        Trade.account_key == snapshot.account.key,
                        Trade.mode == cfg.mode.value,
                        Trade.position_identifier == command.position_identifier,
                        Trade.status == "open",
                    )
                )
                if (
                    len(matching) != 1
                    or trade is None
                    or trade.ticket != command.ticket
                    or trade.volume != matching[0].volume
                    or trade.direction != matching[0].side.value
                    or trade.symbol != matching[0].symbol
                    or matching[0].magic != cfg.mt5_magic_number
                    or trade.features_json.get("execution", {}).get("magic") != cfg.mt5_magic_number
                    or (
                        request.get("expected_position_hash") is not None
                        and position_review_hash(matching[0]) != request["expected_position_hash"]
                    )
                ):
                    deny = "owned position ticket/identifier/volume/source does not match the durable ledger"
                else:
                    position = matching[0]
                    original_tp = Decimal(trade.features_json["execution"]["original_tp"])
                    extend = False
                    if command.operation == Operation.PROTECT:
                        try:
                            _, tp = protection_prices(command, position, snapshot.symbol, snapshot.tick, cfg)
                            extend = position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO
                            if extend:
                                review = (
                                    PositionReview.from_dict(request["review"])
                                    if request.get("review")
                                    else None
                                )
                                if (
                                    review is None
                                    or not review.allows_extension(
                                        cfg,
                                        now,
                                        self.profile.data_source,
                                        position=position,
                                        profile=self.profile,
                                    )
                                    or not self._position_news_valid(session, review, position)
                                ):
                                    raise TradingDisabled(
                                        "TP extension needs fresh news/volatility/momentum/AI review"
                                    )
                                if (
                                    abs(tp - position.entry_price)
                                    > abs(original_tp - position.entry_price) * cfg.tp_extension_factor
                                ):
                                    raise TradingDisabled(
                                        "extension exceeds immutable original-target distance"
                                    )
                        except Exception:
                            deny = "invalid/non-improving/frozen/unapproved protection modification"
                    if deny is None:
                        request.update(
                            authorized=True,
                            authorized_at=now.isoformat(),
                            account_currency=snapshot.account.currency,
                        )
                        grant = WriteGrant(
                            snapshot.account.key,
                            command.request_hash,
                            cfg.safety_fingerprint(),
                            min(intent.expires_at, now + timedelta(seconds=cfg.order_max_age_seconds)),
                            ZERO,
                            ZERO,
                            ZERO,
                            owned_position_verified=True,
                            allow_tp_extension=extend,
                            original_tp=original_tp,
                        )
            if deny is not None:
                rejected = ExecutionResult(
                    command.operation,
                    command.idempotency_key,
                    snapshot.account.key,
                    ResultStatus.REJECTED,
                    reason="authority_veto_no_send",
                )
                request.update(result=execution_dict(rejected), reserved=False, counted=False)
                intent.state, intent.last_error = "rejected", deny
                self.database.add_audit(
                    session, "intent.authority_veto", "risk", {"key": command.idempotency_key, "reason": deny}
                )
            else:
                intent.state = "submitting"
                self.database.add_audit(
                    session,
                    "intent.reserved_before_send",
                    "execution",
                    {
                        "key": command.idempotency_key,
                        "operation": command.operation.value,
                        "risk_usd": request["reserved_risk_usd"],
                    },
                )
            intent.request, intent.updated_at = request, now
        # Raise OUTSIDE the transaction so a veto/audit isn't rolled back.
        if deny is not None:
            raise TradingDisabled(deny)
        if grant is None:
            raise TradingDisabled("no grant was produced")
        return grant

    def _position_news_valid(self, session, review, position):
        if review is None:
            return False
        if self.profile.data_source != SourceKind.MT5 and not review.news.managed:
            return True
        from news.evidence import verify_window

        logical = next(
            (s for s in self.settings.symbols if self.settings.symbol_aliases.get(s, s) == position.symbol),
            None,
        )
        return logical is not None and verify_window(
            session,
            review.news,
            logical_symbol=logical,
            settings=self.settings,
            profile=self.profile,
            now=self.clock.now(),
        )

    def before_send(self, command: BrokerCommand, snapshot: BrokerSnapshot, grant: WriteGrant) -> None:
        """Recheck durable controls AFTER order_check; never reserve/count twice."""
        failure = False
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.control.check(state)
            row = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if (
                row is None
                or row.state != "submitting"
                or row.request.get("authorized") is not True
                or row.request.get("request_hash") != command.request_hash
                or row.account_key != snapshot.account.key
                or row.request.get("session_id") != self.control.session_id
                or row.expires_at <= self.clock.now()
                or snapshot.data_source != self.profile.data_source
            ):
                raise TradingDisabled("intent/control changed after authorization; no send")
            if command.operation == Operation.OPEN:
                risk = self.risk.observe(session, snapshot.account, snapshot.positions)
                decision = self.risk.evaluate(
                    session,
                    command,
                    snapshot,
                    DecisionContext.from_dict(row.request["context"]),
                    risk,
                    self_reservation=Decimal(row.request["reserved_risk_usd"]),
                    self_counted=row.request.get("count_day") == risk.day.isoformat(),
                )
                try:
                    evidence = self.stages.required_evidence(session, snapshot.account)
                    if self.settings.mode == OperatingMode.LIVE and not self.stages.live_confirmed(
                        session, snapshot.account, self.control.session_id, evidence
                    ):
                        decision = replace(
                            decision, approved=False, reasons=decision.reasons + ("owner_live_confirmation",)
                        )
                except TradingDisabled:
                    decision = replace(
                        decision, approved=False, reasons=decision.reasons + ("stage_evidence",)
                    )
                self.risk.record_decision(session, snapshot.account.key, decision, command.request_hash)
                failure = not decision.approved
            else:
                matching = [
                    p
                    for p in snapshot.positions
                    if p.identifier == command.position_identifier and p.ticket == command.ticket
                ]
                trade = session.scalar(
                    select(Trade).where(
                        Trade.account_key == snapshot.account.key,
                        Trade.mode == self.settings.mode.value,
                        Trade.position_identifier == command.position_identifier,
                        Trade.status == "open",
                    )
                )
                failure = (
                    len(matching) != 1
                    or trade is None
                    or matching[0].volume != trade.volume
                    or matching[0].magic != self.settings.mt5_magic_number
                )
                failure = failure or (
                    row.request.get("expected_position_hash") is not None
                    and position_review_hash(matching[0]) != row.request["expected_position_hash"]
                )
                if command.operation == Operation.PROTECT and grant.allow_tp_extension:
                    review = PositionReview.from_dict(row.request["review"])
                    failure = (
                        failure
                        or not review.allows_extension(
                            self.settings,
                            self.clock.now(),
                            self.profile.data_source,
                            position=matching[0],
                            profile=self.profile,
                        )
                        or (not failure and not self._position_news_valid(session, review, matching[0]))
                    )
        if failure:
            raise TradingDisabled("risk/owner/stage/ownership changed before send")

    def on_result(self, command: BrokerCommand, result: ExecutionResult) -> None:
        if result.idempotency_key != command.idempotency_key or result.operation != command.operation:
            raise TradingDisabled("result operation/key changed; no acknowledgement accepted")
        with self.database.locked_session() as session:
            row = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if (
                row is None
                or row.request.get("request_hash") != command.request_hash
                or row.account_key != result.account_key
            ):
                raise TradingDisabled("broker result does not bind its durable intent")
            if row.state in TERMINAL_INTENT_STATES:
                self.database.add_audit(
                    session,
                    "broker.late_result_ignored",
                    "execution",
                    {"key": command.idempotency_key, "state": row.state},
                )
                return
            payload = dict(row.request)
            # UNKNOWN can't overwrite a later positive acknowledgement.
            previous = payload.get("result")
            if (
                previous
                and previous["status"] in {"filled", "partial", "accepted", "no_change"}
                and result.status in {ResultStatus.UNKNOWN, ResultStatus.REJECTED}
            ):
                if result.status == ResultStatus.REJECTED:
                    state = session.get(BotState, 1)
                    if not state.kill_switch_active:
                        state.desired_state = "paused"
                    state.last_error, state.revision = "ledger_mismatch", state.revision + 1
                    self.database.add_audit(
                        session,
                        "broker.contradictory_result_halt",
                        "execution",
                        {"key": command.idempotency_key},
                    )
                return
            was_reserved, was_counted = payload.get("reserved") is True, payload.get("counted") is True
            payload["result"] = execution_dict(result)
            row.ticket = result.order_ticket or None
            row.updated_at = self.clock.now()
            if result.status == ResultStatus.REJECTED:
                row.state = "rejected"
                payload.update(reserved=False, counted=False)
            elif result.status == ResultStatus.UNKNOWN:
                row.state = "unknown"
            else:
                row.state = "acknowledged"  # Actual broker deals/positions still need reconciliation.
            row.request = payload
            risk = session.scalar(
                select(RiskState).where(RiskState.account_key == row.account_key, RiskState.mode == row.mode)
            )
            if risk and result.status == ResultStatus.REJECTED:
                if was_reserved and payload.get("authorized") is True and command.operation == Operation.OPEN:
                    risk.reserved_risk_usd = max(
                        ZERO, risk.reserved_risk_usd - Decimal(payload["reserved_risk_usd"])
                    )
                    if was_counted and payload.get("count_day") == risk.day.isoformat():
                        risk.accepted_entries_today = max(0, risk.accepted_entries_today - 1)
            self.database.add_audit(
                session,
                "broker.acknowledged",
                "execution",
                {
                    "key": command.idempotency_key,
                    "status": result.status.value,
                    "order": result.order_ticket,
                    "deal": result.deal_ticket,
                },
            )

    def on_uncertain(self, command: BrokerCommand, reason: str) -> None:
        with self.database.locked_session() as session:
            row = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if row is None:
                raise TradingDisabled("uncertainty has no staged intent")
            if row.state in TERMINAL_INTENT_STATES:
                self.database.add_audit(
                    session, "broker.late_uncertainty_ignored", "execution", {"key": command.idempotency_key}
                )
                return
            if row.state == "prepared":
                # May be queued in a non-killable worker; never assume it was unsent.
                row.state = "unknown"
            elif row.state == "submitting":
                row.state = "unknown"
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing persistent halt state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.last_error, state.revision = "unknown_execution", state.revision + 1
            row.last_error = "uncertain_broker_operation"
            session.add(
                RiskEvent(
                    time=self.clock.now(),
                    event="unknown_execution",
                    details={"key": command.idempotency_key},
                    mode=row.mode,
                    account_key=row.account_key,
                )
            )
            self.database.add_audit(
                session,
                "broker.uncertain_halt",
                "execution",
                {"key": command.idempotency_key, "reservation_retained": True},
            )

    def mark_definitely_unsent(self, command: BrokerCommand) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
            )
            if row is None or row.request.get("authorized") is True or row.state != "prepared":
                return
            account_key = row.account_key
        self.on_result(
            command,
            ExecutionResult(
                command.operation,
                command.idempotency_key,
                account_key,
                ResultStatus.REJECTED,
                reason="local_veto_no_send",
            ),
        )
