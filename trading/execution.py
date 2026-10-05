"""Explicit, paused-by-default composition of durable risk and broker execution.

Not a daemon and not an API. Part 10 adds the scheduler; native entries are still
blocked without the signal/news providers and reviewed promotion artifacts.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from core.database import Database
from core.models import BrokerDeal, OrderIntent, RiskState, Trade
from core.security import sha256_json
from core.settings import OperatingMode, Settings
from trading.execution_authority import DurableWriteAuthority
from trading.order_calculator import OrderCalculator, OrderPlan
from trading.risk_types import DecisionContext, PositionReview, RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.simulation import SimulatedBroker
from trading.state_store import AtomicSnapshotStore
from trading.trade_logger import TradeLogger
from trading.trailing_engine import TrailingEngine
from trading.types import (
    ZERO,
    Broker,
    BrokerCommand,
    BrokerError,
    ExecutionResult,
    Operation,
    Side,
    SourceKind,
    TradingDisabled,
    UncertainExecution,
)


class ExecutionEngine:
    def __init__(
        self,
        broker: Broker,
        database: Database,
        settings: Settings,
        *,
        profile: RuntimeProfile | None = None,
        state_store: AtomicSnapshotStore | None = None,
    ):
        self.broker, self.database, self.settings, self.clock = broker, database, settings, broker.clock
        self.data_source = (
            broker.market.source_kind if isinstance(broker, SimulatedBroker) else broker.source_kind
        )
        if self.data_source == SourceKind.HISTORICAL and settings.mode != OperatingMode.BACKTEST:
            raise TradingDisabled("historical execution is BACKTEST-only")
        self.profile = profile or RuntimeProfile.current(settings, self.data_source)
        if self.profile.data_source != self.data_source:
            raise TradingDisabled("runtime profile cannot relabel the actual market source")
        if broker.settings.safety_fingerprint() != settings.safety_fingerprint():
            raise TradingDisabled("broker/risk configuration diverges")
        if database.settings.safety_fingerprint() != settings.safety_fingerprint():
            raise TradingDisabled("database/risk/client configuration diverges")
        self.control = RuntimeControl(database, settings, self.clock)
        self.authority = DurableWriteAuthority(database, settings, self.clock, self.control, self.profile)
        self.calculator = OrderCalculator(broker, settings)
        self.logger = TradeLogger(database, settings, self.clock, self.control, self.profile)
        self.trailing = TrailingEngine(broker, settings, self.data_source)
        self.store = state_store or AtomicSnapshotStore(settings.resolve_path(settings.paper_state_file))
        self._lock, self._initialized, self.account_key = asyncio.Lock(), False, None

    def _footprint_exists(self) -> bool:
        with self.database.session() as session:
            return any(
                session.scalar(select(model.id).where(model.mode == self.settings.mode.value).limit(1))
                for model in (Trade, OrderIntent, BrokerDeal, RiskState)
            )

    async def initialize(self) -> dict:
        async with self._lock:
            if self._initialized:
                return await self._reconcile()
            if self.database.engine.url.database == ":memory:":
                raise TradingDisabled("managed execution requires a persistent database, including paper")
            await asyncio.to_thread(self.control.claim)
            try:
                if isinstance(self.broker, SimulatedBroker):
                    checkpoint = await asyncio.to_thread(self.store.load)
                    if checkpoint is None and await asyncio.to_thread(self._footprint_exists):
                        raise TradingDisabled(
                            "paper checkpoint missing but database has prior state; NEVER reset capital"
                        )
                    self.broker.bind_execution(self.authority, self.store)
                else:
                    bind = getattr(self.broker, "bind_authority", None)
                    if bind is None:
                        raise TradingDisabled("broker lacks the mandatory durable authority binding")
                    bind(self.authority)
                await self.broker.initialize()
                await asyncio.to_thread(self.control.heartbeat)
                self.account_key = (await self.broker.get_account_info()).key
                self._initialized = True
                return await self._reconcile()
            except BaseException:
                self._initialized = False
                try:
                    await self.broker.shutdown()
                finally:
                    await asyncio.to_thread(self.control.release)
                raise

    def _ready(self):
        if not self._initialized:
            raise TradingDisabled("initialize the paused execution engine first")

    async def shutdown(self):
        async with self._lock:
            try:
                await self.broker.shutdown()
            finally:
                await asyncio.to_thread(self.control.release)
                self._initialized = False

    async def _reconcile(self) -> dict:
        self._ready()
        await asyncio.to_thread(self.control.heartbeat)
        account = await self.broker.get_account_info()
        if account.key != self.account_key:
            await asyncio.to_thread(self.control.halt, "ledger_mismatch", account_key=self.account_key)
            raise TradingDisabled("broker account changed; read-only recovery required")
        start = await asyncio.to_thread(self.logger.history_start, account.key)
        # Include two seconds before entry to accommodate SDK second precision;
        # ownership still requires exact IDs and bounded broker clock skew.
        deals = await self.broker.get_deals(start - timedelta(seconds=2), self.clock.now())
        positions = await self.broker.get_positions()
        account = await self.broker.get_account_info()
        # Paper reads themselves can cause server-style exits. Obtain final positions.
        positions = await self.broker.get_positions()
        shadow_cache = None
        if isinstance(self.broker, SimulatedBroker):
            shadow_cache = (await self.broker.export_state())["cache"]
        stop_values = {}
        owners = await asyncio.to_thread(self.logger.owned, account.key)
        for owned in owners:
            position = next(
                (
                    item
                    for item in positions
                    if item.identifier == owned.identifier and item.ticket == owned.ticket
                ),
                None,
            )
            if position and position.sl > ZERO and position.volume == owned.volume:
                try:
                    stop_values[owned.identifier] = await self.trailing.net_at_price(
                        position, owned, position.sl
                    )
                except BrokerError:
                    pass  # Missing FX must NEVER become a claimed USD lock.
        settled = frozenset()
        get_settled = getattr(self.broker, "get_settled_orders", None)
        if get_settled is not None:

            def candidates():
                with self.database.session() as session:
                    rows = session.scalars(
                        select(OrderIntent).where(
                            OrderIntent.account_key == account.key,
                            OrderIntent.mode == self.settings.mode.value,
                            OrderIntent.state.in_(("acknowledged", "unknown")),
                        )
                    ).all()
                    return tuple(
                        {
                            row.request["result"]["order_ticket"]
                            for row in rows
                            if row.request.get("result") and row.request["result"].get("order_ticket", 0) > 0
                        }
                    )

            tickets = await asyncio.to_thread(candidates)
            if tickets:
                settled = await get_settled(tickets)
        result = await asyncio.to_thread(
            self.logger.reconcile,
            account,
            positions,
            deals,
            shadow_cache=shadow_cache,
            stop_net_usd=stop_values,
            settled_orders=settled,
            history_complete=True,
        )
        return result

    async def reconcile(self) -> dict:
        async with self._lock:
            try:
                return await self._reconcile()
            except BrokerError:
                await asyncio.to_thread(
                    self.control.halt, "broker_unstable", account_key=self.account_key or "unbound"
                )
                raise

    async def _invoke(self, command: BrokerCommand) -> ExecutionResult:
        if command.operation == Operation.OPEN:
            method = (
                self.broker.open_market_buy
                if command.order.side == Side.BUY
                else self.broker.open_market_sell
            )
            return await method(command.order)
        if command.operation == Operation.CLOSE:
            return await self.broker.close_position(
                command.ticket,
                position_identifier=command.position_identifier,
                idempotency_key=command.idempotency_key,
            )
        method = self.broker.modify_sl if command.sl is not None else self.broker.modify_tp
        value = command.sl if command.sl is not None else command.tp
        return await method(
            command.ticket,
            value,
            position_identifier=command.position_identifier,
            idempotency_key=command.idempotency_key,
        )

    async def _execute(self, command: BrokerCommand, **stage_kwargs) -> ExecutionResult:
        self._ready()
        # Reconcile before stage/sizing and do not send into known uncertainty.
        await self._reconcile()
        account = await self.broker.get_account_info()
        prior = await asyncio.to_thread(self.authority.stage, command, account, **stage_kwargs)
        if prior is not None:
            return prior  # Durable duplicate across restart: NEVER call the broker again.
        try:
            result = await self._invoke(command)
        except (UncertainExecution, asyncio.CancelledError):
            await asyncio.to_thread(self.authority.on_uncertain, command, "engine_uncertain")
            raise
        except BrokerError:
            await asyncio.to_thread(self.authority.mark_definitely_unsent, command)

            # A grant exists but no durable terminal result => halt, never release it blindly.
            def still_inflight():
                with self.database.session() as session:
                    row = session.scalar(
                        select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
                    )
                    return row is not None and row.state not in {"reconciled", "rejected", "canceled"}

            if await asyncio.to_thread(still_inflight):
                await asyncio.to_thread(self.authority.on_uncertain, command, "engine_broker_error")
            raise
        except Exception:
            await asyncio.to_thread(self.authority.on_uncertain, command, "engine_persistence_error")
            raise UncertainExecution("execution/ledger failed; do not retry") from None
        try:
            await self._reconcile()
        except BaseException:
            await asyncio.to_thread(self.authority.on_uncertain, command, "post_ack_reconciliation_failed")
            raise

        # Return enriched durable result if ownership was proven, not an order ID mislabeled as a position.
        def stored():
            from trading.execution_authority import execution_from_dict

            with self.database.session() as session:
                row = session.scalar(
                    select(OrderIntent).where(OrderIntent.idempotency_key == command.idempotency_key)
                )
                return (
                    execution_from_dict(row.request["result"])
                    if row and row.request.get("result")
                    else result
                )

        return await asyncio.to_thread(stored)

    async def execute(self, plan: OrderPlan, context: DecisionContext) -> ExecutionResult:
        async with self._lock:
            command = BrokerCommand(
                Operation.OPEN, plan.order.idempotency_key, plan.order.created_at, order=plan.order
            )
            return await self._execute(command, context=context, target_usd=plan.target_profit_usd)

    async def open(
        self,
        symbol: str,
        side: Side,
        sl: Decimal,
        context: DecisionContext,
        *,
        strategy: str,
        idempotency_key: str | None = None,
    ) -> ExecutionResult | None:
        async with self._lock:
            await self._reconcile()
            plan = await self.calculator.plan_market_order(
                symbol,
                side,
                sl,
                strategy=strategy,
                risk_percent=context.risk_percent,
                idempotency_key=idempotency_key,
            )
            if plan is None:
                await asyncio.to_thread(
                    self.database.audit, "entry.no_legal_risk_size", "risk", {"symbol": symbol}
                )
                return None  # No minimum-lot rounding up, no mandatory daily trade quota.
            command = BrokerCommand(
                Operation.OPEN, plan.order.idempotency_key, plan.order.created_at, order=plan.order
            )
            return await self._execute(command, context=context, target_usd=plan.target_profit_usd)

    async def execute_signal(self, signal_id: int) -> ExecutionResult | None:
        try:
            return await self._execute_signal(signal_id)
        except BrokerError:
            await asyncio.to_thread(
                self.database.audit,
                "signal.entry_bridge_veto",
                "execution",
                {
                    "signal_id": signal_id if type(signal_id) is int else None,
                    "reason": "quality_risk_or_execution_uncertainty",
                },
            )
            raise

    async def _execute_signal(self, signal_id: int) -> ExecutionResult | None:
        """Explicit persisted-signal bridge. Cached outcomes use the ORIGINAL order.

        It is not a scheduler or automatic resume. Signal quality approval is
        separate from owner/risk/stage permission, and no unknown intent is retried.
        """
        from strategy.signal_execution import original_signal_command
        from strategy.signal_store import SignalStore
        from trading.risk_types import DecisionContext, json_dict

        async with self._lock:
            self._ready()
            await self._reconcile()
            store = SignalStore(self.database, self.settings, self.clock, self.profile)
            signal = await asyncio.to_thread(store.get, signal_id)
            key = sha256_json(
                {
                    "purpose": "persisted-signal-entry-v1",
                    "account": self.account_key,
                    "mode": self.settings.mode.value,
                    "signal_id": signal_id,
                    "config_hash": self.settings.safety_fingerprint(),
                    "code_hash": self.profile.code_hash,
                    "model_sha256": self.profile.model_sha256,
                }
            )

            def prior_payload():
                with self.database.session() as session:
                    row = session.scalar(select(OrderIntent).where(OrderIntent.idempotency_key == key))
                    if row is None:
                        return None
                    if (
                        row.account_key != self.account_key
                        or row.mode != self.settings.mode.value
                        or row.config_hash != self.settings.safety_fingerprint()
                    ):
                        raise TradingDisabled("signal intent account/configuration changed")
                    return json_dict(row.request)

            previous = await asyncio.to_thread(prior_payload)
            if previous is not None:
                command = original_signal_command(previous, signal_id, key)
                return await self._execute(
                    command,
                    context=DecisionContext.from_dict(previous["context"]),
                    target_usd=Decimal(previous["target_usd"]),
                )
            if not signal.approved:
                await asyncio.to_thread(
                    self.database.audit,
                    "signal.execution_vetoed",
                    "execution",
                    {"signal_id": signal_id, "reason": "not_reviewed_approved"},
                )
                raise TradingDisabled("persisted signal is not technically/news/AI approved")
            context = signal.context
            if (
                not -2
                <= (self.clock.now() - context.observed_at).total_seconds()
                <= self.settings.order_max_age_seconds
            ):
                raise TradingDisabled("reviewed signal is stale; wait for a new closed-bar opportunity")
            payload = signal.payload()
            tick = await self.broker.get_tick(signal.symbol)
            tick.fresh(self.clock, self.settings.max_tick_age_seconds)
            atr, close = Decimal(payload["atr"]), Decimal(payload["bar_close_price"])
            if (
                atr <= ZERO
                or abs(tick.entry(signal.side) - close) > atr * self.settings.strategy_max_entry_drift_atr
            ):
                raise TradingDisabled("price moved beyond the approved closed-bar ATR drift; do not chase")
            plan = await self.calculator.plan_market_order(
                signal.symbol,
                signal.side,
                signal.stop_price,
                strategy=payload["strategy"],
                idempotency_key=key,
                risk_percent=context.risk_percent,
                created_at=context.observed_at,
            )
            if plan is None:
                await asyncio.to_thread(
                    self.database.audit, "signal.no_legal_risk_size", "execution", {"signal_id": signal_id}
                )
                return None
            command = BrokerCommand(Operation.OPEN, key, plan.order.created_at, order=plan.order)
            return await self._execute(command, context=context, target_usd=plan.target_profit_usd)

    def new_key(self, operation: str, identifier: int) -> str:
        return sha256_json(
            {
                "session_id": self.control.session_id,
                "operation": operation,
                "identifier": identifier,
                "nonce": str(uuid4()),
            }
        )

    async def capture_owned_positions(self):
        """Guarded owner-action/runtime-review preparation; GET dashboards never call this.

        Reads may trigger shadow exits in paper, so reconcile under the execution
        lock and capture ONLY exact ledger/magic/ticket/identifier/volume matches.
        The confirmation hash is rechecked by authority and immediately pre-send.
        """
        async with self._lock:
            await self._reconcile()
            positions = await self.broker.get_positions()
            owners = await asyncio.to_thread(self.logger.owned, self.account_key)
            captured = []
            for owned in owners:
                matching = [
                    p for p in positions if p.ticket == owned.ticket and p.identifier == owned.identifier
                ]
                if len(matching) != 1:
                    raise TradingDisabled("owned capture changed; reconcile before a new confirmation")
                p = matching[0]
                if (
                    p.magic != self.settings.mt5_magic_number
                    or p.volume != owned.volume
                    or p.symbol != owned.symbol
                    or p.side.value != owned.direction
                ):
                    raise TradingDisabled("owned capture mismatched ledger; no confirmation issued")
                captured.append(p)
            return tuple(sorted(captured, key=lambda p: (p.identifier, p.ticket)))

    async def close_owned(
        self,
        ticket: int,
        identifier: int,
        *,
        idempotency_key: str | None = None,
        expected_position_hash: str | None = None,
    ) -> ExecutionResult:
        async with self._lock:
            command = BrokerCommand(
                Operation.CLOSE,
                idempotency_key or self.new_key("close", identifier),
                self.clock.now(),
                ticket=ticket,
                position_identifier=identifier,
            )
            return await self._execute(command, expected_position_hash=expected_position_hash)

    async def protect_sl(
        self,
        ticket: int,
        identifier: int,
        sl: Decimal,
        *,
        lock_level: float = 0,
        idempotency_key: str | None = None,
    ) -> ExecutionResult:
        async with self._lock:
            command = BrokerCommand(
                Operation.PROTECT,
                idempotency_key or self.new_key("sl", identifier),
                self.clock.now(),
                ticket=ticket,
                position_identifier=identifier,
                sl=sl,
            )
            return await self._execute(command, lock_level=lock_level)

    async def extend_tp(
        self,
        ticket: int,
        identifier: int,
        tp: Decimal,
        review: PositionReview,
        *,
        idempotency_key: str | None = None,
    ) -> ExecutionResult:
        async with self._lock:
            command = BrokerCommand(
                Operation.PROTECT,
                idempotency_key or self.new_key("tp", identifier),
                self.clock.now(),
                ticket=ticket,
                position_identifier=identifier,
                tp=tp,
            )
            return await self._execute(command, review=review)
