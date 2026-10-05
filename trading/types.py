"""Typed broker contract: financial numbers are Decimal, times are aware UTC."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Protocol, runtime_checkable

import pandas as pd

from core.security import sha256_json

ZERO = Decimal("0")


class BrokerError(RuntimeError):
    """A safe message; never include credentials or raw SDK requests."""


class ConnectionUnavailable(BrokerError):
    pass


class IdentityChanged(BrokerError):
    pass


class TradingDisabled(BrokerError):
    pass


class InvalidOrder(BrokerError):
    pass


class StaleData(BrokerError):
    pass


class UnsupportedSymbol(BrokerError):
    pass


class RiskViolation(BrokerError):
    pass


class UncertainExecution(BrokerError):
    """The request MAY have executed. Reconcile; never blindly retry."""


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self == Side.BUY else Decimal("-1")


class SourceKind(StrEnum):
    MT5 = "mt5"
    SYNTHETIC = "synthetic"
    TEST_SDK = "test_sdk"
    PAPER = "paper"
    HISTORICAL = "historical"  # Local offline replay; never native broker provenance.


class AccountKind(StrEnum):
    DEMO = "demo"
    REAL = "real"
    CONTEST = "contest"
    SIMULATED = "simulated"


class Operation(StrEnum):
    OPEN = "open"
    CLOSE = "close"
    PROTECT = "protect"


class ResultStatus(StrEnum):
    FILLED = "filled"
    PARTIAL = "partial"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    NO_CHANGE = "no_change"


def decimal_value(value: object) -> Decimal:
    """Explicit SDK float -> Decimal boundary; nonfinite/bool values are rejected."""
    if isinstance(value, bool):
        raise BrokerError("boolean is not a financial number")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise BrokerError("invalid financial number") from exc
    if not number.is_finite():
        raise BrokerError("non-finite financial number")
    return number


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BrokerError("timezone-aware timestamps are required")
    return value.astimezone(timezone.utc)


def financial_fields(instance: object, names: tuple[str, ...], *, positive: bool = False) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= ZERO):
            raise BrokerError(f"invalid Decimal field: {name}")


def valid_key(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise InvalidOrder("idempotency key must be a lowercase SHA256 hex digest")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock:
    """Monotonic controllable simulation clock; never use for real execution."""

    def __init__(self, instant: datetime) -> None:
        self._instant = aware_utc(instant)
        self._lock = RLock()

    def now(self) -> datetime:
        with self._lock:
            return self._instant

    def advance(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("cannot move a simulation clock backwards")
        with self._lock:
            self._instant += delta


@dataclass(frozen=True, slots=True)
class AccountInfo:
    login: int = field(repr=False)
    server: str = field(repr=False)
    currency: str
    kind: AccountKind
    source: SourceKind
    balance: Decimal
    equity: Decimal
    margin: Decimal
    margin_free: Decimal
    credit: Decimal = ZERO
    leverage: int = 0
    trade_allowed: bool = False
    trade_expert: bool = False
    quotes_stale: bool = False

    def __post_init__(self) -> None:
        financial_fields(self, ("balance", "equity", "margin", "margin_free", "credit"))
        if self.margin < ZERO or self.credit < ZERO:
            raise BrokerError("negative margin/credit is invalid")

    @property
    def key(self) -> str:
        # Account identifiers are private; logs/APIs need only this scoped hash.
        return self.source.value + ":" + sha256_json({"login": self.login, "server": self.server})[:32]

    @property
    def risk_capital(self) -> Decimal:
        return max(ZERO, min(self.balance, self.equity - self.credit))


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    name: str
    point: Decimal
    tick_size: Decimal
    tick_value_profit: Decimal
    tick_value_loss: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    digits: int
    currency_base: str
    currency_profit: str
    trade_mode: int = 4  # MQL5: 0 disabled, 1 long-only, 2 short-only, 3 close-only, 4 full
    stops_level: int = 0
    freeze_level: int = 0
    filling_mode: int = 3  # capability bits: 1 FOK, 2 IOC (NOT order filling enums)
    execution_mode: int = 2  # MQL5 market execution
    order_mode: int = 127
    volume_limit: Decimal = ZERO
    visible: bool = True
    calc_mode: int = 0

    def __post_init__(self) -> None:
        financial_fields(
            self,
            ("point", "tick_size", "contract_size", "volume_min", "volume_max", "volume_step"),
            positive=True,
        )
        financial_fields(self, ("tick_value_profit", "tick_value_loss", "volume_limit"))
        if (
            self.volume_min > self.volume_max
            or self.volume_limit < ZERO
            or min(self.stops_level, self.freeze_level) < 0
        ):
            raise UnsupportedSymbol("invalid broker symbol limits")
        if not 0 <= self.digits <= 12 or self.tick_value_profit < 0 or self.tick_value_loss < 0:
            raise UnsupportedSymbol("invalid precision/tick value")
        if self.tick_size % self.point != ZERO or self.point != Decimal(1).scaleb(-self.digits):
            raise UnsupportedSymbol("tick/point/digits metadata is inconsistent")
        if self.volume_min % self.volume_step != ZERO:
            raise UnsupportedSymbol("minimum lot is not aligned to the lot step")


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    bid: Decimal
    ask: Decimal
    time: datetime

    def __post_init__(self) -> None:
        financial_fields(self, ("bid", "ask"), positive=True)
        aware_utc(self.time)
        if self.ask < self.bid:
            raise BrokerError("crossed quote")

    def fresh(self, clock: Clock, max_age_seconds: int) -> None:
        age = (aware_utc(clock.now()) - aware_utc(self.time)).total_seconds()
        if age < -2 or age > max_age_seconds:
            raise StaleData("quote is stale or from the future")

    def spread_points(self, symbol: SymbolInfo) -> Decimal:
        return (self.ask - self.bid) / symbol.point

    def entry(self, side: Side) -> Decimal:
        return self.ask if side == Side.BUY else self.bid

    def exit(self, side: Side) -> Decimal:
        return self.bid if side == Side.BUY else self.ask


@dataclass(frozen=True, slots=True)
class Position:
    ticket: int
    identifier: int
    symbol: str
    side: Side
    volume: Decimal
    entry_price: Decimal
    sl: Decimal
    tp: Decimal
    time: datetime
    magic: int
    profit: Decimal = ZERO  # gross account-currency PnL, NOT USD/net
    swap: Decimal = ZERO
    entry_commission: Decimal | None = None
    comment: str = ""

    def __post_init__(self) -> None:
        financial_fields(self, ("volume", "entry_price"), positive=True)
        financial_fields(self, ("sl", "tp", "profit", "swap"))
        aware_utc(self.time)
        if self.entry_commission is not None:
            financial_fields(self, ("entry_commission",))
        if self.ticket <= 0 or self.identifier <= 0 or self.sl < ZERO or self.tp < ZERO:
            raise BrokerError("invalid position identity/protection")


@dataclass(frozen=True, slots=True)
class Deal:
    ticket: int
    order_ticket: int
    position_identifier: int
    symbol: str
    type: str
    entry: str
    time: datetime
    volume: Decimal
    price: Decimal
    profit: Decimal
    commission: Decimal
    swap: Decimal
    fee: Decimal
    magic: int
    currency: str
    reason: str = ""
    comment: str = ""

    def __post_init__(self) -> None:
        financial_fields(self, ("volume", "price", "profit", "commission", "swap", "fee"))
        aware_utc(self.time)
        if self.ticket <= 0 or self.position_identifier < 0 or self.volume < ZERO or self.price < ZERO:
            raise BrokerError("invalid deal identity/quantity")

    @property
    def net(self) -> Decimal:
        # One DEAL's cash effect; aggregate all legs for a position's whole PnL.
        return self.profit + self.commission + self.swap + self.fee


@dataclass(frozen=True, slots=True)
class MarketOrder:
    symbol: str
    side: Side
    volume: Decimal
    reference_price: Decimal
    sl: Decimal
    tp: Decimal
    idempotency_key: str
    created_at: datetime
    strategy: str = "manual_simulation"

    def __post_init__(self) -> None:
        financial_fields(self, ("volume", "reference_price", "sl", "tp"), positive=True)
        aware_utc(self.created_at)
        valid_key(self.idempotency_key)
        if not isinstance(self.side, Side):
            raise InvalidOrder("side must be a Side enum")
        if self.side.sign * (self.reference_price - self.sl) <= ZERO:
            raise InvalidOrder("SL must be on the loss side of the reference entry")
        if self.side.sign * (self.tp - self.reference_price) <= ZERO:
            raise InvalidOrder("TP must be on the profit side of the reference entry")


@dataclass(frozen=True, slots=True)
class BrokerCommand:
    operation: Operation
    idempotency_key: str
    created_at: datetime
    order: MarketOrder | None = None
    ticket: int | None = None
    position_identifier: int | None = None
    sl: Decimal | None = None
    tp: Decimal | None = None

    def __post_init__(self) -> None:
        valid_key(self.idempotency_key)
        aware_utc(self.created_at)
        if self.operation == Operation.OPEN:
            if self.order is None or self.order.idempotency_key != self.idempotency_key:
                raise InvalidOrder("open command must bind the same order/key")
        elif not self.ticket or not self.position_identifier:
            raise InvalidOrder("maintenance must bind ticket AND stable position identifier")
        if self.operation == Operation.PROTECT and self.sl is None and self.tp is None:
            raise InvalidOrder("protection modification is empty")

    @property
    def request_hash(self) -> str:
        payload = asdict(self)
        # Maintenance retries bind semantic payload, not a freshly-generated
        # wrapper timestamp. An OPEN still includes its immutable order timestamp.
        payload.pop("created_at")
        return sha256_json(payload)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    operation: Operation
    idempotency_key: str
    account_key: str
    status: ResultStatus
    order_ticket: int = 0
    deal_ticket: int = 0
    position_identifier: int | None = None
    filled_volume: Decimal = ZERO
    filled_price: Decimal | None = None
    retcode: int = 0
    retryable: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        valid_key(self.idempotency_key)
        financial_fields(self, ("filled_volume",))
        if self.filled_price is not None:
            financial_fields(self, ("filled_price",), positive=True)

    @property
    def requires_reconciliation(self) -> bool:
        return self.status in {ResultStatus.UNKNOWN, ResultStatus.ACCEPTED, ResultStatus.PARTIAL} or (
            self.status == ResultStatus.FILLED and self.position_identifier is None
        )


@runtime_checkable
class MarketData(Protocol):
    source_kind: SourceKind
    clock: Clock

    async def initialize(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def get_account_info(self) -> AccountInfo: ...
    async def get_symbols(self) -> tuple[str, ...]: ...
    async def select_symbol(self, symbol: str) -> SymbolInfo: ...
    async def get_symbol_info(self, symbol: str) -> SymbolInfo: ...
    async def get_tick(self, symbol: str) -> Tick: ...
    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 300, *, as_of: datetime | None = None
    ) -> pd.DataFrame: ...
    async def calculate_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal: ...
    async def calculate_margin(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal: ...


class Broker(MarketData, Protocol):
    async def get_positions(self) -> tuple[Position, ...]: ...
    async def get_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]: ...
    async def get_closed_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]: ...
    async def open_market_buy(self, order: MarketOrder) -> ExecutionResult: ...
    async def open_market_sell(self, order: MarketOrder) -> ExecutionResult: ...
    async def close_position(
        self, ticket: int, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult: ...
    async def modify_sl(
        self, ticket: int, sl: Decimal, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult: ...
    async def modify_tp(
        self, ticket: int, tp: Decimal, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult: ...
