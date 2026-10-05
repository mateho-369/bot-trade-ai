"""In-memory paper execution with bid/ask, adverse slippage, fees and swap.

Never calls a market source's write methods. Snapshot persistence is explicit;
Part 5 wires atomic storage. This adapter alone is NOT an unattended trading bot.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from core.security import canonical_json
from core.settings import OperatingMode, Settings
from trading.ai_controls import broker_ceilings
from trading.authorization import validate_write_grant
from trading.client_helpers import ClientCalculations
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price, protection_prices, validate_entry
from trading.snapshots import broker_snapshot
from trading.types import (
    ZERO,
    AccountInfo,
    AccountKind,
    BrokerCommand,
    BrokerError,
    Deal,
    ExecutionResult,
    InvalidOrder,
    MarketData,
    MarketOrder,
    Operation,
    Position,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    StaleData,
    TradingDisabled,
    UncertainExecution,
    aware_utc,
    valid_key,
)

_LOG = logging.getLogger("reflexbot.simulation")


class SimulatedBroker(ClientCalculations):
    def __init__(
        self,
        market: MarketData,
        settings: Settings,
        *,
        source_kind: SourceKind,
        ledger_id: str = "paper-main",
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        if settings.mode not in {OperatingMode.PAPER, OperatingMode.BACKTEST}:
            raise TradingDisabled("simulation adapters cannot run as demo/live broker execution")
        self.market, self.settings, self.clock = market, settings, market.clock
        self.source_kind, self.ledger_id = source_kind, ledger_id
        self._lock = asyncio.Lock()
        self._currency = CurrencyConverter(market, settings)
        self._initialized = False
        self._balance = settings.paper_initial_balance
        self._positions: dict[int, Position] = {}
        self._margins: dict[int, Decimal] = {}
        self._original_tps: dict[int, Decimal] = {}
        self._deals: list[Deal] = []
        self._cache: dict[str, tuple[str, ExecutionResult]] = {}
        self._next_position, self._next_order, self._next_deal = 100000, 200000, 300000
        self._day = self.clock.now().astimezone(ZoneInfo(settings.trading_day_timezone)).date()
        self._day_start, self._peak = self._balance, self._balance
        self._entries_today = 0
        self._daily_latched, self._drawdown_latched, self._quotes_stale = False, False, False
        self._restored_state = restored_state
        self._authority = None
        self._state_store = None
        self._checkpoint_digest = None
        self._quarantined = self._persistence_failed = False

    def bind_execution(self, authority, state_store) -> None:
        if self._initialized or self._authority is not None or self._restored_state is not None:
            raise TradingDisabled(
                "bind managed simulation before initialize, without a competing restored_state"
            )
        self._authority, self._state_store = authority, state_store

    def health(self):
        return {
            "source": self.source_kind.value,
            "connected": self._initialized,
            "writes_quarantined": self._quarantined,
            "durable_simulation": self._state_store is not None,
        }

    async def _checkpoint(self):
        if self._state_store is None:
            return
        payload = self._state_payload()
        digest = canonical_json(payload)
        if digest == self._checkpoint_digest:
            return
        try:
            await asyncio.to_thread(self._state_store.save, payload)
            self._checkpoint_digest = digest
        except BaseException:
            self._quarantined = self._persistence_failed = True
            raise UncertainExecution(
                "shadow state checkpoint failed/canceled; never reset or resend"
            ) from None

    async def _permit(self, command, meta, tick, loss=ZERO, margin=ZERO, reward=ZERO):
        if self._quarantined:
            raise UncertainExecution("managed simulation is quarantined")
        if self._authority is None:
            return None
        snapshot = await broker_snapshot(
            self.market,
            self.settings,
            self._account(),
            meta,
            tick,
            tuple(self._positions.values()),
            loss,
            margin,
            reward,
            entry=command.operation == Operation.OPEN,
            durable_simulation=self._state_store is not None,
        )
        try:
            grant = await asyncio.to_thread(self._authority.authorize, command, snapshot)
            validate_write_grant(command, snapshot, grant, self.settings, self.clock)
            await asyncio.to_thread(self._authority.before_send, command, snapshot, grant)
            # A shadow fill must still use the exact fresh bid/ask valued above.
            current = await self.market.get_tick(meta.name)
            current.fresh(self.clock, self.settings.max_tick_age_seconds)
            if (current.bid, current.ask) != (tick.bid, tick.ask):
                raise RiskViolation("quote changed during shadow authorization; replan, no fill")
            return grant
        except asyncio.CancelledError:
            self._quarantined = True
            await asyncio.to_thread(self._authority.on_uncertain, command, "shadow_canceled")
            raise
        except BrokerError:
            # Permission/prefill error, BEFORE mutating shadow positions.
            result = ExecutionResult(
                command.operation,
                command.idempotency_key,
                snapshot.account.key,
                ResultStatus.REJECTED,
                reason="shadow_prefill_veto_no_fill",
            )
            await asyncio.to_thread(self._authority.on_result, command, result)
            raise

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            await self.market.initialize()
            data_account = await self.market.get_account_info()
            if data_account.currency != self.settings.account_currency:
                raise RiskViolation("paper and data valuation currencies must match explicitly")
            self._data_account_key = data_account.key
            if self._state_store is not None:
                self._restored_state = await asyncio.to_thread(self._state_store.load)
            if self._restored_state is not None:
                self._restore(self._restored_state)
            self._initialized = True
            await self._checkpoint()

    def _ready(self) -> None:
        if not self._initialized:
            raise BrokerError("initialize the simulated broker first")

    async def shutdown(self) -> None:
        # Does not liquidate positions. Persist a snapshot before process shutdown.
        await self.market.shutdown()
        self._initialized = False

    async def __aenter__(self):
        try:
            await self.initialize()
            return self
        except BaseException:
            await self.shutdown()
            raise

    async def __aexit__(self, *args):
        await self.shutdown()

    def _account(self) -> AccountInfo:
        margin = sum(self._margins.values(), ZERO)
        equity = self._balance + sum(
            (position.profit + position.swap for position in self._positions.values()), ZERO
        )
        return AccountInfo(
            1,
            self.ledger_id + ":" + self._data_account_key,
            self.settings.account_currency,
            AccountKind.SIMULATED,
            self.source_kind,
            self._balance,
            equity,
            margin,
            equity - margin,
            leverage=self.settings.mock_leverage,
            trade_allowed=True,
            trade_expert=True,
            quotes_stale=self._quotes_stale,
        )

    async def _fee_account(self, volume: Decimal, *, half: bool = False) -> Decimal:
        dollars = (
            volume
            * self.settings.commission_round_turn_usd_per_lot
            / (Decimal("2") if half else Decimal("1"))
        )
        return -(await self._currency.convert(-dollars, "USD", self.settings.account_currency))

    async def _refresh(self) -> None:
        self._ready()
        if self._persistence_failed:
            raise UncertainExecution("shadow accounting cannot advance after checkpoint failure")
        day = self.clock.now().astimezone(ZoneInfo(self.settings.trading_day_timezone)).date()
        if day != self._day:
            # Carry the last observed equity into the new day BEFORE marking
            # gaps/swap/exits, so the first new-day loss is not silently erased.
            self._day, self._day_start = day, self._account().equity
            self._entries_today, self._daily_latched = 0, False
        self._quotes_stale = False
        for identifier in tuple(self._positions):
            position = self._positions.get(identifier)
            if position is None:
                continue
            tick = await self.market.get_tick(position.symbol)
            try:
                tick.fresh(self.clock, self.settings.max_tick_age_seconds)
            except StaleData:
                self._quotes_stale = True
                continue  # Never generate a new exit from an old quote.
            price = tick.exit(position.side)
            gross = await self.market.calculate_profit(
                position.symbol, position.side, position.volume, position.entry_price, price
            )
            days = Decimal(str((self.clock.now() - position.time).total_seconds())) / Decimal("86400")
            swap_usd = position.volume * self.settings.estimated_swap_usd_per_lot_per_day * max(ZERO, days)
            swap = await self._currency.convert(-swap_usd, "USD", self.settings.account_currency)
            position = replace(position, profit=gross, swap=swap)
            self._positions[identifier] = position
            self._margins[identifier] = await self.market.calculate_margin(
                position.symbol, position.side, position.volume, tick.entry(position.side)
            )
            if position.sl > ZERO and position.side.sign * (price - position.sl) <= ZERO:
                await self._close_at(position, price, "sl")  # Gap fills at worse market price, NOT SL.
            elif position.tp > ZERO and position.side.sign * (price - position.tp) >= ZERO:
                await self._close_at(
                    position, position.tp, "tp"
                )  # Conservative target fill, no favorable gap gift.
        account = self._account()
        self._peak = max(self._peak, account.equity)
        if (
            self._day_start > ZERO
            and (self._day_start - account.equity) / self._day_start * 100
            >= self.settings.max_daily_loss_percent
        ):
            self._daily_latched = True
        if (
            self._peak > ZERO
            and (self._peak - account.equity) / self._peak * 100 >= self.settings.max_drawdown_percent
        ):
            self._drawdown_latched = True
        await self._checkpoint()

    async def get_account_info(self) -> AccountInfo:
        async with self._lock:
            await self._refresh()
            return self._account()

    async def get_positions(self) -> tuple[Position, ...]:
        async with self._lock:
            await self._refresh()
            return tuple(self._positions.values())

    async def get_symbols(self) -> tuple[str, ...]:
        self._ready()
        return await self.market.get_symbols()

    async def select_symbol(self, symbol: str):
        self._ready()
        return await self.market.select_symbol(symbol)

    async def get_symbol_info(self, symbol: str):
        self._ready()
        return await self.market.get_symbol_info(symbol)

    async def get_tick(self, symbol: str):
        self._ready()
        return await self.market.get_tick(symbol)

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 300, *, as_of: datetime | None = None
    ):
        self._ready()
        return await self.market.get_candles(symbol, timeframe, count, as_of=as_of)

    async def calculate_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal:
        self._ready()
        return await self.market.calculate_profit(symbol, side, volume, entry, exit_price)

    async def calculate_margin(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal:
        self._ready()
        return await self.market.calculate_margin(symbol, side, volume, entry)

    async def get_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]:
        start, end = aware_utc(since), aware_utc(until or self.clock.now())
        if start > end or end > self.clock.now():
            raise BrokerError("invalid simulation deal interval")
        async with self._lock:
            await self._refresh()
            return tuple(deal for deal in self._deals if start <= deal.time <= end)

    async def get_closed_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]:
        return tuple(deal for deal in await self.get_deals(since, until) if deal.entry == "out")

    def _cached(self, command: BrokerCommand) -> ExecutionResult | None:
        item = self._cache.get(command.idempotency_key)
        if item is not None:
            if item[0] != command.request_hash:
                raise RiskViolation("simulation idempotency key changed payload")
            _LOG.info(
                "Simulation cached decision status=%s key=%s",
                item[1].status.value,
                command.idempotency_key[:12],
            )
            return item[1]
        if len(self._cache) >= 100000:
            raise TradingDisabled("simulation intent cache exceeds the bound")
        return None

    async def _remember(self, command: BrokerCommand, result: ExecutionResult) -> ExecutionResult:
        self._cache[command.idempotency_key] = (command.request_hash, result)
        try:
            # Shadow ledger/cache durable BEFORE broker acknowledgement callback.
            await self._checkpoint()
            if self._authority is not None:
                await asyncio.to_thread(self._authority.on_result, command, result)
        except BaseException:
            self._quarantined = True
            if self._authority is not None:
                await asyncio.to_thread(self._authority.on_uncertain, command, "shadow_ack_failed")
            raise
        _LOG.info(
            "Simulation decision op=%s status=%s source=%s key=%s",
            command.operation.value,
            result.status.value,
            self.source_kind.value,
            command.idempotency_key[:12],
        )
        return result

    async def _open(self, order: MarketOrder) -> ExecutionResult:
        command = BrokerCommand(Operation.OPEN, order.idempotency_key, order.created_at, order=order)
        _LOG.info(
            "Simulation entry attempt key=%s source=%s", command.idempotency_key[:12], self.source_kind.value
        )
        async with self._lock:
            self._ready()
            prior = self._cached(command)
            if prior:
                return prior
            await self._refresh()
            account = self._account()
            if (
                self._quotes_stale
                or self._daily_latched
                or self._drawdown_latched
                or self._entries_today >= broker_ceilings(self.settings).max_daily_trades
            ):
                raise RiskViolation("simulation daily/drawdown/count/stale-exposure gate blocks entry")
            if len(self._positions) >= broker_ceilings(self.settings).max_open_positions or any(
                position.symbol == order.symbol for position in self._positions.values()
            ):
                raise RiskViolation("simulation position cap/no-averaging rule")
            meta, tick = (
                await self.market.get_symbol_info(order.symbol),
                await self.market.get_tick(order.symbol),
            )
            validate_entry(order, meta, tick, self.settings, self.clock)
            worst_entry = adverse_price(
                tick.entry(order.side), order.side, meta, self.settings.max_slippage_points, entry=True
            )
            worst_exit = adverse_price(
                order.sl, order.side, meta, self.settings.max_slippage_points, entry=False
            )
            fees = await self._fee_account(order.volume)
            loss = (
                -(
                    await self.market.calculate_profit(
                        order.symbol, order.side, order.volume, worst_entry, worst_exit
                    )
                )
                + fees
            )
            reward = (
                await self.market.calculate_profit(
                    order.symbol,
                    order.side,
                    order.volume,
                    worst_entry,
                    adverse_price(order.tp, order.side, meta, self.settings.max_slippage_points, entry=False),
                )
            ) - fees
            margin = await self.market.calculate_margin(order.symbol, order.side, order.volume, worst_entry)
            budget = account.risk_capital * broker_ceilings(self.settings).risk_percent / Decimal("100")
            available = min(
                account.margin_free,
                account.risk_capital * self.settings.max_margin_usage_percent / Decimal("100")
                - account.margin,
            )
            if (
                loss <= ZERO
                or loss > budget
                or margin > available
                or reward / loss < self.settings.min_net_reward_risk
            ):
                raise RiskViolation("simulation nominal risk/margin/net reward fails")
            await self._permit(command, meta, tick, loss, margin, reward)
            fill = adverse_price(
                tick.entry(order.side), order.side, meta, self.settings.paper_slippage_points, entry=True
            )
            self._next_position += 1
            self._next_order += 1
            self._next_deal += 1
            identifier = self._next_position + 400000
            open_fee = await self._fee_account(order.volume, half=True)
            self._balance -= open_fee
            position = Position(
                self._next_position,
                identifier,
                order.symbol,
                order.side,
                order.volume,
                fill,
                order.sl,
                order.tp,
                self.clock.now(),
                self.settings.mt5_magic_number,
                entry_commission=-open_fee,
                comment=order.strategy,
            )
            self._positions[identifier], self._margins[identifier], self._original_tps[identifier] = (
                position,
                margin,
                order.tp,
            )
            self._deals.append(
                Deal(
                    self._next_deal,
                    self._next_order,
                    identifier,
                    order.symbol,
                    order.side.value,
                    "in",
                    self.clock.now(),
                    order.volume,
                    fill,
                    ZERO,
                    -open_fee,
                    ZERO,
                    ZERO,
                    self.settings.mt5_magic_number,
                    account.currency,
                    "entry",
                    order.strategy,
                )
            )
            self._entries_today += 1
            result = ExecutionResult(
                Operation.OPEN,
                order.idempotency_key,
                account.key,
                ResultStatus.FILLED,
                self._next_order,
                self._next_deal,
                identifier,
                order.volume,
                fill,
                reason="SIMULATED_ONLY",
            )
            await self._remember(command, result)
            await self._refresh()
            return result

    async def open_market_buy(self, order: MarketOrder) -> ExecutionResult:
        if order.side != Side.BUY:
            raise InvalidOrder("BUY received SELL")
        return await self._open(order)

    async def open_market_sell(self, order: MarketOrder) -> ExecutionResult:
        if order.side != Side.SELL:
            raise InvalidOrder("SELL received BUY")
        return await self._open(order)

    async def _close_at(self, position: Position, reference: Decimal, reason: str) -> ExecutionResult:
        meta = await self.market.get_symbol_info(position.symbol)
        exit_price = adverse_price(
            reference, position.side, meta, self.settings.paper_slippage_points, entry=False
        )
        if exit_price <= ZERO:
            raise RiskViolation("simulation exit is nonpositive")
        gross = await self.market.calculate_profit(
            position.symbol, position.side, position.volume, position.entry_price, exit_price
        )
        fee = await self._fee_account(position.volume, half=True)
        self._balance += gross + position.swap - fee
        self._next_order += 1
        self._next_deal += 1
        self._deals.append(
            Deal(
                self._next_deal,
                self._next_order,
                position.identifier,
                position.symbol,
                Side.SELL.value if position.side == Side.BUY else Side.BUY.value,
                "out",
                self.clock.now(),
                position.volume,
                exit_price,
                gross,
                -fee,
                position.swap,
                ZERO,
                position.magic,
                self.settings.account_currency,
                reason,
                position.comment,
            )
        )
        del self._positions[position.identifier]
        self._margins.pop(position.identifier)
        self._original_tps.pop(position.identifier)
        await self._checkpoint()
        _LOG.info("Simulation closed id=%s reason=%s; not a broker fill", position.identifier, reason)
        return ExecutionResult(
            Operation.CLOSE,
            "0" * 64,
            self._account().key,
            ResultStatus.FILLED,
            self._next_order,
            self._next_deal,
            position.identifier,
            position.volume,
            exit_price,
            reason="SIMULATED_" + reason,
        )

    def _owned(self, command: BrokerCommand) -> Position:
        position = self._positions.get(command.position_identifier)
        if (
            position is None
            or position.ticket != command.ticket
            or position.magic != self.settings.mt5_magic_number
        ):
            raise TradingDisabled("simulated position identifier/ownership does not match")
        return position

    async def close_position(
        self, ticket: int, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult:
        command = BrokerCommand(
            Operation.CLOSE,
            idempotency_key,
            self.clock.now(),
            ticket=ticket,
            position_identifier=position_identifier,
        )
        _LOG.info("Simulation close attempt key=%s", command.idempotency_key[:12])
        async with self._lock:
            self._ready()
            prior = self._cached(command)
            if prior:
                return prior
            await self._refresh()
            position = self._owned(command)
            tick = await self.market.get_tick(position.symbol)
            tick.fresh(self.clock, self.settings.max_tick_age_seconds)
            meta = await self.market.get_symbol_info(position.symbol)
            await self._permit(command, meta, tick)
            result = await self._close_at(position, tick.exit(position.side), "manual")
            return await self._remember(command, replace(result, idempotency_key=idempotency_key))

    async def _protect(self, command: BrokerCommand) -> ExecutionResult:
        _LOG.info("Simulation protection attempt key=%s", command.idempotency_key[:12])
        async with self._lock:
            self._ready()
            prior = self._cached(command)
            if prior:
                return prior
            await self._refresh()
            position = self._owned(command)
            meta, tick = (
                await self.market.get_symbol_info(position.symbol),
                await self.market.get_tick(position.symbol),
            )
            tick.fresh(self.clock, self.settings.max_tick_age_seconds)
            sl, tp = protection_prices(command, position, meta, tick, self.settings)
            grant = await self._permit(command, meta, tick)
            if position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO:
                if grant is None or not grant.allow_tp_extension:
                    raise TradingDisabled("simulation TP extension requires approved review authority")
                if (
                    abs(tp - position.entry_price)
                    > abs(grant.original_tp - position.entry_price) * self.settings.tp_extension_factor
                ):
                    raise RiskViolation("shadow TP extension exceeds original-target cap")
            status = ResultStatus.NO_CHANGE if (sl, tp) == (position.sl, position.tp) else ResultStatus.FILLED
            self._positions[position.identifier] = replace(position, sl=sl, tp=tp)
            return await self._remember(
                command,
                ExecutionResult(
                    Operation.PROTECT,
                    command.idempotency_key,
                    self._account().key,
                    status,
                    position_identifier=position.identifier,
                    reason="SIMULATED_PROTECTION",
                ),
            )

    async def modify_sl(
        self, ticket: int, sl: Decimal, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult:
        return await self._protect(
            BrokerCommand(
                Operation.PROTECT,
                idempotency_key,
                self.clock.now(),
                ticket=ticket,
                position_identifier=position_identifier,
                sl=sl,
            )
        )

    async def modify_tp(
        self, ticket: int, tp: Decimal, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult:
        return await self._protect(
            BrokerCommand(
                Operation.PROTECT,
                idempotency_key,
                self.clock.now(),
                ticket=ticket,
                position_identifier=position_identifier,
                tp=tp,
            )
        )

    def _state_payload(self) -> dict[str, Any]:
        import json

        payload = {
            "version": 1,
            "source_kind": self.source_kind.value,
            "market_source_kind": self.market.source_kind.value,
            "data_account_key": self._data_account_key,
            "config_hash": self.settings.safety_fingerprint(),
            "ledger_id": self.ledger_id,
            "balance": self._balance,
            "positions": [asdict(position) for position in self._positions.values()],
            "margins": {str(key): value for key, value in self._margins.items()},
            "original_tps": {str(key): value for key, value in self._original_tps.items()},
            "deals": [asdict(deal) for deal in self._deals],
            "cache": {key: [digest, asdict(result)] for key, (digest, result) in self._cache.items()},
            "counters": [self._next_position, self._next_order, self._next_deal],
            "day": self._day.isoformat(),
            "day_start": self._day_start,
            "peak": self._peak,
            "entries_today": self._entries_today,
            "daily_latched": self._daily_latched,
            "drawdown_latched": self._drawdown_latched,
        }
        return json.loads(canonical_json(payload))

    async def export_state(self) -> dict[str, Any]:
        async with self._lock:
            self._ready()
            return self._state_payload()

    def _restore(self, state: dict[str, Any]) -> None:
        from datetime import date

        if (
            state.get("source_kind") != self.source_kind.value
            or state.get("market_source_kind") != self.market.source_kind.value
            or state.get("data_account_key") != self._data_account_key
            or state.get("version") != 1
            or state.get("config_hash") != self.settings.safety_fingerprint()
            or state.get("ledger_id") != self.ledger_id
        ):
            raise RiskViolation("paper snapshot version/config/ledger identity does not match")
        if any(len(state.get(name, [])) > 100000 for name in ("positions", "deals", "cache")):
            raise RiskViolation("paper snapshot exceeds safety limits")

        def decode(row: dict, cls, money: tuple[str, ...]):
            values = dict(row)
            for name in money:
                if values.get(name) is not None:
                    values[name] = Decimal(values[name])
            if "time" in values:
                values["time"] = aware_utc(datetime.fromisoformat(values["time"]))
            if "side" in values:
                values["side"] = Side(values["side"])
            if "operation" in values:
                values["operation"] = Operation(values["operation"])
                values["status"] = ResultStatus(values["status"])
            return cls(**values)

        self._balance = Decimal(state["balance"])
        self._positions = {
            row["identifier"]: decode(
                row, Position, ("volume", "entry_price", "sl", "tp", "profit", "swap", "entry_commission")
            )
            for row in state["positions"]
        }
        self._deals = [
            decode(row, Deal, ("volume", "price", "profit", "commission", "swap", "fee"))
            for row in state["deals"]
        ]
        self._margins = {int(key): Decimal(value) for key, value in state["margins"].items()}
        self._original_tps = {int(key): Decimal(value) for key, value in state["original_tps"].items()}
        self._cache = {
            key: (value[0], decode(value[1], ExecutionResult, ("filled_volume", "filled_price")))
            for key, value in state["cache"].items()
        }
        self._next_position, self._next_order, self._next_deal = map(int, state["counters"])
        self._day, self._day_start, self._peak = (
            date.fromisoformat(state["day"]),
            Decimal(state["day_start"]),
            Decimal(state["peak"]),
        )
        self._entries_today = int(state["entries_today"])
        self._daily_latched, self._drawdown_latched = (
            bool(state["daily_latched"]),
            bool(state["drawdown_latched"]),
        )
        if set(self._positions) != set(self._margins) or set(self._positions) != set(self._original_tps):
            raise RiskViolation("paper snapshot exposure maps are incomplete")
        if (
            self._positions
            and max(position.ticket for position in self._positions.values()) > self._next_position
        ):
            raise RiskViolation("paper snapshot position counter regressed")
        if not all(
            value.is_finite()
            for value in (self._balance, self._day_start, self._peak, *self._margins.values())
        ):
            raise RiskViolation("paper snapshot contains non-finite accounting values")

        if any(
            position.time > self.clock.now() or position.magic != self.settings.mt5_magic_number
            for position in self._positions.values()
        ):
            raise RiskViolation("paper snapshot position time/ownership is invalid")
        if self._day > self.clock.now().astimezone(ZoneInfo(self.settings.trading_day_timezone)).date():
            raise RiskViolation("paper snapshot day is in the future")
        if len(self._positions) != len(state["positions"]) or len(
            {deal.ticket for deal in self._deals}
        ) != len(self._deals):
            raise RiskViolation("paper snapshot has duplicate position/deal identifiers")
        if any(
            deal.time > self.clock.now() or deal.currency != self.settings.account_currency
            for deal in self._deals
        ):
            raise RiskViolation("paper snapshot contains future/mixed-currency deals")
        if any(value < ZERO for value in self._margins.values()) or any(
            not value.is_finite() or value <= ZERO for value in self._original_tps.values()
        ):
            raise RiskViolation("paper snapshot margin/original-target maps are invalid")
        if self._entries_today < 0 or self._day_start <= ZERO or self._peak <= ZERO:
            raise RiskViolation("paper snapshot baselines/counters are invalid")
        if self._deals and (
            max(deal.ticket for deal in self._deals) > self._next_deal
            or max(deal.order_ticket for deal in self._deals) > self._next_order
        ):
            raise RiskViolation("paper snapshot order/deal counters regressed")
        for key, (digest, result) in self._cache.items():
            valid_key(key)
            valid_key(digest)
            if result.idempotency_key != key or result.account_key != self._account().key:
                raise RiskViolation("paper snapshot intent identity is invalid")
