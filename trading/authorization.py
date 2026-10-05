"""Fail-closed integration seam for Part 5's durable risk/execution authority.

Implementations are trusted internal code, never values supplied by an API/AI.
All methods run on the broker worker (except on_uncertain after a caller timeout)
and MUST NOT call the async MT5 client recursively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trading.types import (
    ZERO,
    AccountInfo,
    BrokerCommand,
    ExecutionResult,
    Position,
    SourceKind,
    SymbolInfo,
    Tick,
    TradingDisabled,
    aware_utc,
    financial_fields,
)


@dataclass(frozen=True, slots=True)
class PositionRisk:
    identifier: int
    loss_account: Decimal

    def __post_init__(self):
        financial_fields(self, ("loss_account",))
        if self.identifier <= 0 or self.loss_account < ZERO:
            raise TradingDisabled("invalid current position-risk valuation")


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    account: AccountInfo
    symbol: SymbolInfo
    tick: Tick
    positions: tuple[Position, ...]
    worst_loss_account: Decimal
    required_margin_account: Decimal
    expected_reward_account: Decimal
    observed_at: datetime
    position_risks: tuple[PositionRisk, ...] = ()
    usd_asset_rate: Decimal | None = None
    usd_liability_rate: Decimal | None = None
    data_source: SourceKind | None = None
    durable_simulation: bool = False

    def __post_init__(self):
        financial_fields(self, ("worst_loss_account", "required_margin_account", "expected_reward_account"))
        aware_utc(self.observed_at)
        if type(self.durable_simulation) is not bool or (
            self.data_source is not None and not isinstance(self.data_source, SourceKind)
        ):
            raise TradingDisabled("snapshot provenance/durability is invalid")
        for rate in (self.usd_asset_rate, self.usd_liability_rate):
            if rate is not None and (not isinstance(rate, Decimal) or not rate.is_finite() or rate <= ZERO):
                raise TradingDisabled("snapshot FX rate is invalid")
        if len({risk.identifier for risk in self.position_risks}) != len(self.position_risks):
            raise TradingDisabled("snapshot has duplicate position-risk IDs")

    def dollars(self, amount: Decimal) -> Decimal:
        if self.account.currency == "USD":
            return amount
        rate = self.usd_asset_rate if amount >= ZERO else self.usd_liability_rate
        if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= ZERO:
            raise TradingDisabled("snapshot lacks verified account/USD conversion")
        return amount * rate


@dataclass(frozen=True, slots=True)
class WriteGrant:
    account_key: str
    request_hash: str
    config_hash: str
    expires_at: datetime
    max_volume: Decimal
    max_loss_account: Decimal
    max_margin_account: Decimal
    entry_gates_verified: bool = False
    owner_live_confirmed: bool = False
    owned_position_verified: bool = False
    allow_tp_extension: bool = False
    original_tp: Decimal | None = None


class WriteAuthority(Protocol):
    def authorize(self, command: BrokerCommand, snapshot: BrokerSnapshot) -> WriteGrant:
        """Verify gates and COMMIT unique intent/reserved risk before returning."""
        ...

    def before_send(self, command: BrokerCommand, snapshot: BrokerSnapshot, grant: WriteGrant) -> None:
        """Revalidate durable controls and fresh caps without another reservation."""
        ...

    def on_result(self, command: BrokerCommand, result: ExecutionResult) -> None:
        """Durably record broker acknowledgement, including partial/unknown state."""
        ...

    def on_uncertain(self, command: BrokerCommand, reason: str) -> None:
        """Latch a halt; never downgrade an already-reconciled/filled intent."""
        ...


class DenyAllWrites:
    def authorize(self, command: BrokerCommand, snapshot: BrokerSnapshot) -> WriteGrant:
        raise TradingDisabled("no durable risk/approval authority is connected; broker writes are disabled")

    def on_result(self, command: BrokerCommand, result: ExecutionResult) -> None:
        raise TradingDisabled("deny-all authority cannot record a broker fill")

    def on_uncertain(self, command: BrokerCommand, reason: str) -> None:
        raise TradingDisabled("unexpected write reached the deny-all authority")


def validate_write_grant(command, snapshot, grant, settings, clock):
    """Common final binding/bounds validation for native and shadow writes."""
    from datetime import timedelta

    from core.settings import OperatingMode
    from trading.types import Operation, RiskViolation, aware_utc

    if (
        grant.account_key != snapshot.account.key
        or grant.request_hash != command.request_hash
        or grant.config_hash != settings.safety_fingerprint()
    ):
        raise TradingDisabled("authorization does not bind account/request/configuration")
    now, expiry = clock.now(), aware_utc(grant.expires_at)
    if not now < expiry <= now + timedelta(seconds=settings.order_max_age_seconds):
        raise TradingDisabled("authorization is expired or exceeds the short permit lifetime")
    for amount in (grant.max_volume, grant.max_loss_account, grant.max_margin_account):
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < ZERO:
            raise TradingDisabled("invalid authorization financial bound")
    if command.operation == Operation.OPEN:
        if grant.entry_gates_verified is not True or (
            settings.mode == OperatingMode.LIVE and grant.owner_live_confirmed is not True
        ):
            raise TradingDisabled("stage/risk/owner entry gates are not verified")
        order = command.order
        if (
            order is None
            or order.volume > grant.max_volume
            or snapshot.worst_loss_account > grant.max_loss_account
            or snapshot.required_margin_account > grant.max_margin_account
        ):
            raise RiskViolation("fresh broker valuation exceeds the authorized risk/volume/margin")
    elif grant.owned_position_verified is not True:
        raise TradingDisabled("maintenance authority did not verify ownership")
