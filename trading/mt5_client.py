"""Windows MT5 adapter. One leased SDK, one worker, fail-closed broker writes.

No automatic retry of order_send. Timeout/cancellation is an uncertain execution
and permanently quarantines writes in this instance. Default authority denies ALL
writes. Parts 5/10 connect the durable authority and runtime, not an API caller.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, TypeVar

import pandas as pd

from core.security import sanitize_text, secret_values
from core.settings import TIMEFRAME_MINUTES, OperatingMode, Settings
from trading.authorization import (
    BrokerSnapshot,
    DenyAllWrites,
    PositionRisk,
    WriteAuthority,
    WriteGrant,
    validate_write_grant,
)
from trading.candles import validated_candles
from trading.client_helpers import ClientCalculations
from trading.currency import ConversionQuote
from trading.price_rules import adverse_price, protection_prices, validate_entry, validate_volume
from trading.types import (
    ZERO,
    AccountInfo,
    AccountKind,
    BrokerCommand,
    BrokerError,
    Clock,
    ConnectionUnavailable,
    Deal,
    ExecutionResult,
    IdentityChanged,
    InvalidOrder,
    MarketOrder,
    Operation,
    Position,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    SymbolInfo,
    SystemClock,
    Tick,
    TradingDisabled,
    UncertainExecution,
    UnsupportedSymbol,
    aware_utc,
    decimal_value,
)

T = TypeVar("T")
_LOG = logging.getLogger("reflexbot.mt5")
_LEASE_LOCK = Lock()
_SDK_LEASES: dict[int, object] = {}
# Only documented explicit no-fill retcodes enter the rejected category.
_NO_FILL = {
    10004,
    10006,
    10007,
    10013,
    10014,
    10015,
    10016,
    10017,
    10018,
    10019,
    10020,
    10021,
    10022,
    10026,
    10027,
    10029,
    10030,
    10032,
    10033,
    10034,
    10035,
    10036,
    10038,
    10039,
    10040,
    10041,
    10042,
    10043,
    10044,
    10045,
    10046,
}


class MT5Client(ClientCalculations):
    def __init__(
        self,
        settings: Settings,
        *,
        authority: WriteAuthority | None = None,
        sdk: Any = None,
        clock: Clock | None = None,
    ) -> None:
        if settings.mt5_backend != "real":
            raise TradingDisabled("real MT5 client requires MT5_BACKEND=real")
        if sdk is not None and getattr(sdk, "__reflexbot_test_sdk__", False) is not True:
            raise TradingDisabled(
                "SDK injection requires an explicitly marked test SDK, never the native module"
            )
        if sdk is None and sys.platform != "win32":
            raise ConnectionUnavailable("native MT5 requires Windows x64; use MockMT5Client here")
        if sdk is None and clock is not None and type(clock) is not SystemClock:
            raise TradingDisabled("real MT5 cannot use a simulated clock")
        self.settings, self.clock = settings, clock or SystemClock()
        self.source_kind = SourceKind.TEST_SDK if sdk is not None else SourceKind.MT5
        self._api = sdk
        self._authority = authority or DenyAllWrites()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-broker")
        self._lock, self._state_lock = RLock(), RLock()
        self._async_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lease_token = object()
        self._leased = False
        self._pin: str | None = None
        self._connected = False
        self._closing = False
        self._closed = False
        self._quarantined = False
        self._quarantine_reason = ""
        self._last_read: datetime | None = None
        self._pending_calls = set()
        self._cache: dict[str, tuple[str, ExecutionResult]] = {}
        self._secrets = secret_values(settings)

    def bind_authority(self, authority: WriteAuthority) -> None:
        """Only trusted composition code may bind a durable authority before writes."""
        with self._state_lock:
            if self._cache or self._quarantined or self._closing:
                raise TradingDisabled("authority cannot replace an active/quarantined write runtime")
            if not isinstance(self._authority, DenyAllWrites):
                raise TradingDisabled("authority is already bound")
            self._authority = authority

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "source": self.source_kind.value,
                "connected": self._connected,
                "writes_quarantined": self._quarantined,
                "reason": self._quarantine_reason,
                "last_success": self._last_read.isoformat() if self._last_read else None,
                "write_authority": type(self._authority).__name__,
                "pending_calls": len(self._pending_calls),
            }

    def _quarantine(self, reason: str) -> None:
        with self._state_lock:
            self._quarantined, self._quarantine_reason = True, reason
        _LOG.error("Broker writes quarantined: %s", reason)

    def _guarded(self, function: Callable[[], T]) -> T:
        with self._lock:
            try:
                result = function()
                with self._state_lock:
                    self._last_read = self.clock.now()
                return result
            except BrokerError:
                raise
            except Exception:
                raise BrokerError("SDK/authority operation failed; raw error suppressed") from None

    async def _call(
        self, function: Callable[[], T], *, command: BrokerCommand | None = None, allow_closing: bool = False
    ) -> T:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise BrokerError("one MT5 client must belong to one asyncio event loop")
        async with self._async_lock:
            if self._closed or (self._closing and not allow_closing):
                raise ConnectionUnavailable("client is shutting down")
            if command is not None and self._quarantined:
                raise UncertainExecution("write quarantine requires reconciliation and a fresh runtime")
            with self._state_lock:
                if self._pending_calls and not allow_closing:
                    raise ConnectionUnavailable(
                        "prior native work remains unresolved; no additional SDK queueing"
                    )
            # Track the CONCURRENT future, not merely its asyncio wrapper: a
            # timed-out/cancelled wrapper does not mean the native thread exited.
            native_future = self._worker.submit(self._guarded, function)
            with self._state_lock:
                self._pending_calls.add(native_future)

            def native_finished(completed):
                with self._state_lock:
                    self._pending_calls.discard(completed)

            native_future.add_done_callback(native_finished)
            future = asyncio.wrap_future(native_future, loop=loop)

            # Drain exceptions from late results; the durable callback still runs
            # on the worker even if the awaiting coroutine times out/cancels.
            def drain(completed: asyncio.Future) -> None:
                if not completed.cancelled():
                    completed.exception()

            future.add_done_callback(drain)
            try:
                return await asyncio.wait_for(asyncio.shield(future), self.settings.mt5_api_timeout_seconds)
            except (TimeoutError, asyncio.CancelledError) as exc:
                self._quarantine("native operation timed out/cancelled; no automatic write retry")
                if command is not None:
                    try:
                        await asyncio.to_thread(
                            self._authority.on_uncertain, command, "caller_timeout_or_cancel"
                        )
                    except Exception:
                        _LOG.error("Could not persist uncertain execution; keep quarantine")
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if command is not None:
                    raise UncertainExecution(
                        "order acknowledgement is uncertain; reconcile broker history"
                    ) from None
                raise ConnectionUnavailable(
                    "native read timed out; client writes remain quarantined"
                ) from None

    def _load_sdk(self) -> None:
        if self._api is None:
            self._api = importlib.import_module("MetaTrader5")
        if not self._leased:
            with _LEASE_LOCK:
                owner = _SDK_LEASES.get(id(self._api))
                if owner is not None and owner is not self._lease_token:
                    raise ConnectionUnavailable("MT5 SDK already leased by another client in this process")
                _SDK_LEASES[id(self._api)] = self._lease_token
                self._leased = True

    def _error(self, operation: str) -> ConnectionUnavailable:
        value = self._api.last_error()
        code = value[0] if isinstance(value, tuple) and value and isinstance(value[0], int) else "unknown"
        return ConnectionUnavailable(f"{operation} failed (SDK code {code}); credentials/body suppressed")

    def _account_sync(self) -> AccountInfo:
        raw = self._api.account_info()
        if raw is None:
            raise self._error("account_info")
        kinds = {0: AccountKind.DEMO, 1: AccountKind.CONTEST, 2: AccountKind.REAL}
        kind = kinds.get(int(raw.trade_mode))
        if kind is None:
            raise ConnectionUnavailable("unknown broker account mode")
        account = AccountInfo(
            int(raw.login),
            str(raw.server),
            str(raw.currency).upper(),
            kind,
            self.source_kind,
            decimal_value(raw.balance),
            decimal_value(raw.equity),
            decimal_value(raw.margin),
            decimal_value(raw.margin_free),
            decimal_value(getattr(raw, "credit", 0)),
            int(getattr(raw, "leverage", 0)),
            bool(getattr(raw, "trade_allowed", False)),
            bool(getattr(raw, "trade_expert", False)),
        )
        if account.currency != self.settings.account_currency:
            self._quarantine("terminal currency mismatch")
            raise IdentityChanged("declared account currency differs from terminal currency")
        if self.settings.mt5_login and (
            account.login != self.settings.mt5_login or account.server != self.settings.mt5_server
        ):
            self._quarantine("configured login/server mismatch")
            raise IdentityChanged("terminal does not match the configured login/server")
        if self.settings.mode == OperatingMode.DEMO and kind != AccountKind.DEMO:
            self._quarantine("demo execution attached to a non-demo account")
            raise IdentityChanged("DEMO_TRADING requires the terminal's actual DEMO account")
        if self.settings.mode == OperatingMode.LIVE and kind != AccountKind.REAL:
            self._quarantine("live execution attached to a non-real account")
            raise IdentityChanged("LIVE_TRADING requires the terminal's actual REAL account")
        if self._pin is not None and account.key != self._pin:
            self._quarantine("terminal account changed")
            raise IdentityChanged("terminal identity changed; do not auto-bind another account")
        if self._pin is None:
            self._pin = account.key
        return account

    def _connect_sync(self) -> None:
        self._load_sdk()
        if self.source_kind == SourceKind.MT5:
            path = Path(self.settings.mt5_terminal_path)
            if not path.is_file() or path.name.lower() != "terminal64.exe":
                raise ConnectionUnavailable("configured terminal64.exe does not exist")
        for attempt in range(self.settings.mt5_reconnect_attempts):
            if self._closing:
                raise ConnectionUnavailable("connection aborted during shutdown")
            success = self._api.initialize(
                self.settings.mt5_terminal_path, timeout=self.settings.mt5_connect_timeout_ms
            )
            if success and self.settings.mt5_login:
                success = self._api.login(
                    self.settings.mt5_login,
                    password=self.settings.mt5_password.get_secret_value(),
                    server=self.settings.mt5_server,
                    timeout=self.settings.mt5_connect_timeout_ms,
                )
            terminal = self._api.terminal_info() if success else None
            if terminal is not None and terminal.connected:
                if self.source_kind == SourceKind.MT5:
                    actual = os.path.normcase(os.path.realpath(str(terminal.path)))
                    expected = os.path.normcase(
                        os.path.realpath(str(Path(self.settings.mt5_terminal_path).parent))
                    )
                    if actual != expected:
                        self._quarantine("unexpected terminal installation")
                        raise IdentityChanged("SDK attached to an unexpected terminal installation")
                self._account_sync()
                self._connected = True
                _LOG.info(
                    "MT5 connected; source=%s; execution authority=%s",
                    self.source_kind.value,
                    type(self._authority).__name__,
                )
                return
            self._connected = False
            self._api.shutdown()
            if attempt + 1 < self.settings.mt5_reconnect_attempts:
                time.sleep(self.settings.mt5_reconnect_backoff_seconds)
        raise self._error("initialize/login")

    def _ensure_sync(self) -> AccountInfo:
        self._load_sdk()
        terminal = self._api.terminal_info()
        if not self._connected or terminal is None or not terminal.connected:
            self._connected = False
            self._connect_sync()  # Read/connect retry only; NEVER retries order_send.
        return self._account_sync()

    async def initialize(self) -> None:
        await self._call(self._connect_sync)

    async def reconnect(self) -> None:
        # A reconnect never resets write quarantine, account pin or owner approval.
        await self._call(self._connect_sync)

    def _shutdown_sync(self) -> None:
        try:
            if self._api is not None and self._leased:
                self._api.shutdown()
        finally:
            self._connected = False
            with _LEASE_LOCK:
                if self._api is not None and _SDK_LEASES.get(id(self._api)) is self._lease_token:
                    del _SDK_LEASES[id(self._api)]
                self._leased = False

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closing = True
        try:
            await self._call(self._shutdown_sync, allow_closing=True)
        finally:
            self._closed = True
            # Never join a hung native thread on the event loop. A failed shutdown
            # retains its SDK lease until the worker actually releases it.
            self._worker.shutdown(wait=False, cancel_futures=False)

    async def __aenter__(self):
        try:
            await self.initialize()
            return self
        except BaseException:
            await self.shutdown()
            raise

    async def __aexit__(self, *args):
        await self.shutdown()

    async def get_account_info(self) -> AccountInfo:
        return await self._call(self._ensure_sync)

    def _meta_sync(self, name: str) -> SymbolInfo:
        raw = self._api.symbol_info(name)
        if raw is None or str(raw.name) != name:
            raise UnsupportedSymbol("symbol unavailable; configure the exact broker alias")
        if not raw.visible and not self._api.symbol_select(name, True):
            raise UnsupportedSymbol("broker symbol selection failed")
        return SymbolInfo(
            name,
            decimal_value(raw.point),
            decimal_value(raw.trade_tick_size),
            decimal_value(raw.trade_tick_value_profit),
            decimal_value(raw.trade_tick_value_loss),
            decimal_value(raw.trade_contract_size),
            decimal_value(raw.volume_min),
            decimal_value(raw.volume_max),
            decimal_value(raw.volume_step),
            int(raw.digits),
            str(raw.currency_base),
            str(raw.currency_profit),
            int(raw.trade_mode),
            int(raw.trade_stops_level),
            int(raw.trade_freeze_level),
            int(raw.filling_mode),
            int(raw.trade_exemode),
            int(raw.order_mode),
            decimal_value(getattr(raw, "volume_limit", 0)),
            True,
            int(raw.trade_calc_mode),
        )

    async def get_symbols(self) -> tuple[str, ...]:
        def read():
            self._ensure_sync()
            rows = self._api.symbols_get()
            if rows is None:
                raise self._error("symbols_get")
            if len(rows) > 50000:
                raise BrokerError("symbol response exceeds the safety bound")
            return tuple(str(row.name) for row in rows)

        return await self._call(read)

    async def select_symbol(self, symbol: str) -> SymbolInfo:
        def select():
            self._ensure_sync()
            if not self._api.symbol_select(symbol, True):
                raise UnsupportedSymbol("symbol_select failed")
            return self._meta_sync(symbol)

        return await self._call(select)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        def read():
            self._ensure_sync()
            return self._meta_sync(symbol)

        return await self._call(read)

    def _tick_sync(self, meta: SymbolInfo) -> Tick:
        raw = self._api.symbol_info_tick(meta.name)
        if raw is None:
            raise self._error("symbol_info_tick")
        milliseconds = int(getattr(raw, "time_msc", 0))
        timestamp = datetime.fromtimestamp(
            milliseconds / 1000 if milliseconds else int(raw.time), timezone.utc
        )
        quantum = Decimal(1).scaleb(-meta.digits)
        return Tick(
            meta.name,
            decimal_value(raw.bid).quantize(quantum),
            decimal_value(raw.ask).quantize(quantum),
            timestamp,
        )

    async def get_tick(self, symbol: str) -> Tick:
        def read():
            self._ensure_sync()
            return self._tick_sync(self._meta_sync(symbol))

        return await self._call(read)

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 300, *, as_of: datetime | None = None
    ) -> pd.DataFrame:
        if timeframe not in TIMEFRAME_MINUTES or not 1 <= count <= 10000:
            raise BrokerError("invalid timeframe/history count")
        cutoff = aware_utc(as_of or self.clock.now())
        if cutoff > self.clock.now():
            raise BrokerError("future history request is forbidden")

        def read():
            self._ensure_sync()
            self._meta_sync(symbol)
            native_tf = getattr(self._api, "TIMEFRAME_" + timeframe)
            if as_of is None:
                rows = self._api.copy_rates_from_pos(symbol, native_tf, 0, count + 1)
            else:
                rows = self._api.copy_rates_from(symbol, native_tf, cutoff, count + 1)
            if rows is None or len(rows) == 0:
                raise self._error("copy_rates")
            frame = pd.DataFrame(rows)
            frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
            nominal = frame["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
            # Never use the newest (possibly forming) bar. A next opening
            # supplies a conservative session/DST boundary for its predecessor.
            frame["close_time"] = pd.concat([nominal, frame["time"].shift(-1)], axis=1).max(axis=1)
            return validated_candles(frame.iloc[:-1], timeframe, cutoff).tail(count).reset_index(drop=True)

        return await self._call(read)

    def _positions_sync(self) -> tuple[Position, ...]:
        rows = self._api.positions_get()
        if rows is None:
            raise self._error("positions_get")  # None is NOT the empty tuple.
        if len(rows) > 10000 or any(row.type not in (0, 1) for row in rows):
            raise BrokerError("position count/side is outside the supported broker contract")
        return tuple(
            Position(
                int(row.ticket),
                int(row.identifier),
                str(row.symbol),
                Side.BUY if row.type == 0 else Side.SELL,
                decimal_value(row.volume),
                decimal_value(row.price_open),
                decimal_value(row.sl),
                decimal_value(row.tp),
                datetime.fromtimestamp(int(row.time), timezone.utc),
                int(row.magic),
                decimal_value(row.profit),
                decimal_value(row.swap),
                None,
                sanitize_text(str(row.comment), self._secrets),
            )
            for row in rows
        )

    async def get_positions(self) -> tuple[Position, ...]:
        def read():
            self._ensure_sync()
            return self._positions_sync()

        return await self._call(read)

    async def get_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]:
        start, end = aware_utc(since), aware_utc(until or self.clock.now())
        if start > end or end > self.clock.now():
            raise BrokerError("invalid deal-history interval")

        def read():
            account = self._ensure_sync()
            rows = self._api.history_deals_get(start, end)
            if rows is None:
                raise self._error("history_deals_get")
            if len(rows) > 100000:
                raise BrokerError("deal response too large; request a smaller interval")
            types = {
                0: "buy",
                1: "sell",
                2: "balance",
                3: "credit",
                4: "charge",
                5: "correction",
                6: "bonus",
                7: "commission",
                8: "commission_daily",
                9: "commission_monthly",
                10: "commission_agent_daily",
                11: "commission_agent_monthly",
                12: "interest",
                13: "buy_canceled",
                14: "sell_canceled",
                15: "dividend",
                16: "dividend_franked",
                17: "tax",
            }
            entries = {0: "in", 1: "out", 2: "inout", 3: "out_by"}
            output = []
            for row in rows:
                ms = int(getattr(row, "time_msc", 0))
                instant = datetime.fromtimestamp(ms / 1000 if ms else int(row.time), timezone.utc)
                if not start <= instant <= end:
                    continue
                output.append(
                    Deal(
                        int(row.ticket),
                        int(row.order),
                        int(row.position_id),
                        str(row.symbol),
                        types.get(int(row.type), "unknown_" + str(row.type)),
                        entries.get(int(row.entry), "unknown") if row.type in (0, 1) else "cash",
                        instant,
                        decimal_value(row.volume),
                        decimal_value(row.price),
                        decimal_value(row.profit),
                        decimal_value(row.commission),
                        decimal_value(row.swap),
                        decimal_value(getattr(row, "fee", 0)),
                        int(row.magic),
                        account.currency,
                        str(row.reason),
                        sanitize_text(str(row.comment), self._secrets),
                    )
                )
            return tuple(sorted(output, key=lambda deal: (deal.time, deal.ticket)))

        return await self._call(read)

    async def get_settled_orders(self, tickets: tuple[int, ...]) -> frozenset[int]:
        """Positive final-order evidence for a partially filled native order."""
        if len(tickets) > 100 or any(type(ticket) is not int or not 0 < ticket < 2**63 for ticket in tickets):
            raise BrokerError("invalid bounded final-order query")

        def read():
            self._ensure_sync()
            history = getattr(self._api, "history_orders_get", None)
            if history is None:
                return frozenset()  # TEST SDK lacking the API is NOT final-order proof.
            final = set()
            for ticket in tickets:
                rows = history(ticket=ticket)
                if rows is None:
                    raise self._error("history_orders_get")
                for row in rows:
                    if (
                        int(row.ticket) == ticket
                        and int(row.magic) == self.settings.mt5_magic_number
                        and int(row.state) in {2, 4, 5, 6}
                        and decimal_value(row.volume_initial) > ZERO
                    ):
                        final.add(ticket)
            return frozenset(final)

        return await self._call(read)

    async def get_closed_deals(self, since: datetime, until: datetime | None = None) -> tuple[Deal, ...]:
        return tuple(
            deal for deal in await self.get_deals(since, until) if deal.entry in {"out", "inout", "out_by"}
        )

    def _profit_sync(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal:
        value = self._api.order_calc_profit(
            0 if side == Side.BUY else 1, symbol, float(volume), float(entry), float(exit_price)
        )
        if value is None:
            raise self._error("order_calc_profit")
        return decimal_value(value)

    def _margin_sync(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal:
        value = self._api.order_calc_margin(0 if side == Side.BUY else 1, symbol, float(volume), float(entry))
        if value is None:
            raise self._error("order_calc_margin")
        result = decimal_value(value)
        if result < ZERO:
            raise RiskViolation("negative native margin")
        return result

    async def calculate_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal:
        def read():
            self._ensure_sync()
            self._meta_sync(symbol)
            if min(volume, entry, exit_price) <= ZERO:
                raise InvalidOrder("invalid native valuation inputs")
            return self._profit_sync(symbol, side, volume, entry, exit_price)

        return await self._call(read)

    async def calculate_margin(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal:
        def read():
            self._ensure_sync()
            self._meta_sync(symbol)
            if min(volume, entry) <= ZERO:
                raise InvalidOrder("invalid native margin inputs")
            return self._margin_sync(symbol, side, volume, entry)

        return await self._call(read)

    def _cost_sync(self, volume: Decimal, account: AccountInfo) -> Decimal:
        dollars = self.settings.commission_round_turn_usd_per_lot * volume
        if account.currency == "USD":
            return dollars
        symbol = self.settings.account_to_usd_symbols.get(account.currency)
        if not symbol:
            raise RiskViolation("account currency lacks an explicit USD conversion route")
        meta = self._meta_sync(symbol)
        tick = self._tick_sync(meta)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        return -ConversionQuote(meta, tick).convert(-dollars, "USD", account.currency)

    def _snapshot_sync(self, account, meta, tick, positions, risk, margin, reward, *, entry):
        asset, liability = None, None
        valuations = []
        if entry:
            if account.currency == "USD":
                asset = liability = Decimal("1")
            else:
                route = self.settings.account_to_usd_symbols.get(account.currency)
                if not route:
                    raise RiskViolation("missing account/USD risk conversion route")
                fxmeta = self._meta_sync(route)
                fxtick = self._tick_sync(fxmeta)
                fxtick.fresh(self.clock, self.settings.max_tick_age_seconds)
                fx = ConversionQuote(fxmeta, fxtick)
                asset = fx.convert(Decimal("1"), account.currency, "USD")
                liability = -fx.convert(Decimal("-1"), account.currency, "USD")
            for position in positions:
                pmeta = self._meta_sync(position.symbol)
                ptick = self._tick_sync(pmeta)
                ptick.fresh(self.clock, self.settings.max_tick_age_seconds)
                if position.sl <= ZERO:
                    loss = account.risk_capital
                else:
                    exit_price = adverse_price(
                        position.sl, position.side, pmeta, self.settings.max_slippage_points, entry=False
                    )
                    at_stop = self._profit_sync(
                        position.symbol, position.side, position.volume, position.entry_price, exit_price
                    )
                    current = self._profit_sync(
                        position.symbol,
                        position.side,
                        position.volume,
                        position.entry_price,
                        ptick.exit(position.side),
                    )
                    costs = self._cost_sync(position.volume, account)
                    loss = max(ZERO, -at_stop - position.swap + costs, current - at_stop + costs / 2)
                valuations.append(PositionRisk(position.identifier, loss))
        return BrokerSnapshot(
            account,
            meta,
            tick,
            positions,
            risk,
            margin,
            reward,
            self.clock.now(),
            tuple(valuations),
            asset,
            liability,
            self.source_kind,
            False,
        )

    @staticmethod
    def _filling(meta: SymbolInfo) -> int:
        if meta.filling_mode & 1:
            return 0  # ORDER_FILLING_FOK
        if meta.filling_mode & 2:
            return 1  # ORDER_FILLING_IOC; partial acknowledgement must be reconciled
        if meta.execution_mode != 2:
            return 2  # RETURN is forbidden for Market Execution
        raise InvalidOrder("no supported filling policy; do not guess an order enum")

    def _validate_grant(self, command: BrokerCommand, snapshot: BrokerSnapshot, grant: WriteGrant) -> None:
        validate_write_grant(command, snapshot, grant, self.settings, self.clock)

    def _acknowledgement(self, command: BrokerCommand, account: AccountInfo, raw: Any) -> ExecutionResult:
        if raw is None:
            return ExecutionResult(
                command.operation,
                command.idempotency_key,
                account.key,
                ResultStatus.UNKNOWN,
                reason="empty_order_send_ack",
            )
        code = int(raw.retcode)
        if code == 10009:
            status = ResultStatus.FILLED
        elif code == 10010:
            status = ResultStatus.PARTIAL
        elif code == 10008:
            status = ResultStatus.ACCEPTED
        elif code == 10025 and command.operation == Operation.PROTECT:
            status = ResultStatus.NO_CHANGE
        elif code in _NO_FILL:
            status = ResultStatus.REJECTED
        else:
            status = ResultStatus.UNKNOWN  # TIMEOUT/CONNECTION/LOCKED/unrecognized are uncertain.
        volume = decimal_value(getattr(raw, "volume", 0))
        price = decimal_value(getattr(raw, "price", 0))
        order_id, deal_id = int(getattr(raw, "order", 0)), int(getattr(raw, "deal", 0))
        if min(order_id, deal_id) < 0 or max(order_id, deal_id) >= 2**63:
            status = ResultStatus.UNKNOWN
        if volume < ZERO or (command.order and volume > command.order.volume):
            status = ResultStatus.UNKNOWN
        # order_ticket is NOT a position ticket/identifier. Reconcile the deal's
        # position_id against positions.identifier in ExecutionEngine (Part 5).
        return ExecutionResult(
            command.operation,
            command.idempotency_key,
            account.key,
            status,
            order_id,
            deal_id,
            None,
            volume,
            price if price > ZERO else None,
            code,
            code in {10004, 10020, 10021} and status == ResultStatus.REJECTED,
            "broker_retcode_" + str(code),
        )

    def _entry_valuation(self, order: MarketOrder, account: AccountInfo, meta: SymbolInfo, tick: Tick):
        entry = tick.entry(order.side)
        worst_entry = adverse_price(entry, order.side, meta, self.settings.max_slippage_points, entry=True)
        worst_exit = adverse_price(order.sl, order.side, meta, self.settings.max_slippage_points, entry=False)
        if min(worst_entry, worst_exit) <= ZERO:
            raise RiskViolation("slippage bound crosses nonpositive prices")
        cost = self._cost_sync(order.volume, account)
        risk = -self._profit_sync(order.symbol, order.side, order.volume, worst_entry, worst_exit) + cost
        reward = (
            self._profit_sync(
                order.symbol,
                order.side,
                order.volume,
                worst_entry,
                adverse_price(order.tp, order.side, meta, self.settings.max_slippage_points, entry=False),
            )
            - cost
        )
        margin = self._margin_sync(order.symbol, order.side, order.volume, worst_entry)
        budget = account.risk_capital * self.settings.effective_risk_percent / Decimal("100")
        available = min(
            account.margin_free,
            account.risk_capital * self.settings.max_margin_usage_percent / Decimal("100") - account.margin,
        )
        if (
            risk <= ZERO
            or risk > budget
            or margin > available
            or reward / risk < self.settings.min_net_reward_risk
        ):
            raise RiskViolation("fresh nominal risk/margin/reward limits reject the entry")
        return risk, margin, reward

    def _write_sync(self, command: BrokerCommand) -> ExecutionResult:
        if self._quarantined:
            raise UncertainExecution("write quarantine is latched")
        if self.settings.mode not in {OperatingMode.DEMO, OperatingMode.LIVE}:
            raise TradingDisabled("paper/backtest real-data client is strictly read-only")
        account = self._ensure_sync()
        prior = self._cache.get(command.idempotency_key)
        if prior:
            if prior[0] != command.request_hash:
                raise RiskViolation("idempotency key reused with a different command")
            _LOG.info(
                "Broker cached decision status=%s key=%s", prior[1].status.value, command.idempotency_key[:12]
            )
            return prior[1]
        if len(self._cache) >= 100000:
            raise TradingDisabled("local intent cache is full; pause and reconcile before restart")
        terminal = self._api.terminal_info()
        if (
            not account.trade_allowed
            or not account.trade_expert
            or not terminal.trade_allowed
            or getattr(terminal, "tradeapi_disabled", True)
        ):
            raise TradingDisabled("terminal/account external Python trading permission is disabled")
        positions = self._positions_sync()
        risk, margin, reward = ZERO, ZERO, ZERO
        request: dict[str, Any]
        if command.operation == Operation.OPEN:
            order = command.order
            if order is None:
                raise InvalidOrder("missing market order")
            meta = self._meta_sync(order.symbol)
            tick = self._tick_sync(meta)
            validate_entry(order, meta, tick, self.settings, self.clock)
            if len(positions) >= self.settings.max_open_positions or any(
                position.symbol == order.symbol for position in positions
            ):
                raise RiskViolation("position count/same-symbol averaging is forbidden")
            if any(
                position.magic != self.settings.mt5_magic_number or position.sl <= ZERO
                for position in positions
            ):
                raise RiskViolation("foreign or unprotected exposure blocks new risk")
            pending = self._api.orders_get()
            if pending is None:
                raise self._error("orders_get")
            if pending:
                raise RiskViolation("pending broker orders must be reconciled before new entries")
            entry = tick.entry(order.side)
            risk, margin, reward = self._entry_valuation(order, account, meta, tick)
            comment = (
                "rb:" + order.idempotency_key[:10] + ":" + re.sub(r"[^A-Za-z0-9_-]", "_", order.strategy)[:16]
            )
            request = {
                "action": 1,
                "symbol": order.symbol,
                "volume": float(order.volume),
                "type": 0 if order.side == Side.BUY else 1,
                "price": float(entry),
                "sl": float(order.sl),
                "tp": float(order.tp),
                "deviation": self.settings.max_slippage_points,
                "magic": self.settings.mt5_magic_number,
                "comment": comment[:31],
                "type_time": 0,
                "type_filling": self._filling(meta),
            }
        else:
            matching = [
                position
                for position in positions
                if position.ticket == command.ticket and position.identifier == command.position_identifier
            ]
            if len(matching) != 1 or matching[0].magic != self.settings.mt5_magic_number:
                raise TradingDisabled("position ownership/ticket/identifier is not verified")
            position = matching[0]
            meta, tick = self._meta_sync(position.symbol), None
            tick = self._tick_sync(meta)
            tick.fresh(self.clock, self.settings.max_tick_age_seconds)
            if command.operation == Operation.CLOSE:
                validate_volume(position.volume, meta)
                request = {
                    "action": 1,
                    "position": position.ticket,
                    "symbol": position.symbol,
                    "volume": float(position.volume),
                    "type": 1 if position.side == Side.BUY else 0,
                    "price": float(tick.exit(position.side)),
                    "deviation": self.settings.max_slippage_points,
                    "magic": self.settings.mt5_magic_number,
                    "comment": "rb:close:" + command.idempotency_key[:12],
                    "type_time": 0,
                    "type_filling": self._filling(meta),
                }
            else:
                sl, tp = protection_prices(command, position, meta, tick, self.settings)
                request = {
                    "action": 6,
                    "position": position.ticket,
                    "symbol": position.symbol,
                    "sl": float(sl),
                    "tp": float(tp),
                    "magic": self.settings.mt5_magic_number,
                }
        snapshot = self._snapshot_sync(
            account, meta, tick, positions, risk, margin, reward, entry=command.operation == Operation.OPEN
        )
        grant = self._authority.authorize(command, snapshot)
        try:
            self._validate_grant(command, snapshot, grant)
            if command.operation == Operation.PROTECT:
                position = matching[0]
                tp = decimal_value(request["tp"])
                if position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO:
                    if (
                        grant.allow_tp_extension is not True
                        or not isinstance(grant.original_tp, Decimal)
                        or not grant.original_tp.is_finite()
                        or grant.original_tp <= ZERO
                    ):
                        raise TradingDisabled("TP extension lacks original-target/news/volatility authority")
                    if (
                        abs(tp - position.entry_price)
                        > abs(grant.original_tp - position.entry_price) * self.settings.tp_extension_factor
                    ):
                        raise RiskViolation("TP extension exceeds its original-target cap")
            check = self._api.order_check(request)
            if check is not None and int(check.retcode) == 0:
                fresh_account = self._account_sync()
                fresh_positions = self._positions_sync()

                def exposure(items):
                    return tuple(
                        sorted(
                            (p.ticket, p.identifier, p.symbol, p.side.value, p.volume, p.sl, p.tp, p.magic)
                            for p in items
                        )
                    )

                if exposure(fresh_positions) != exposure(positions):
                    raise RiskViolation("exposure changed during authorization; replan, no send")
                fresh_meta = self._meta_sync(meta.name)
                fresh_tick = self._tick_sync(fresh_meta)
                fresh_tick.fresh(self.clock, self.settings.max_tick_age_seconds)
                fresh_terminal = self._api.terminal_info()
                if (
                    fresh_terminal is None
                    or not fresh_terminal.connected
                    or not fresh_terminal.trade_allowed
                    or getattr(fresh_terminal, "tradeapi_disabled", True)
                    or not fresh_account.trade_allowed
                    or not fresh_account.trade_expert
                ):
                    raise TradingDisabled("trading permission/connection changed before send")
                if command.operation == Operation.OPEN:
                    pending = self._api.orders_get()
                    if pending is None or pending:
                        raise RiskViolation("pending-order snapshot changed before send")
                    validate_entry(order, fresh_meta, fresh_tick, self.settings, self.clock)
                    risk, margin, reward = self._entry_valuation(order, fresh_account, fresh_meta, fresh_tick)
                    request["price"] = float(fresh_tick.entry(order.side))
                elif command.operation == Operation.PROTECT:
                    protection_prices(command, matching[0], fresh_meta, fresh_tick, self.settings)
                else:
                    request["price"] = float(fresh_tick.exit(matching[0].side))
                fresh_snapshot = self._snapshot_sync(
                    fresh_account,
                    fresh_meta,
                    fresh_tick,
                    fresh_positions,
                    risk,
                    margin,
                    reward,
                    entry=command.operation == Operation.OPEN,
                )
                self._validate_grant(command, fresh_snapshot, grant)
                before_send = getattr(self._authority, "before_send", None)
                if before_send is not None:
                    before_send(command, fresh_snapshot, grant)
                if self._quarantined:
                    raise UncertainExecution("write cancelled before send by quarantine latch")
        except Exception:
            aborted = ExecutionResult(
                command.operation,
                command.idempotency_key,
                account.key,
                ResultStatus.REJECTED,
                reason="preflight_aborted_no_send",
            )
            self._finish_write(command, aborted)
            raise
        if check is None:
            result = ExecutionResult(
                command.operation,
                command.idempotency_key,
                account.key,
                ResultStatus.REJECTED,
                reason="order_check_unavailable_no_send",
            )
        elif int(check.retcode) != 0:
            result = ExecutionResult(
                command.operation,
                command.idempotency_key,
                account.key,
                ResultStatus.REJECTED,
                retcode=int(check.retcode),
                reason="order_check_rejected",
            )
        else:
            try:
                raw = self._api.order_send(request)  # EXACTLY ONCE per attempt; never auto-retry.
                result = self._acknowledgement(command, account, raw)
                self._account_sync()  # Detect a manual account switch during the call.
            except Exception:
                result = ExecutionResult(
                    command.operation,
                    command.idempotency_key,
                    account.key,
                    ResultStatus.UNKNOWN,
                    reason="send_exception_or_identity_change",
                )
        return self._finish_write(command, result)

    def _finish_write(self, command: BrokerCommand, result: ExecutionResult) -> ExecutionResult:
        self._cache[command.idempotency_key] = (command.request_hash, result)
        try:
            self._authority.on_result(command, result)
        except Exception:
            self._quarantine("broker result could not be persisted")
            raise UncertainExecution("broker may have executed but durable acknowledgement failed") from None
        if result.status in {ResultStatus.UNKNOWN, ResultStatus.PARTIAL, ResultStatus.ACCEPTED}:
            self._quarantine("unknown/unsettled broker acknowledgement")
            try:
                self._authority.on_uncertain(command, result.reason)
            except Exception:
                _LOG.error("Unsettled-execution observer failed; quarantine remains latched")
        _LOG.info(
            "Broker decision op=%s status=%s key=%s code=%s",
            command.operation.value,
            result.status.value,
            command.idempotency_key[:12],
            result.retcode,
        )
        return result

    async def _write(self, command: BrokerCommand) -> ExecutionResult:
        _LOG.info("Broker attempt op=%s key=%s", command.operation.value, command.idempotency_key[:12])
        try:
            return await self._call(lambda: self._write_sync(command), command=command)
        except BrokerError as exc:
            _LOG.warning(
                "Broker veto/error op=%s key=%s kind=%s",
                command.operation.value,
                command.idempotency_key[:12],
                type(exc).__name__,
            )
            raise

    async def open_market_buy(self, order: MarketOrder) -> ExecutionResult:
        if order.side != Side.BUY:
            raise InvalidOrder("BUY API received a SELL order")
        return await self._write(
            BrokerCommand(Operation.OPEN, order.idempotency_key, order.created_at, order=order)
        )

    async def open_market_sell(self, order: MarketOrder) -> ExecutionResult:
        if order.side != Side.SELL:
            raise InvalidOrder("SELL API received a BUY order")
        return await self._write(
            BrokerCommand(Operation.OPEN, order.idempotency_key, order.created_at, order=order)
        )

    async def close_position(
        self, ticket: int, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult:
        return await self._write(
            BrokerCommand(
                Operation.CLOSE,
                idempotency_key,
                self.clock.now(),
                ticket=ticket,
                position_identifier=position_identifier,
            )
        )

    async def modify_sl(
        self, ticket: int, sl: Decimal, *, position_identifier: int, idempotency_key: str
    ) -> ExecutionResult:
        return await self._write(
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
        return await self._write(
            BrokerCommand(
                Operation.PROTECT,
                idempotency_key,
                self.clock.now(),
                ticket=ticket,
                position_identifier=position_identifier,
                tp=tp,
            )
        )
