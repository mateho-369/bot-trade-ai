# MT5 AI ReflexBot — Part 4: full implementation

## Deliverables

- Cumulative project: `MT5_AI_ReflexBot_Parts_01_04.zip` (Parts 1–4, 0.2.0).
- This guide reproduces every new Part 4 source/test/diagnostic file in full.
- Latest complete settings and `.env.example` are included at the end.
- Part 1–3's original ordered source remains in `docs/PARTS_01_03.md`; the
  cumulative repository contains coherent current versions, not mixed snapshots.
- Native writes remain DENY-ALL by default. This is not an autonomous/live-ready
  trading bot. No real MT5 connection or orders were performed.

# Part 4 — MT5 client, MockMT5, paper execution and order calculator

**Release 0.2.0; Parts 1–4.** This is real library code and runnable diagnostics,
not the assembled trading bot. Native execution defaults to **DENY ALL**, including
broker-demo opens, closes and modifications. There is no `main.py run` yet.

The complete source is reproduced in `docs/PART_04.md`; the repository files in
the cumulative archive are authoritative. `docs/PARTS_01_03.md` is the previously
delivered snapshot, not the current configuration. Use the updated `.env.example`
and `core/settings.py` from this release when starting from scratch.

## Implemented modules

| File | Responsibility |
|---|---|
| `trading/types.py` | Decimal/aware-UTC DTOs; side/account/source/status enums; clocks; async contracts and safe exceptions |
| `trading/price_rules.py` | Actual tick grids, downward lots, stop/freeze rules, freshness, deviation and monotonically improved SL |
| `trading/currency.py` | Verified explicit USD conversion routes with fresh bid/ask and signed asset/liability treatment |
| `trading/candles.py` | Finalized OHLC validation, close timestamps and no forming/future rows |
| `trading/order_calculator.py` | Native profit/margin sizing, commission/swap estimates, bounded target solver, cost-aware order plans |
| `trading/client_helpers.py` | Balance/equity, open-position and calculation convenience methods |
| `trading/authorization.py` | Trusted internal write-authority contract plus working `DenyAllWrites` default |
| `trading/mt5_client.py` | Windows SDK adapter; one worker/SDK lease; account pinning; guarded write paths and uncertainty quarantine |
| `trading/mock_mt5.py` | Deterministic synthetic market/contract examples and a simulated broker |
| `trading/simulation.py` | Shared bid/ask simulation, costs, gap-aware exits, swap, counters, snapshots and idempotency |
| `trading/paper_mt5.py` | Simulated orders with a separate market-data source; never calls source write methods |
| `trading/symbol_manager.py` | Explicit aliases and supported metadata; unavailable symbols disabled; unknown news exposure flagged |
| `scripts/smoke_mock.py` | Isolated deterministic synthetic buy/TP/duplicate/deal-ledger check |
| `scripts/check_mt5_readonly.py` | Explicit Windows-only local connection/metadata diagnostic; forces paper/read-only |

Python protocols describe contracts, not fake implementations. Mock, paper and
native adapters implement their own actual operations. The future risk authority,
trailing/position manager and runtime are deliberately not supplied as permissive
stubs.

## Configuration additions

```dotenv
MT5_API_TIMEOUT_SECONDS=45
ORDER_MAX_AGE_SECONDS=30
PAPER_INITIAL_BALANCE=1000
PAPER_SLIPPAGE_POINTS=2
MOCK_LEVERAGE=100
```

- API deadline applies to each serialized native operation, not the whole
  strategy calculation. Initial SDK connection timeout remains 10,000 ms.
- Order age defaults to 30 seconds. Fresh tick tolerance defaults to 10 seconds;
  quote clocks ahead by more than two seconds are rejected.
- Paper capital is in configured **account currency**, not automatically USD.
  It never inherits the actual terminal account balance.
- Paper slippage cannot exceed the configured nominal slippage-point budget.
  Slippage estimates are rounded adversely to a whole executable native tick.
  Ten points can be less than one tick on some instruments.
- Mock leverage/contracts are illustrative, **not** your broker's specifications.
  Real-data paper margin/profit comes from the real source's native calculators.
- `.env.example` remains paper/mock/paused, with empty broker/Telegram secrets.

Use actual broker aliases, e.g. `SYMBOL_ALIASES_JSON={"XAUUSD":"XAUUSDm"}`.
Logical keys must be enabled symbols. No suffix/name guessing is performed.
Spread limits are in **broker points**, not pips or USD. Synthetic BTC/ETH spreads
can exceed the conservative default 35 points; those setups are correctly vetoed
unless the owner explicitly configures a reviewed per-symbol limit.

## Async client contract

All adapters expose:

```text
initialize(), shutdown()
get_account_info(), get_balance(), get_equity()
get_symbols(), select_symbol(), get_symbol_info(), get_tick(), get_candles()
get_positions(), get_open_positions(), get_deals(), get_closed_deals()
calculate_profit(), calculate_margin()
calculate_lot_size(), calculate_profit_usd()
calculate_price_distance_from_usd_profit()
open_market_buy(order), open_market_sell(order)
close_position(ticket, *, position_identifier, idempotency_key)
modify_sl(ticket, sl, *, position_identifier, idempotency_key)
modify_tp(ticket, tp, *, position_identifier, idempotency_key)
```

Use `async with ...` for cleanup. Financial write inputs must be Decimal and
order timestamps timezone-aware. SDK floats are converted explicitly at the
boundary. Do not pass JSON floats straight to the broker.

### Runnable synthetic example

This example can run on Linux/macOS/Windows; it starts no terminal or server.
Its direct adapter calls are developer simulation, not approved runtime trading.

```python
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from core.security import sha256_json
from core.settings import Settings
from trading.mock_mt5 import MockMT5Client
from trading.order_calculator import OrderCalculator
from trading.types import ManualClock, Side


async def example():
    settings = Settings(_env_file=None)  # Keep paper/mock; no real credentials.
    clock = ManualClock(datetime(2026, 10, 2, 8, tzinfo=timezone.utc))
    async with MockMT5Client(settings, clock=clock) as broker:
        plan = await OrderCalculator(broker, settings).plan_market_order(
            "XAUUSD",
            Side.BUY,
            Decimal("2608"),
            strategy="developer_simulation",
            idempotency_key=sha256_json({"example": "one-intent"}),
        )
        if plan is None:
            return  # Below-minimum lot: skip, never increase risk.
        result = await broker.open_market_buy(plan.order)
        assert await broker.open_market_buy(plan.order) == result
        print(await broker.get_positions())


asyncio.run(example())
```

For a fully environment-isolated check instead, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_mock
```

```bash
.venv/bin/python -m scripts.smoke_mock
```

Actual synthetic smoke result: 300 finalized bars, one 0.02-lot artificial entry,
nominal risk 4.9400 account units within a 5.0 budget, target 5.43400 USD,
one closing leg, zero remaining positions and balance reconciliation. The
artificial net gain was 5.76 USD. **This engineered trade is NOT a backtest,
performance evidence, a profit forecast, or permission to promote/trade.**

## Native safety behavior

### Windows/read-only inspection

After locally configuring the terminal path, aliases and account currency:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_mt5_readonly --env-file .env
```

This may launch MT5 or log in using locally supplied credentials. It forces
paper flags and uses `DenyAllWrites`, even if `.env` attempts live execution.
It reads identity/currency/account values/metadata/fresh spreads, never calls
`order_send`, and does not print login/password/server credentials. Perform
this on your own Windows machine; no native connection was attempted here.
On non-Windows it returns a structured `ConnectionUnavailable` and exit code 2.
To keep MT5 minimized, start/minimize the terminal interactively first; VBS/logon
launchers are delivered in Part 10, not fabricated in this installment.

For real-data paper in trusted application code:

```python
# settings must be PAPER_TRADING=true, LIVE_TRADING=false, MT5_BACKEND=real.
from trading.mt5_client import MT5Client
from trading.paper_mt5 import PaperMT5Client

async with PaperMT5Client(MT5Client(settings), settings) as paper:
    account = await paper.get_account_info()  # Shadow capital; NOT real balance.
    bars = await paper.get_candles("XAUUSD", "M5", 300)  # Use exact broker alias.
```

The paper wrapper invokes only market-data/valuation operations on its source.
Its source provenance must ALSO be checked: `source_kind == PAPER` alone does
not make synthetic/test data eligible for stage promotion. Inspect
`market_source_kind` and independently verified dataset/artifact provenance.

### Serialized worker and identity

- SDK import/initialize/login/history/math/writes/shutdown are on one dedicated
  worker, behind an RLock and asyncio serialization lock. No native call runs on
  the event loop; a blocked worker does not block Telegram/FastAPI coroutines.
- One client leases a given SDK module per process. Keep one client on one event
  loop. This is NOT the future cross-process single-instance runtime lock.
- Actual login/server identity is pinned. Account currency must match the
  declared currency. Demo writes require the terminal's actual DEMO account;
  live writes require REAL. A switch/mismatch latches write quarantine.
- Native instances reject simulated clocks. Fake SDK injection requires an
  explicit test marker and is tagged TEST_SDK; never inject the native module
  or a test authority into application runtime code. The marker is a misuse
  guard, not a security sandbox for malicious Python code.
- Read/connect retries are bounded. `order_send` is NEVER automatically retried.
- Only terminal/account external-Python trading permissions, owner-enabled
  symbols, supported native SL/TP, legal lots/grids and fresh prices can pass
  the low-level checks. Foreign/unprotected/pending/same-symbol exposure vetoes
  a new entry. Metadata, permissions, exposure, quote, native risk/margin and
  short permit are checked again before send.

### Authority and idempotency

The future trusted `WriteAuthority.authorize` must commit a unique durable
intent/reserved risk and validate pause/kill/daily/drawdown/aggregate/confidence/
news/staging/owner gates **before** returning its account/request/config-bound,
short-lived grant. It cannot recursively call the async client from its worker.
Authority exceptions must use safe messages. Result/uncertainty callbacks must
be atomic and thread-safe, with a fresh DB session per operation.

After authority preparation, a preflight error is reported through the result callback as
`REJECTED / preflight_aborted_no_send`; empty/rejected `order_check` similarly
reports a definitely-unsent outcome. Durable storage of these callbacks is Part 5. An empty/unknown/send-exception response,
a caller timeout/cancellation, or acknowledgement-persistence failure halts
writes. Partial/accepted responses also quarantine pending reconciliation.
Late completion still invokes the result callback. A timeout observer MUST NOT
downgrade a later filled/rejected/reconciled result.

A local cache rejects changed-payload key reuse and avoids duplicate sends in
one instance; it does not replace durable cross-restart idempotency. Maintenance
hashes omit their new wrapper timestamp so same-key retries remain stable;
OPEN retains the immutable order's creation timestamp. Native order ticket,
deal ticket, position ticket and stable position identifier are distinct.
Never guess a position identifier from an order ticket. A FILLED acknowledgement
without its position identifier still requires reconciliation by the later
execution engine. Comments are supplementary, not authoritative matching proof.

Quarantine does not clear on reconnect. Read-only reconciliation plus a fresh,
paused, durably-authorized runtime is required. Native threads cannot safely be
killed: shutdown may time out, and the lease stays held until the worker really
drains. Python can remain alive waiting for a stuck native thread; the later
external watchdog must terminate/restart a hung process without automatically
resubmitting an uncertain order. Retain server-side SL/TP and use a dedicated
bot account; no atomic transaction can prevent unrelated manual/EAs from racing
broker state between the final check and broker execution.

## Sizing / USD targets / protection

1. Risk capital is `max(0, min(balance, equity - credit))`.
2. Compute loss at adverse tick-rounded entry and adverse stop exit using native
   profit valuation, plus configured round-turn commission.
3. Probe at broker minimum lot; cap by risk, available margin, volume maximum
   and directional limit. Round DOWN by actual step; zero means skip.
4. Revalue the actual candidate. Up to 32 downward adjustments handle non-linear
   native valuation; no assumption that minimum-lot risk scales perfectly.
5. Solve TP with bounded integer-tick bracketing/binary search. BUY/SELL and
   off-grid weighted entry prices are supported; executable targets remain on
   the real tick grid. Verify the smallest sufficient tick and net reward/risk.
6. Dynamic targets default to at least 1.1 net R. A too-small fixed $5 objective
   is rejected rather than increasing lot risk. Fees/swap can be included;
   slippage can be explicitly included for the exit.

USD/account conversion requires an explicit `ACCOUNT_TO_USD_SYMBOLS_JSON`, actual
pair metadata and a fresh quote. Positive proceeds use asset bid/ask conversion;
negative costs/losses use adverse funding rates. Crosses use two verified USD
legs. No silent USD=USDC=USC equivalence. Native profit is authoritative; mock
profit uses illustrative linear contract math. Unknown mock account-currency
tick-value snapshots are zero, never quote-currency numbers mislabeled as cash.

SL modifications never loosen/remove SL, and cannot silently remove TP. Stop
and freeze levels use executable exits (BUY bid / SELL ask). Native TP extensions
need the explicit flag, original immutable target and a separate authority grant,
and remain bounded by the original-target factor. Simulator extensions stay
blocked until Part 5 connects the volatility/news/position supervisor; no dummy
approval is generated. Triggered 30/60/90 profit-lock and ATR management arrive
in **Part 5**, not this calculator installment.

## Simulation and history limitations

- All simulated entry/exit fills use adverse tick-rounded slippage and executable
  bid/ask. SL gaps fill at the worse market quote; TP gaps do not gift a favorable
  windfall. Stops are checked before targets. No guaranteed nominal-loss cap.
- Commission is split equally between opening/closing legs. Swap is a configurable
  continuous per-lot/per-day estimate, not broker-specific triple-swap/rollover.
  Simulations do not model partial fills, stop-out/liquidation, financing calendars
  or every broker portfolio-margin rule. Calibrate costs from actual demo history.
- Equity includes marked gross PnL and accrued swap; balance includes realized
  costs/legs. Sum ALL position deal legs for complete net PnL; one closing deal
  does not contain entry commission. Native cash/credit/charge legs stay separate.
- Fresh ticks are mandatory for shadow exits; old quotes cannot fabricate stops.
  Stale held exposure blocks new risk. Basic position/count/risk/margin/daily/
  drawdown rules are implemented; the durable full owner/risk engine is Part 5.
- Daily count/loss resets use configured timezone and last observed carried
  equity before the first new-day mark. Drawdown latch persists across days.
  This is sampled equity, not a reconstruction of an unobserved midnight mark.
- Snapshots include positions, margins, original TP, both deal legs, intent cache,
  counters/latches and configuration/data-source/account identity. Restore rejects
  wrong scope and incoherent/future records. Export/restore is implemented;
  **automatic atomic persistence is not wired yet**. Do not run paper unattended
  or reset paper capital to manufacture passing metrics.
- Synthetic candles are a function of absolute bar index and fixed fixture seed,
  not future ticks. Real history excludes the newest possibly-forming bar and
  uses a conservative next-opening/nominal close bound for session/DST changes.
  During long market gaps, availability may be deliberately delayed rather than
  inventing a prematurely closed bar. Higher-frame joins must use CLOSE time.

## Validation and next boundary

Executed: **199 pytest tests passed** (60 foundation + 139 Part 4), Ruff
format/lint, compileall, pip consistency, foundation CLI and synthetic smoke.
Native tests use marked fake SDKs only, including leases, cancellation, shutdown,
late callbacks, pinned identity, grants, order check and acknowledgement classes.
No Windows SDK/terminal, real broker/demo/live orders, complete risk/trailing
runtime, strategy backtest, AI/news providers or Telegram integration was tested.
See `docs/VALIDATION.md` for the reproducible validation scope.

**Next: Part 5 — durable risk/execution authority, risk engine, trailing engine
and position manager.** Deployment/learning/news/UI/backtester remain in the
requested subsequent parts. This installment is not live-ready or certified.


---

## Actual cumulative archive tree

```text
ACTUAL ARCHIVE CONTENTS — Parts 1–4, release 0.2.0

This is the actual file list, not the planned Parts 5–11 target tree.
Runtime credentials/database/logs/data/models/dependencies/caches are excluded.

mt5_ai_reflex_bot/
  .env.example
  .gitignore
  README.md
  ai/.gitkeep
  app/.gitkeep
  backtesting/.gitkeep
  config.py
  core/__init__.py
  core/database.py
  core/logging_setup.py
  core/models.py
  core/security.py
  core/settings.py
  data/backups/.gitkeep
  data/candles/.gitkeep
  data/logs/.gitkeep
  data/models/.gitkeep
  data/news/.gitkeep
  docs/ARCHITECTURE.md
  docs/CURRENT_TREE.txt
  docs/PARTS_01_03.md
  docs/PART_04.md
  docs/PART_04_NOTES.md
  docs/RELEASE_04_MANIFEST.json
  docs/TARGET_TREE.txt
  docs/VALIDATION.md
  main.py
  miniapp/api/.gitkeep
  miniapp/static/.gitkeep
  news/.gitkeep
  pyproject.toml
  requirements.linux.lock.txt
  requirements.txt
  scripts/.gitkeep
  scripts/__init__.py
  scripts/check_mt5_readonly.py
  scripts/smoke_mock.py
  strategy/.gitkeep
  telegram_bot/.gitkeep
  tests/__init__.py
  tests/conftest.py
  tests/fake_mt5_sdk.py
  tests/test_database.py
  tests/test_foundation_cli.py
  tests/test_mt5_client.py
  tests/test_order_calculator.py
  tests/test_security_logging.py
  tests/test_settings.py
  tests/test_simulated_broker.py
  tests/test_trading_contracts.py
  tests/test_trading_diagnostics.py
  trading/.gitkeep
  trading/__init__.py
  trading/authorization.py
  trading/candles.py
  trading/client_helpers.py
  trading/currency.py
  trading/mock_mt5.py
  trading/mt5_client.py
  trading/order_calculator.py
  trading/paper_mt5.py
  trading/price_rules.py
  trading/simulation.py
  trading/symbol_manager.py
  trading/types.py
```

## Full Part 4 source

The following are literal current files. Test-only SDK/authorities live under
`tests/` and must NEVER be used for real trading. Libraries have no import-time
terminal/network/order side effects. Contracts are protocols, not permissive stubs.

### `trading/__init__.py`

```python
"""Trading adapters. Importing this package never connects to a terminal."""
```

### `trading/types.py`

```python
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
```

### `trading/price_rules.py`

```python
"""Pure broker-grid rules; never round a lot UP to meet the broker minimum."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from core.settings import Settings
from trading.types import (
    ZERO,
    BrokerCommand,
    Clock,
    InvalidOrder,
    MarketOrder,
    Position,
    RiskViolation,
    Side,
    SymbolInfo,
    Tick,
    aware_utc,
)


def snap(price: Decimal, step: Decimal, *, up: bool) -> Decimal:
    if step <= ZERO or not price.is_finite():
        raise InvalidOrder("invalid price grid")
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    return (price / step).to_integral_value(rounding=rounding) * step


def adverse_price(price: Decimal, side: Side, symbol: SymbolInfo, points: int, *, entry: bool) -> Decimal:
    """Round estimated slippage against us to an ACTUAL executable tick.

    A ten-point allowance need not be a whole native tick (e.g. 0.10 vs 0.25).
    This is a nominal estimate, not a guarantee against market gaps.
    """
    if not isinstance(points, int) or isinstance(points, bool) or points < 0:
        raise InvalidOrder("invalid point-based slippage allowance")
    direction = side.sign if entry else -side.sign
    return snap(price + direction * points * symbol.point, symbol.tick_size, up=direction > ZERO)


def floor_volume(volume: Decimal, symbol: SymbolInfo) -> Decimal:
    volume = min(volume, symbol.volume_max)
    result = snap(volume, symbol.volume_step, up=False)
    return ZERO if result < symbol.volume_min else result


def validate_volume(volume: Decimal, symbol: SymbolInfo) -> None:
    if not volume.is_finite() or not symbol.volume_min <= volume <= symbol.volume_max:
        raise InvalidOrder("volume violates min/max")
    if volume % symbol.volume_step != ZERO:
        raise InvalidOrder("volume violates broker step")
    if symbol.volume_limit > ZERO and volume > symbol.volume_limit:
        raise InvalidOrder("volume exceeds the broker directional limit")


def validate_price(price: Decimal, symbol: SymbolInfo) -> None:
    if not price.is_finite() or price <= ZERO or price % symbol.tick_size != ZERO:
        raise InvalidOrder("price is not on the positive broker tick grid")


def validate_levels(
    symbol: SymbolInfo, tick: Tick, side: Side, sl: Decimal, tp: Decimal, *, modifying: bool = False
) -> None:
    distance = max(symbol.stops_level, symbol.freeze_level if modifying else 0) * symbol.point
    market = tick.exit(side)
    for price in (sl, tp):
        if price > ZERO:
            validate_price(price, symbol)
    if sl > ZERO and (side.sign * (market - sl) <= ZERO or side.sign * (market - sl) < distance):
        raise InvalidOrder("SL violates stop/freeze distance")
    if tp > ZERO and (side.sign * (tp - market) <= ZERO or side.sign * (tp - market) < distance):
        raise InvalidOrder("TP violates stop/freeze distance")
    if modifying and symbol.freeze_level:
        for price in (sl, tp):
            if price > ZERO and abs(price - market) <= symbol.freeze_level * symbol.point:
                raise InvalidOrder("SL/TP is inside the freeze zone")


def validate_entry(
    order: MarketOrder, symbol: SymbolInfo, tick: Tick, settings: Settings, clock: Clock
) -> None:
    enabled = {settings.symbol_aliases.get(name, name) for name in settings.symbols}
    if order.symbol not in enabled:
        raise InvalidOrder("symbol is not enabled by the owner")
    age = (aware_utc(clock.now()) - aware_utc(order.created_at)).total_seconds()
    if age < -2 or age > settings.order_max_age_seconds:
        raise InvalidOrder("order is stale or from the future")
    tick.fresh(clock, settings.max_tick_age_seconds)
    validate_volume(order.volume, symbol)
    validate_price(order.reference_price, symbol)
    validate_levels(symbol, tick, order.side, order.sl, order.tp)
    if not symbol.order_mode & 1 or not symbol.order_mode & 16 or not symbol.order_mode & 32:
        raise InvalidOrder("broker does not advertise native SL and TP support")
    allowed = (
        symbol.trade_mode == 4
        or (symbol.trade_mode == 1 and order.side == Side.BUY)
        or (symbol.trade_mode == 2 and order.side == Side.SELL)
    )
    if not allowed:
        raise InvalidOrder("symbol trading mode forbids this entry")
    limit = settings.symbol_spread_limits.get(order.symbol, settings.max_spread_points)
    # Overrides may be keyed by logical name or native broker name.
    for logical in settings.symbols:
        if settings.symbol_aliases.get(logical, logical) == order.symbol:
            limit = settings.symbol_spread_limits.get(logical, limit)
    if tick.spread_points(symbol) > limit:
        raise RiskViolation("spread exceeds the owner limit")
    if abs(tick.entry(order.side) - order.reference_price) > settings.max_slippage_points * symbol.point:
        raise RiskViolation("price moved outside the approved reference/deviation window")


def protection_prices(
    command: BrokerCommand, position: Position, symbol: SymbolInfo, tick: Tick, settings: Settings
) -> tuple[Decimal, Decimal]:
    sl = position.sl if command.sl is None else command.sl
    tp = position.tp if command.tp is None else command.tp
    if sl <= ZERO or tp < ZERO or (command.tp is not None and tp <= ZERO):
        raise InvalidOrder("cannot remove protection")
    if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
        raise RiskViolation("SL modification would loosen protection")
    if symbol.freeze_level:
        for existing in (position.sl, position.tp):
            if (
                existing > ZERO
                and abs(existing - tick.exit(position.side)) <= symbol.freeze_level * symbol.point
            ):
                raise InvalidOrder("existing protection is frozen")
    validate_levels(symbol, tick, position.side, sl, tp, modifying=True)
    extending = tp > ZERO and position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO
    if extending and not settings.allow_tp_extension:
        raise RiskViolation("TP extension is disabled")
    return sl, tp
```

### `trading/currency.py`

```python
"""Bid/ask-aware cash conversion. No silent cent-account/USDC/USD relabeling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.settings import Settings
from trading.types import ZERO, Clock, MarketData, RiskViolation, SymbolInfo, Tick


@dataclass(frozen=True, slots=True)
class ConversionQuote:
    symbol: SymbolInfo
    tick: Tick

    def convert(self, amount: Decimal, source: str, target: str) -> Decimal:
        if source == target:
            return amount
        base, quote = self.symbol.currency_base, self.symbol.currency_profit
        if (source, target) == (base, quote):
            # Asset proceeds sell at bid; funding a liability buys at ask.
            return amount * (self.tick.bid if amount >= ZERO else self.tick.ask)
        if (source, target) == (quote, base):
            return amount / (self.tick.ask if amount >= ZERO else self.tick.bid)
        raise RiskViolation("configured conversion symbol has the wrong currency pair")


class CurrencyConverter:
    def __init__(self, market: MarketData, settings: Settings, clock: Clock | None = None) -> None:
        self.market, self.settings = market, settings
        self.clock = clock or market.clock

    async def convert(self, amount: Decimal, source: str, target: str) -> Decimal:
        if not isinstance(amount, Decimal) or not amount.is_finite():
            raise RiskViolation("invalid cash conversion amount")
        if source == target:
            return amount
        if "USD" not in (source, target):
            # Two verified legs; conservatively includes each conversion spread.
            dollars = await self.convert(amount, source, "USD")
            return await self.convert(dollars, "USD", target)
        currency = target if source == "USD" else source
        name = self.settings.account_to_usd_symbols.get(currency)
        if not name:
            raise RiskViolation("non-USD currency requires an explicit conversion-symbol mapping")
        meta = await self.market.get_symbol_info(name)
        tick = await self.market.get_tick(name)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        return ConversionQuote(meta, tick).convert(amount, source, target)

    async def to_usd(self, amount: Decimal) -> Decimal:
        account = await self.market.get_account_info()
        return await self.convert(amount, account.currency, "USD")

    async def usd_to_account(self, amount: Decimal) -> Decimal:
        account = await self.market.get_account_info()
        return await self.convert(amount, "USD", account.currency)
```

### `trading/candles.py`

```python
"""Validate finalized OHLC data, including higher-timeframe close timestamps."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from core.settings import TIMEFRAME_MINUTES
from trading.types import BrokerError, aware_utc


def validated_candles(frame: pd.DataFrame, timeframe: str, as_of: datetime) -> pd.DataFrame:
    if timeframe not in TIMEFRAME_MINUTES:
        raise BrokerError("unsupported timeframe")
    required = {"time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"}
    if not required.issubset(frame.columns) or frame.empty:
        raise BrokerError("missing candle fields/history")
    result = frame.copy(deep=True)
    parsed = pd.to_datetime(result["time"])
    if parsed.dt.tz is None:
        raise BrokerError("candle timestamps must already be timezone-aware")
    result["time"] = parsed.dt.tz_convert("UTC")
    nominal = result["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    if "close_time" in result:
        closes = pd.to_datetime(result["close_time"])
        if closes.dt.tz is None or closes.isna().any():
            raise BrokerError("broker close timestamps must be aware and complete")
        result["close_time"] = closes.dt.tz_convert("UTC")
        if (result["close_time"] < nominal).any():
            raise BrokerError("broker close timestamps cannot precede the nominal bound")
    else:
        result["close_time"] = nominal
    result = result.loc[result["close_time"] <= pd.Timestamp(aware_utc(as_of))].copy()
    if result.empty or result["time"].duplicated().any() or not result["time"].is_monotonic_increasing:
        raise BrokerError("candle timestamps are empty/duplicated/out of order")
    columns = ["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    values = result[columns].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise BrokerError("candle prices must be finite and positive")
    if (result[["tick_volume", "spread", "real_volume"]] < 0).any().any():
        raise BrokerError("negative candle volume/spread")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any() or (
        result["low"] > result[["open", "close", "high"]].min(axis=1)
    ).any():
        raise BrokerError("invalid OHLC range")
    return result.reset_index(drop=True)
```

### `trading/authorization.py`

```python
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
    AccountInfo,
    BrokerCommand,
    ExecutionResult,
    Position,
    SymbolInfo,
    Tick,
    TradingDisabled,
)


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
```

### `trading/order_calculator.py`

```python
"""Native broker valuation, downward sizing, bounded tick-grid target solving."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from core.security import sha256_json
from core.settings import Settings
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price, floor_volume, snap, validate_entry, validate_volume
from trading.types import ZERO, Clock, InvalidOrder, MarketData, MarketOrder, RiskViolation, Side


@dataclass(frozen=True, slots=True)
class LotCalculation:
    volume: Decimal
    risk_budget_account: Decimal
    worst_loss_account: Decimal
    margin_account: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class OrderPlan:
    order: MarketOrder
    risk_budget_account: Decimal
    worst_loss_account: Decimal
    margin_account: Decimal
    target_profit_usd: Decimal
    expected_net_profit_usd: Decimal
    reward_risk: Decimal


class OrderCalculator:
    def __init__(self, broker: MarketData, settings: Settings, clock: Clock | None = None) -> None:
        self.broker, self.settings = broker, settings
        self.clock = clock or broker.clock
        self.currency = CurrencyConverter(broker, settings, self.clock)

    def costs_usd(self, volume: Decimal, held_days: Decimal = ZERO) -> Decimal:
        if not isinstance(volume, Decimal) or volume <= ZERO or held_days < ZERO or not held_days.is_finite():
            raise InvalidOrder("invalid volume/holding-cost inputs")
        return volume * (
            self.settings.commission_round_turn_usd_per_lot
            + self.settings.estimated_swap_usd_per_lot_per_day * held_days
        )

    async def costs_account(self, volume: Decimal, held_days: Decimal = ZERO) -> Decimal:
        # A cost is a liability: use the adverse conversion side.
        return -(await self.currency.usd_to_account(-self.costs_usd(volume, held_days)))

    async def net_profit_account(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        exit_price: Decimal,
        *,
        include_costs: bool = True,
        held_days: Decimal = ZERO,
    ) -> Decimal:
        result = await self.broker.calculate_profit(symbol, side, volume, entry, exit_price)
        if not result.is_finite():
            raise RiskViolation("native profit calculator returned non-finite data")
        return result - (await self.costs_account(volume, held_days) if include_costs else ZERO)

    async def calculate_profit_usd(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        exit_price: Decimal,
        *,
        include_costs: bool = False,
        held_days: Decimal = ZERO,
    ) -> Decimal:
        result = await self.net_profit_account(
            symbol, side, volume, entry, exit_price, include_costs=include_costs, held_days=held_days
        )
        return await self.currency.to_usd(result)

    async def calculate_lot_size(
        self, symbol: str, side: Side, entry: Decimal, sl: Decimal, *, risk_percent: Decimal | None = None
    ) -> LotCalculation:
        meta = await self.broker.get_symbol_info(symbol)
        account = await self.broker.get_account_info()
        percent = self.settings.effective_risk_percent if risk_percent is None else risk_percent
        if (
            not isinstance(percent, Decimal)
            or not percent.is_finite()
            or not ZERO < percent <= self.settings.effective_risk_percent
        ):
            raise RiskViolation("requested risk exceeds the configured effective cap")
        if side.sign * (entry - sl) <= ZERO:
            raise InvalidOrder("SL must be on the loss side")
        budget = account.risk_capital * percent / Decimal("100")
        available_margin = max(
            ZERO,
            min(
                account.margin_free,
                account.risk_capital * self.settings.max_margin_usage_percent / Decimal("100")
                - account.margin,
            ),
        )
        worst_entry = adverse_price(entry, side, meta, self.settings.max_slippage_points, entry=True)
        worst_exit = adverse_price(sl, side, meta, self.settings.max_slippage_points, entry=False)
        if min(worst_entry, worst_exit) <= ZERO:
            raise InvalidOrder("slippage bound would cross nonpositive prices")
        probe = meta.volume_min
        loss = -(await self.net_profit_account(symbol, side, probe, worst_entry, worst_exit))
        margin = await self.broker.calculate_margin(symbol, side, probe, worst_entry)
        if loss <= ZERO or margin < ZERO or not margin.is_finite():
            raise RiskViolation("invalid native loss/margin valuation")
        limit = min(meta.volume_max, probe * budget / loss)
        if margin > ZERO:
            limit = min(limit, probe * available_margin / margin)
        if meta.volume_limit > ZERO:
            limit = min(limit, meta.volume_limit)
        volume = floor_volume(limit, meta)
        if volume == ZERO:
            return LotCalculation(ZERO, budget, ZERO, ZERO, "broker minimum lot exceeds risk/margin budget")
        # Revalue the actual candidate. Do not assume a tiered native calculator
        # scales linearly. Bounded downward adjustment never rounds volume up.
        for _ in range(32):
            final_loss = -(await self.net_profit_account(symbol, side, volume, worst_entry, worst_exit))
            final_margin = await self.broker.calculate_margin(symbol, side, volume, worst_entry)
            if final_loss > ZERO and ZERO <= final_margin <= available_margin and final_loss <= budget:
                return LotCalculation(
                    volume, budget, final_loss, final_margin, "within nominal risk and margin caps"
                )
            if final_loss <= ZERO or final_margin < ZERO or not final_margin.is_finite():
                raise RiskViolation("invalid native candidate valuation")
            ratio = min(
                Decimal("1"),
                budget / final_loss,
                available_margin / final_margin if final_margin else Decimal("1"),
            )
            smaller = floor_volume(min(volume - meta.volume_step, volume * ratio), meta)
            if smaller == ZERO:
                break
            volume = smaller
        return LotCalculation(ZERO, budget, ZERO, ZERO, "no legal lot within native risk/margin caps")

    async def price_for_profit_usd(
        self,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry: Decimal,
        target_usd: Decimal,
        *,
        include_costs: bool = False,
        held_days: Decimal = ZERO,
        exit_slippage_points: int = 0,
    ) -> Decimal:
        if not isinstance(target_usd, Decimal) or not target_usd.is_finite() or target_usd < ZERO:
            raise InvalidOrder("target must be a finite nonnegative USD amount")
        meta = await self.broker.get_symbol_info(symbol)
        validate_volume(volume, meta)
        if not isinstance(entry, Decimal) or not entry.is_finite() or entry <= ZERO:
            raise InvalidOrder("invalid entry price")
        # A partial-fill VWAP entry can be OFF-grid; only executable targets
        # must be on the grid. Keep the true entry for native valuation.
        anchor = snap(entry, meta.tick_size, up=side == Side.BUY)
        costs = await self.costs_account(volume, held_days) if include_costs else ZERO

        async def evaluate(ticks: int) -> Decimal:
            price = anchor + side.sign * meta.tick_size * ticks
            executable = adverse_price(price, side, meta, exit_slippage_points, entry=False)
            if min(price, executable) <= ZERO:
                raise RiskViolation("target solver reached a nonpositive price")
            value = await self.broker.calculate_profit(symbol, side, volume, entry, executable)
            return await self.currency.to_usd(value - costs)

        low, high = 0, 1
        if await evaluate(low) >= target_usd:
            return anchor
        # Integer tick search: bounded, BUY/SELL-aware, smallest sufficient target.
        # SELL prices have a physical positive lower bound. Clamp the bracket
        # rather than rejecting a reachable target when doubling crosses zero.
        ceiling = int(anchor / meta.tick_size) - 1 if side == Side.SELL else 2**25
        if ceiling < 1:
            raise RiskViolation("target has no positive executable price")
        high = min(high, ceiling)
        for _ in range(25):
            if await evaluate(high) >= target_usd:
                break
            if high == ceiling:
                raise RiskViolation("target exceeds the bounded positive-price range")
            low, high = high, min(high * 2, ceiling)
        else:
            raise RiskViolation("USD target cannot be bracketed within the solver bound")
        while high - low > 1:
            middle = (low + high) // 2
            if await evaluate(middle) >= target_usd:
                high = middle
            else:
                low = middle
        if await evaluate(high) < target_usd:
            raise RiskViolation("valuation changed during target solving; replan with fresh quotes")
        return anchor + side.sign * meta.tick_size * high

    async def calculate_price_distance_from_usd_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, target_usd: Decimal, **kwargs
    ) -> Decimal:
        price = await self.price_for_profit_usd(symbol, side, volume, entry, target_usd, **kwargs)
        return abs(price - entry)

    async def plan_market_order(
        self,
        symbol: str,
        side: Side,
        sl: Decimal,
        *,
        strategy: str,
        idempotency_key: str | None = None,
        risk_percent: Decimal | None = None,
        target_usd: Decimal | None = None,
        created_at: datetime | None = None,
    ) -> OrderPlan | None:
        meta = await self.broker.get_symbol_info(symbol)
        tick = await self.broker.get_tick(symbol)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        entry = tick.entry(side)
        sl = snap(sl, meta.tick_size, up=side == Side.SELL)
        sized = await self.calculate_lot_size(symbol, side, entry, sl, risk_percent=risk_percent)
        if sized.volume == ZERO:
            return None
        goal = self.settings.target_profit_usd_per_trade if target_usd is None else target_usd
        if not isinstance(goal, Decimal) or not goal.is_finite() or goal <= ZERO:
            raise InvalidOrder("profit objective must be a positive Decimal")
        if self.settings.use_dynamic_target:
            loss_usd = -(await self.currency.to_usd(-sized.worst_loss_account))
            goal = max(goal, loss_usd * self.settings.target_r_multiple)
        worst_entry = adverse_price(entry, side, meta, self.settings.max_slippage_points, entry=True)
        tp = await self.price_for_profit_usd(
            symbol,
            side,
            sized.volume,
            worst_entry,
            goal,
            include_costs=True,
            exit_slippage_points=self.settings.max_slippage_points,
        )
        net_account = await self.net_profit_account(
            symbol,
            side,
            sized.volume,
            worst_entry,
            adverse_price(tp, side, meta, self.settings.max_slippage_points, entry=False),
        )
        reward_risk = net_account / sized.worst_loss_account
        if reward_risk < self.settings.min_net_reward_risk:
            raise RiskViolation("profit objective fails the net reward/risk floor; skip, do not enlarge risk")
        order = MarketOrder(
            symbol,
            side,
            sized.volume,
            entry,
            sl,
            tp,
            idempotency_key or sha256_json({"nonce": str(uuid4()), "symbol": symbol}),
            created_at or self.clock.now(),
            strategy,
        )
        validate_entry(order, meta, tick, self.settings, self.clock)
        net_usd = await self.currency.to_usd(net_account)
        return OrderPlan(
            order,
            sized.risk_budget_account,
            sized.worst_loss_account,
            sized.margin_account,
            goal,
            net_usd,
            reward_risk,
        )
```

### `trading/client_helpers.py`

```python
"""Shared async conveniences; account currency and USD methods are distinct."""

from decimal import Decimal

from trading.types import Side


class ClientCalculations:
    async def get_balance(self) -> Decimal:
        return (await self.get_account_info()).balance

    async def get_equity(self) -> Decimal:
        return (await self.get_account_info()).equity

    async def get_open_positions(self):
        return await self.get_positions()

    def _calculator(self):
        from trading.order_calculator import OrderCalculator

        return OrderCalculator(self, self.settings, self.clock)

    async def calculate_lot_size(self, symbol: str, side: Side, entry: Decimal, sl: Decimal, **kwargs):
        return await self._calculator().calculate_lot_size(symbol, side, entry, sl, **kwargs)

    async def calculate_profit_usd(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal, **kwargs
    ) -> Decimal:
        return await self._calculator().calculate_profit_usd(
            symbol, side, volume, entry, exit_price, **kwargs
        )

    async def calculate_price_distance_from_usd_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, target_usd: Decimal, **kwargs
    ) -> Decimal:
        return await self._calculator().calculate_price_distance_from_usd_profit(
            symbol, side, volume, entry, target_usd, **kwargs
        )
```

### `trading/mt5_client.py`

```python
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, TypeVar

import pandas as pd

from core.security import sanitize_text, secret_values
from core.settings import TIMEFRAME_MINUTES, OperatingMode, Settings
from trading.authorization import BrokerSnapshot, DenyAllWrites, WriteAuthority, WriteGrant
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
        self._cache: dict[str, tuple[str, ExecutionResult]] = {}
        self._secrets = secret_values(settings)

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "source": self.source_kind.value,
                "connected": self._connected,
                "writes_quarantined": self._quarantined,
                "reason": self._quarantine_reason,
                "last_success": self._last_read.isoformat() if self._last_read else None,
                "write_authority": type(self._authority).__name__,
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
            future = loop.run_in_executor(self._worker, self._guarded, function)

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
        if (
            grant.account_key != snapshot.account.key
            or grant.request_hash != command.request_hash
            or grant.config_hash != self.settings.safety_fingerprint()
        ):
            raise TradingDisabled("authorization does not bind account/request/configuration")
        expiry = aware_utc(grant.expires_at)
        now = self.clock.now()
        if not now < expiry <= now + timedelta(seconds=self.settings.order_max_age_seconds):
            raise TradingDisabled("authorization is expired or exceeds the short permit lifetime")
        for amount in (grant.max_volume, grant.max_loss_account, grant.max_margin_account):
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < ZERO:
                raise TradingDisabled("invalid authorization financial bound")
        if command.operation == Operation.OPEN:
            if grant.entry_gates_verified is not True or (
                self.settings.mode == OperatingMode.LIVE and grant.owner_live_confirmed is not True
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
        snapshot = BrokerSnapshot(account, meta, tick, positions, risk, margin, reward, self.clock.now())
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
                self._validate_grant(
                    command,
                    BrokerSnapshot(
                        fresh_account,
                        fresh_meta,
                        fresh_tick,
                        fresh_positions,
                        risk,
                        margin,
                        reward,
                        self.clock.now(),
                    ),
                    grant,
                )
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
```

### `trading/simulation.py`

```python
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
from trading.client_helpers import ClientCalculations
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price, protection_prices, validate_entry
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

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            await self.market.initialize()
            data_account = await self.market.get_account_info()
            if data_account.currency != self.settings.account_currency:
                raise RiskViolation("paper and data valuation currencies must match explicitly")
            self._data_account_key = data_account.key
            if self._restored_state is not None:
                self._restore(self._restored_state)
            self._initialized = True

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

    def _remember(self, command: BrokerCommand, result: ExecutionResult) -> ExecutionResult:
        self._cache[command.idempotency_key] = (command.request_hash, result)
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
                or self._entries_today >= self.settings.max_daily_trades
            ):
                raise RiskViolation("simulation daily/drawdown/count/stale-exposure gate blocks entry")
            if len(self._positions) >= self.settings.max_open_positions or any(
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
            budget = account.risk_capital * self.settings.effective_risk_percent / Decimal("100")
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
            self._remember(command, result)
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
            result = await self._close_at(position, tick.exit(position.side), "manual")
            return self._remember(command, replace(result, idempotency_key=idempotency_key))

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
            if position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO:
                # Part 5 supplies the volatility/news authority; do not fabricate it.
                raise TradingDisabled("simulation TP extension requires the later supervisor integration")
            status = ResultStatus.NO_CHANGE if (sl, tp) == (position.sl, position.tp) else ResultStatus.FILLED
            self._positions[position.identifier] = replace(position, sl=sl, tp=tp)
            return self._remember(
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

    async def export_state(self) -> dict[str, Any]:
        import json

        async with self._lock:
            self._ready()
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
```

### `trading/mock_mt5.py`

```python
"""Deterministic synthetic market + simulated execution; no MT5 import/network."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from core.settings import TIMEFRAME_MINUTES, Settings
from trading.candles import validated_candles
from trading.currency import CurrencyConverter
from trading.simulation import SimulatedBroker
from trading.types import (
    ZERO,
    AccountInfo,
    AccountKind,
    BrokerError,
    Clock,
    Side,
    SourceKind,
    SymbolInfo,
    SystemClock,
    Tick,
    UnsupportedSymbol,
    aware_utc,
)

D = Decimal


def synthetic_catalogue() -> tuple[dict[str, SymbolInfo], dict[str, tuple[Decimal, Decimal]]]:
    # EXAMPLES ONLY. Never use this table as broker contract specifications.
    definitions = {
        "XAUUSD": ("0.01", "100", "2610.00", "0.20", "XAU", "USD", "0.01", 2),
        "BTCUSD": ("0.01", "1", "60000.00", "20.00", "BTC", "USD", "0.01", 2),
        "ETHUSD": ("0.01", "1", "3000.00", "2.00", "ETH", "USD", "0.01", 2),
        "EURUSD": ("0.00001", "100000", "1.10000", "0.00012", "EUR", "USD", "0.01", 5),
        "GBPUSD": ("0.00001", "100000", "1.25000", "0.00012", "GBP", "USD", "0.01", 5),
        "USDJPY": ("0.001", "100000", "150.000", "0.020", "USD", "JPY", "0.01", 3),
        "US30": ("0.1", "1", "40000.0", "1.0", "USD", "USD", "0.1", 1),
        "NAS100": ("0.1", "1", "18000.0", "1.0", "USD", "USD", "0.1", 1),
    }
    symbols, quotes = {}, {}
    for name, (point, contract, bid, spread, base, quote, minimum, digits) in definitions.items():
        symbols[name] = SymbolInfo(
            name,
            D(point),
            D(point),
            D(point) * D(contract),
            D(point) * D(contract),
            D(contract),
            D(minimum),
            D("100"),
            D(minimum),
            digits,
            base,
            quote,
            stops_level=10,
        )
        quotes[name] = (D(bid), D(bid) + D(spread))
    return symbols, quotes


class MockMarketData:
    source_kind = SourceKind.SYNTHETIC

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        symbols: dict[str, SymbolInfo] | None = None,
        quotes: dict[str, tuple[Decimal, Decimal]] | None = None,
    ) -> None:
        self.settings, self.clock = settings, clock or SystemClock()
        self._symbols, self._quotes = synthetic_catalogue()
        self._symbols.update(symbols or {})
        self._quotes.update(quotes or {})
        for logical, alias in settings.symbol_aliases.items():
            if logical in self._symbols and alias not in self._symbols:
                self._symbols[alias] = replace(self._symbols[logical], name=alias)
                self._quotes[alias] = self._quotes[logical]
        self._overrides: dict[str, Tick] = {}
        self._initialized = False
        self._converter = CurrencyConverter(self, settings)

    async def initialize(self) -> None:
        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False

    def _ready(self) -> None:
        if not self._initialized:
            raise BrokerError("mock market is not initialized")

    async def get_account_info(self) -> AccountInfo:
        self._ready()
        balance = self.settings.paper_initial_balance
        return AccountInfo(
            0,
            "synthetic-valuation-only",
            self.settings.account_currency,
            AccountKind.SIMULATED,
            SourceKind.SYNTHETIC,
            balance,
            balance,
            ZERO,
            balance,
            leverage=self.settings.mock_leverage,
        )

    async def get_symbols(self) -> tuple[str, ...]:
        self._ready()
        return tuple(self._symbols)

    async def select_symbol(self, symbol: str) -> SymbolInfo:
        return await self.get_symbol_info(symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        self._ready()
        if symbol not in self._symbols:
            raise UnsupportedSymbol("unknown synthetic symbol; supply explicit test metadata/quotes")
        meta = self._symbols[symbol]
        if meta.currency_profit != self.settings.account_currency:
            # Unknown snapshot tick values are zero, never mislabeled quote
            # currency as account currency. Native-style profit valuation uses
            # the explicit FX conversion path instead of tick-value shortcuts.
            return replace(meta, tick_value_profit=ZERO, tick_value_loss=ZERO)
        return meta

    async def get_tick(self, symbol: str) -> Tick:
        await self.get_symbol_info(symbol)
        if symbol in self._overrides:
            return self._overrides[symbol]
        if symbol not in self._quotes:
            raise UnsupportedSymbol("synthetic symbol has no configured quote")
        bid, ask = self._quotes[symbol]
        return Tick(symbol, bid, ask, self.clock.now())

    async def set_tick(
        self, symbol: str, bid: Decimal, ask: Decimal, *, timestamp: datetime | None = None
    ) -> None:
        meta = await self.get_symbol_info(symbol)
        if bid % meta.tick_size or ask % meta.tick_size:
            raise BrokerError("synthetic quote is off the tick grid")
        self._overrides[symbol] = Tick(symbol, bid, ask, timestamp or self.clock.now())

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 300, *, as_of: datetime | None = None
    ) -> pd.DataFrame:
        meta = await self.get_symbol_info(symbol)
        if timeframe not in TIMEFRAME_MINUTES or not 1 <= count <= 10000:
            raise BrokerError("invalid synthetic timeframe/count")
        cutoff = aware_utc(as_of or self.clock.now())
        if cutoff > self.clock.now():
            raise BrokerError("synthetic data cannot peek beyond its clock")
        seconds = TIMEFRAME_MINUTES[timeframe] * 60
        last_close = int(cutoff.timestamp()) // seconds * seconds
        anchor = self._quotes[symbol][0]
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 1000
        quantum = Decimal(1).scaleb(-meta.digits)
        amplitude = min(anchor / D("100"), meta.point * 80)

        # Prices are a function of ABSOLUTE bar index, never current/future quote.
        def price(index: int) -> Decimal:
            value = anchor + amplitude * D(
                str(math.sin((index + seed) / 7) + 0.3 * math.cos((index + seed) / 19))
            )
            return value.quantize(quantum)

        rows = []
        for offset in range(count):
            start = last_close - (count - offset) * seconds
            index = start // seconds
            opening, closing = price(index), price(index + 1)
            rows.append(
                {
                    "time": datetime.fromtimestamp(start, cutoff.tzinfo),
                    "open": float(opening),
                    "high": float(max(opening, closing) + meta.point * 5),
                    "low": float(min(opening, closing) - meta.point * 5),
                    "close": float(closing),
                    "tick_volume": 100 + (index + seed) % 100,
                    "spread": int((self._quotes[symbol][1] - anchor) / meta.point),
                    "real_volume": 0,
                }
            )
        return validated_candles(pd.DataFrame(rows), timeframe, cutoff)

    async def calculate_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal
    ) -> Decimal:
        meta = await self.get_symbol_info(symbol)
        if min(volume, entry, exit_price) <= ZERO:
            raise BrokerError("invalid mock profit inputs")
        gross_quote = side.sign * (exit_price - entry) * meta.contract_size * volume
        return await self._converter.convert(
            gross_quote, meta.currency_profit, self.settings.account_currency
        )

    async def calculate_margin(self, symbol: str, side: Side, volume: Decimal, entry: Decimal) -> Decimal:
        meta = await self.get_symbol_info(symbol)
        if min(volume, entry) <= ZERO:
            raise BrokerError("invalid mock margin inputs")
        amount = entry * meta.contract_size * volume / self.settings.mock_leverage
        return -(await self._converter.convert(-amount, meta.currency_profit, self.settings.account_currency))


class MockMT5Client(SimulatedBroker):
    """Same async broker API, purely synthetic. Cannot be instantiated as live/demo."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        symbols: dict[str, SymbolInfo] | None = None,
        quotes: dict[str, tuple[Decimal, Decimal]] | None = None,
        ledger_id: str = "mock-main",
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        if settings.mt5_backend != "mock":
            raise BrokerError("MockMT5Client requires MT5_BACKEND=mock")
        market = MockMarketData(settings, clock=clock, symbols=symbols, quotes=quotes)
        super().__init__(
            market,
            settings,
            source_kind=SourceKind.SYNTHETIC,
            ledger_id=ledger_id,
            restored_state=restored_state,
        )

    async def set_tick(
        self, symbol: str, bid: Decimal, ask: Decimal, *, timestamp: datetime | None = None
    ) -> None:
        async with self._lock:
            await self.market.set_tick(symbol, bid, ask, timestamp=timestamp)
            await self._refresh()
```

### `trading/paper_mt5.py`

```python
"""Real market data + ONLY simulated orders. The source is never used to trade."""

from typing import Any

from core.settings import OperatingMode, Settings
from trading.simulation import SimulatedBroker
from trading.types import MarketData, SourceKind, TradingDisabled


class PaperMT5Client(SimulatedBroker):
    def __init__(
        self,
        market: MarketData,
        settings: Settings,
        *,
        ledger_id: str = "paper-main",
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        if settings.mode != OperatingMode.PAPER:
            raise TradingDisabled("PaperMT5Client requires PAPER_TRADING=true")
        super().__init__(
            market, settings, source_kind=SourceKind.PAPER, ledger_id=ledger_id, restored_state=restored_state
        )

    @property
    def market_source_kind(self) -> SourceKind:
        return self.market.source_kind
```

### `trading/symbol_manager.py`

```python
"""Resolve explicit logical/native names and validate available broker metadata."""

from dataclasses import dataclass

from core.settings import Settings
from trading.types import MarketData, SymbolInfo, UnsupportedSymbol


@dataclass(frozen=True, slots=True)
class ManagedSymbol:
    logical: str
    native: str
    info: SymbolInfo
    news_currencies: tuple[str, ...]
    spread_limit_points: int


class SymbolManager:
    def __init__(self, market: MarketData, settings: Settings) -> None:
        self.market, self.settings = market, settings
        self._symbols: dict[str, ManagedSymbol] = {}
        self._errors: dict[str, str] = {}

    async def initialize(self) -> None:
        found: dict[str, ManagedSymbol] = {}
        errors: dict[str, str] = {}
        native_names = set()
        for logical in self.settings.symbols:
            native = self.settings.symbol_aliases.get(logical, logical)
            try:
                info = await self.market.select_symbol(native)
                if info.name != native or native in native_names:
                    raise UnsupportedSymbol("broker alias mismatch or duplicate actual symbol")
                native_names.add(native)
                found[logical] = ManagedSymbol(
                    logical,
                    native,
                    info,
                    self.settings.symbol_news_currencies.get(logical, ()),
                    self.settings.symbol_spread_limits.get(
                        logical,
                        self.settings.symbol_spread_limits.get(native, self.settings.max_spread_points),
                    ),
                )
            except UnsupportedSymbol:
                errors[logical] = "unavailable/invalid broker metadata; configure an explicit alias"
        self._symbols, self._errors = found, errors

    def enabled(self) -> tuple[ManagedSymbol, ...]:
        return tuple(self._symbols.values())

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def resolve(self, logical: str) -> ManagedSymbol:
        if logical not in self._symbols:
            raise UnsupportedSymbol("logical symbol is disabled/unavailable")
        return self._symbols[logical]

    def news_exposure_known(self, logical: str) -> bool:
        # Unknown exposure is never guessed safe. NewsFilter in Part 8 vetoes it.
        return bool(self.resolve(logical).news_currencies)
```

### `scripts/__init__.py`

```python
"""Explicitly-invoked diagnostics. Importing this package starts nothing."""
```

### `scripts/smoke_mock.py`

```python
"""One deterministic SYNTHETIC trade, not a strategy evaluation/backtest.

Run: python -m scripts.smoke_mock
Ignores host environment and .env credentials. Imports no native MT5 module.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.security import sha256_json
from core.settings import Settings
from trading.mock_mt5 import MockMT5Client
from trading.order_calculator import OrderCalculator
from trading.symbol_manager import SymbolManager
from trading.types import ManualClock, Side


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # NEVER consume credentials/mode/risk from the host.


async def run() -> dict:
    settings = SmokeSettings(_env_file=None, symbols=("XAUUSD",))
    clock = ManualClock(datetime(2026, 10, 2, 8, tzinfo=timezone.utc))
    async with MockMT5Client(settings, clock=clock) as broker:
        symbols = SymbolManager(broker, settings)
        await symbols.initialize()
        managed = symbols.resolve("XAUUSD")
        candles = await broker.get_candles(managed.native, "M5", 300)
        plan = await OrderCalculator(broker, settings).plan_market_order(
            managed.native,
            Side.BUY,
            Decimal("2608"),
            strategy="synthetic_smoke",
            idempotency_key=sha256_json({"smoke": 1}),
        )
        if plan is None:
            raise RuntimeError("default smoke risk budget unexpectedly cannot fit minimum lot")
        opened = await broker.open_market_buy(plan.order)
        assert await broker.open_market_buy(plan.order) == opened
        positions = await broker.get_positions()
        assert len(positions) == 1
        clock.advance(timedelta(seconds=1))
        await broker.set_tick(managed.native, plan.order.tp, plan.order.tp + Decimal("0.20"))
        deals = await broker.get_deals(datetime(2026, 10, 2, 8, tzinfo=timezone.utc))
        account = await broker.get_account_info()
        assert len(deals) == 2 and await broker.get_positions() == ()
        net = sum(deal.net for deal in deals)
        assert account.balance == settings.paper_initial_balance + net
        return {
            "source": broker.source_kind.value,
            "simulated_only": True,
            "eligible_stage_evidence": False,
            "native_sdk_imported": "MetaTrader5" in sys.modules,
            "real_orders_sent": 0,
            "mode": settings.mode.value,
            "finalized_candles": len(candles),
            "duplicate_open_sent_once": len(deals) == 2,
            "volume": str(plan.order.volume),
            "nominal_risk_account": str(plan.worst_loss_account),
            "risk_budget_account": str(plan.risk_budget_account),
            "target_usd": str(plan.target_profit_usd),
            "simulated_net_usd": str(net),
            "ending_simulated_balance": str(account.balance),
            "deals": len(deals),
            "positions": 0,
            "warning": "Artificial quotes; this is NOT profitability evidence or permission to trade.",
        }


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### `scripts/check_mt5_readonly.py`

```python
"""Windows-only explicit connection diagnostic. NEVER sends a broker order.

Run: python -m scripts.check_mt5_readonly --env-file .env
Force PAPER flags even if the supplied environment attempts demo/live execution.
May launch terminal64.exe/login with local .env credentials; invoke only locally.
"""

import argparse
import asyncio
import json

from pydantic import ValidationError

from core.settings import Settings
from trading.mt5_client import MT5Client
from trading.symbol_manager import SymbolManager
from trading.types import BrokerError


async def inspect(settings: Settings) -> dict:
    async with MT5Client(settings) as client:  # Default DENY-ALL write authority.
        account = await client.get_account_info()
        symbols = SymbolManager(client, settings)
        await symbols.initialize()
        metadata = []
        for item in symbols.enabled():
            tick = await client.get_tick(item.native)
            tick.fresh(client.clock, settings.max_tick_age_seconds)
            metadata.append(
                {
                    "logical": item.logical,
                    "native": item.native,
                    "tick_size": str(item.info.tick_size),
                    "point": str(item.info.point),
                    "volume_min": str(item.info.volume_min),
                    "volume_step": str(item.info.volume_step),
                    "spread_points": str(tick.spread_points(item.info)),
                    "spread_limit_points": item.spread_limit_points,
                    "news_exposure_configured": symbols.news_exposure_known(item.logical),
                }
            )
        return {
            "diagnostic": "read-only",
            "real_orders_sent": 0,
            "mode_forced": "paper",
            "account_key": account.key,
            "actual_account_kind": account.kind.value,
            "currency": account.currency,
            "balance": str(account.balance),
            "equity": str(account.equity),
            "symbols": metadata,
            "disabled_symbols": symbols.errors(),
            "health": client.health(),
            "warning": "Connection success is NOT stage approval, news coverage, or live readiness.",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    try:
        settings = Settings(
            _env_file=args.env_file,
            mt5_backend="real",
            demo_mode=True,
            paper_trading=True,
            live_trading=False,
            backtest_mode=False,
        )
        report = asyncio.run(inspect(settings))
    except ValidationError:
        print(
            json.dumps(
                {
                    "error": "invalid_configuration",
                    "detail": "Run main.py check-config; raw values suppressed.",
                }
            )
        )
        return 2
    except BrokerError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc), "real_orders_sent": 0}))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### `tests/__init__.py`

```python
"""Isolated tests and TEST-ONLY broker SDK/authority fixtures. Not runtime code."""
```

### `tests/fake_mt5_sdk.py`

```python
"""TEST ONLY: no real MT5 import, network or owner/trading authorization."""

from datetime import timedelta
from decimal import Decimal
from threading import Event, RLock, get_ident
from types import SimpleNamespace as NS

from trading.authorization import WriteGrant
from trading.types import ResultStatus

D = Decimal


class FakeSDK:
    __reflexbot_test_sdk__ = True
    TIMEFRAME_M5 = 5
    TIMEFRAME_H1 = 60

    def __init__(self, clock, magic=20260217):
        self.clock, self.magic = clock, magic
        self.connected = False
        self.tradeapi_disabled = False
        self.permission = True
        self.account = NS(
            login=123,
            server="TEST-server",
            currency="USD",
            trade_mode=0,
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            margin_free=1000.0,
            credit=0.0,
            leverage=100,
            trade_allowed=True,
            trade_expert=True,
        )
        self.metadata = NS(
            name="XAUUSD",
            point=0.01,
            trade_tick_size=0.01,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
            trade_contract_size=100.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            digits=2,
            currency_base="XAU",
            currency_profit="USD",
            trade_mode=4,
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=3,
            trade_exemode=2,
            order_mode=127,
            volume_limit=0.0,
            visible=True,
            trade_calc_mode=0,
        )
        self.bid, self.ask = 2610.0, 2610.2
        self.tick_time = None
        self.positions, self.pending, self.deals = (), (), ()
        self.ack = NS(retcode=10009, order=777, deal=888, volume=0.01, price=2610.2)
        self.check = NS(retcode=0)
        self.requests, self.calls = [], []
        self.init_count, self.shutdown_count = 0, 0
        self.send_entered, self.send_release, self.shutdown_done = Event(), Event(), Event()
        self.block_send = False
        self.send_error = None
        self.send_hook = None
        self.check_hook = None
        self.account_hook = None
        self._lock = RLock()

    def called(self, name):
        with self._lock:
            self.calls.append((name, get_ident()))

    def initialize(self, *args, **kwargs):
        self.called("initialize")
        self.init_count += 1
        self.connected = True
        return True

    def login(self, *args, **kwargs):
        self.called("login")
        return True

    def shutdown(self):
        self.called("shutdown")
        self.shutdown_count += 1
        self.connected = False
        self.shutdown_done.set()

    def last_error(self):
        return (-1, "TEST-secret-should-never-be-logged")

    def terminal_info(self):
        self.called("terminal_info")
        return NS(
            connected=self.connected,
            trade_allowed=self.permission,
            tradeapi_disabled=self.tradeapi_disabled,
            path="C:/Program Files/MetaTrader 5",
        )

    def account_info(self):
        self.called("account_info")
        if self.account_hook:
            self.account_hook()
        return self.account

    def symbol_info(self, name):
        self.called("symbol_info")
        return self.metadata if name == self.metadata.name else None

    def symbol_select(self, name, value):
        self.called("symbol_select")
        return name == self.metadata.name

    def symbols_get(self):
        self.called("symbols_get")
        return (self.metadata,)

    def symbol_info_tick(self, name):
        self.called("symbol_info_tick")
        instant = self.tick_time or self.clock.now()
        return NS(
            bid=self.bid,
            ask=self.ask,
            time=int(instant.timestamp()),
            time_msc=int(instant.timestamp() * 1000),
        )

    def positions_get(self):
        self.called("positions_get")
        return self.positions

    def orders_get(self):
        self.called("orders_get")
        return self.pending

    def history_deals_get(self, since, until):
        self.called("history_deals_get")
        return self.deals

    def order_calc_profit(self, kind, symbol, volume, entry, exit_price):
        self.called("order_calc_profit")
        return float((D(str(exit_price)) - D(str(entry))) * D(str(volume)) * 100 * (1 if kind == 0 else -1))

    def order_calc_margin(self, kind, symbol, volume, entry):
        self.called("order_calc_margin")
        return float(D(str(entry)) * D(str(volume)))

    def order_check(self, request):
        self.called("order_check")
        if self.check_hook:
            self.check_hook()
        return self.check

    def order_send(self, request):
        self.called("order_send")
        self.requests.append(dict(request))
        self.send_entered.set()
        if self.block_send and not self.send_release.wait(3):
            raise RuntimeError("test must release the fake worker")
        if self.send_hook:
            self.send_hook()
        if self.send_error:
            raise self.send_error
        return self.ack

    def rates(self, count, cutoff, timeframe):
        # Include an unfinished last bar; adapter must remove it.
        minutes = timeframe
        end = int(cutoff.timestamp()) // (minutes * 60) * minutes * 60
        return [
            dict(
                time=end - (count - 1 - n) * minutes * 60,
                open=2610.0,
                high=2611.0,
                low=2609.0,
                close=2610.5,
                tick_volume=100,
                spread=20,
                real_volume=0,
            )
            for n in range(count)
        ]

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.called("copy_rates_from_pos")
        return self.rates(count, self.clock.now() - timedelta(minutes=start * timeframe), timeframe)

    def copy_rates_from(self, symbol, timeframe, cutoff, count):
        self.called("copy_rates_from")
        return self.rates(count, cutoff, timeframe)

    def owned_position(self, **overrides):
        values = dict(
            ticket=42,
            identifier=50042,
            symbol="XAUUSD",
            type=0,
            volume=0.01,
            price_open=2610.2,
            sl=2608.0,
            tp=2616.0,
            time=int(self.clock.now().timestamp()),
            magic=self.magic,
            profit=0.0,
            swap=0.0,
            comment="test",
        )
        values.update(overrides)
        return NS(**values)


class FixtureAuthority:
    """Only fake clients may use this; deliberately not a production authority."""

    def __init__(self, settings, clock):
        self.settings, self.clock = settings, clock
        self.authorized, self.results, self.uncertain = [], [], []
        self.latest = None
        self.result_event = Event()
        self._lock = RLock()
        self.mutate_grant = None
        self.after_authorize = None
        self.fail_result = False

    def authorize(self, command, snapshot):
        assert snapshot.account.source.value == "test_sdk", "NEVER use this fixture on a real account"
        with self._lock:
            self.authorized.append((command, snapshot))
        permit = WriteGrant(
            snapshot.account.key,
            command.request_hash,
            self.settings.safety_fingerprint(),
            self.clock.now() + timedelta(seconds=20),
            command.order.volume if command.order else D("0"),
            snapshot.worst_loss_account,
            snapshot.required_margin_account,
            entry_gates_verified=True,
            owned_position_verified=True,
        )
        if self.after_authorize:
            self.after_authorize()
        return self.mutate_grant(permit) if self.mutate_grant else permit

    def on_result(self, command, result):
        if self.fail_result:
            raise RuntimeError("TEST-persistence-failure-secret")
        with self._lock:
            self.results.append(result)
            self.latest = result.status
            self.result_event.set()

    def on_uncertain(self, command, reason):
        with self._lock:
            self.uncertain.append(reason)
            # A timeout observer MUST NOT downgrade a later terminal result.
            if self.latest not in {ResultStatus.FILLED, ResultStatus.REJECTED}:
                self.latest = ResultStatus.UNKNOWN
```

### `tests/test_trading_contracts.py`

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from core.security import sha256_json
from core.settings import Settings
from trading.candles import validated_candles
from trading.mock_mt5 import synthetic_catalogue
from trading.price_rules import adverse_price, floor_volume, protection_prices, snap, validate_entry
from trading.types import (
    AccountInfo,
    AccountKind,
    BrokerCommand,
    BrokerError,
    InvalidOrder,
    ManualClock,
    MarketOrder,
    Operation,
    Position,
    RiskViolation,
    Side,
    SourceKind,
    StaleData,
    Tick,
    decimal_value,
)

D = Decimal
NOW = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
KEY = sha256_json({"fixture": "contracts"})


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


def order(side=Side.BUY, **kwargs):
    values = dict(
        symbol="XAUUSD",
        side=side,
        volume=D("0.01"),
        reference_price=D("2610.20") if side == Side.BUY else D("2610"),
        sl=D("2608") if side == Side.BUY else D("2612"),
        tp=D("2616") if side == Side.BUY else D("2604"),
        idempotency_key=KEY,
        created_at=NOW,
    )
    values.update(kwargs)
    return MarketOrder(**values)


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", "-Infinity", None, "not-a-number"])
def test_sdk_decimal_boundary_rejects_bad_numbers(bad):
    with pytest.raises(BrokerError):
        decimal_value(bad)


def test_risk_capital_does_not_spend_credit_or_floating_wins():
    account = AccountInfo(
        1,
        "private",
        "USD",
        AccountKind.DEMO,
        SourceKind.TEST_SDK,
        D("1000"),
        D("1500"),
        D("0"),
        D("1500"),
        credit=D("600"),
    )
    assert account.risk_capital == D("900")
    assert replace(account, equity=D("1600")).risk_capital == D("1000")
    assert "private" not in repr(account)


@pytest.mark.parametrize(
    "field,value",
    [
        ("volume", 0.01),
        ("sl", D("0")),
        ("tp", D("NaN")),
        ("idempotency_key", "retry-1"),
        ("side", "buy"),
        ("created_at", NOW.replace(tzinfo=None)),
    ],
)
def test_orders_require_decimal_valid_direction_utc_and_strong_key(field, value):
    with pytest.raises(BrokerError):
        order(**{field: value})


def test_maintenance_hash_is_stable_but_payload_bound():
    first = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=D("2609"))
    assert first.request_hash == replace(first, created_at=NOW + timedelta(seconds=20)).request_hash
    assert first.request_hash != replace(first, sl=D("2609.1")).request_hash
    opened = BrokerCommand(Operation.OPEN, KEY, NOW, order=order())
    assert (
        opened.request_hash
        != replace(opened, order=replace(order(), created_at=NOW + timedelta(seconds=1))).request_hash
    )


def test_native_tick_grid_is_not_decimal_digits_and_lot_never_rounds_up():
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], tick_size=D("0.25"))
    assert snap(D("100.12"), meta.tick_size, up=True) == D("100.25")
    assert snap(D("100.12"), meta.tick_size, up=False) == D("100.00")
    assert adverse_price(D("100"), Side.BUY, meta, 10, entry=True) == D("100.25")
    assert adverse_price(D("100"), Side.SELL, meta, 10, entry=True) == D("99.75")
    assert adverse_price(D("100"), Side.BUY, meta, 10, entry=False) == D("99.75")
    assert floor_volume(D("0.009"), meta) == 0
    assert floor_volume(D("0.0199"), meta) == D("0.01")


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_stop_protection_only_improves(side):
    meta = synthetic_catalogue()[0]["XAUUSD"]
    position = Position(
        17,
        200,
        "XAUUSD",
        side,
        D("0.01"),
        D("2610"),
        D("2608") if side == Side.BUY else D("2612"),
        D("2616") if side == Side.BUY else D("2604"),
        NOW,
        42,
    )
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    improved = D("2609") if side == Side.BUY else D("2611")
    command = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=improved)
    assert protection_prices(command, position, meta, tick, settings())[0] == improved
    with pytest.raises(RiskViolation):
        protection_prices(replace(command, sl=position.sl - side.sign), position, meta, tick, settings())
    with pytest.raises(InvalidOrder):
        protection_prices(replace(command, sl=D("0")), position, meta, tick, settings())
    with pytest.raises(InvalidOrder):
        protection_prices(replace(command, tp=D("0")), position, meta, tick, settings())


def test_existing_freeze_zone_and_tp_extension_are_not_bypassed():
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], freeze_level=30)
    position = Position(
        17, 200, "XAUUSD", Side.BUY, D("0.01"), D("2610.20"), D("2609.80"), D("2616"), NOW, 42
    )
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    command = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=D("2609.90"))
    with pytest.raises(InvalidOrder):
        protection_prices(command, position, meta, tick, settings())
    with pytest.raises(RiskViolation):
        protection_prices(
            replace(command, sl=None, tp=D("2617")), position, replace(meta, freeze_level=0), tick, settings()
        )


@pytest.mark.parametrize(
    "case",
    ["old_quote", "future_quote", "old_order", "spread", "deviation", "wrong_symbol", "disabled", "no_stops"],
)
def test_entry_fails_closed(case):
    meta = synthetic_catalogue()[0]["XAUUSD"]
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    current = order()
    if case == "old_quote":
        tick = replace(tick, time=NOW - timedelta(seconds=11))
    if case == "future_quote":
        tick = replace(tick, time=NOW + timedelta(seconds=3))
    if case == "old_order":
        current = replace(current, created_at=NOW - timedelta(seconds=31))
    if case == "spread":
        tick = replace(tick, ask=D("2610.40"))
    if case == "deviation":
        tick = replace(tick, bid=D("2610.20"), ask=D("2610.40"))
    if case == "wrong_symbol":
        current = replace(current, symbol="UNAPPROVED")
    if case == "disabled":
        meta = replace(meta, trade_mode=0)
    if case == "no_stops":
        meta = replace(meta, order_mode=1)
    with pytest.raises(BrokerError):
        validate_entry(current, meta, tick, settings(), ManualClock(NOW))


def candle_frame():
    return pd.DataFrame(
        [
            dict(
                time=NOW - timedelta(minutes=10 - n * 5),
                open=10.0,
                high=12.0,
                low=9.0,
                close=11.0,
                tick_volume=50,
                spread=1,
                real_volume=0,
            )
            for n in range(3)
        ]
    )


def test_candles_exclude_forming_bars_and_carry_close_time():
    result = validated_candles(candle_frame(), "M5", NOW)
    assert len(result) == 2
    assert result.close_time.max() == pd.Timestamp(NOW)


@pytest.mark.parametrize("case", ["duplicate", "unordered", "nan", "negative_volume", "range", "naive"])
def test_invalid_candles_are_rejected(case):
    frame = candle_frame().iloc[:2].copy()
    if case == "duplicate":
        frame.loc[1, "time"] = frame.loc[0, "time"]
    if case == "unordered":
        frame = frame.iloc[::-1]
    if case == "nan":
        frame.loc[0, "close"] = float("nan")
    if case == "negative_volume":
        frame.loc[0, "tick_volume"] = -1
    if case == "range":
        frame.loc[0, "high"] = 5
    if case == "naive":
        frame["time"] = frame.time.dt.tz_localize(None)
    with pytest.raises(BrokerError):
        validated_candles(frame, "M5", NOW)


def test_simulation_config_slippage_and_timeout_bounds():
    with pytest.raises(ValidationError):
        settings(paper_slippage_points=11)
    with pytest.raises(ValidationError):
        settings(mt5_api_timeout_seconds=0)
    with pytest.raises(ValidationError):
        settings(paper_initial_balance=0)
    with pytest.raises(ValueError):
        ManualClock(NOW).advance(timedelta(seconds=-1))


def test_tick_side_convention_and_staleness():
    tick = Tick("EURUSD", D("1.10"), D("1.11"), NOW)
    assert tick.entry(Side.BUY) == D("1.11")
    assert tick.exit(Side.BUY) == D("1.10")
    assert tick.entry(Side.SELL) == D("1.10")
    assert tick.exit(Side.SELL) == D("1.11")
    with pytest.raises(StaleData):
        tick.fresh(ManualClock(NOW + timedelta(seconds=11)), 10)
```

### `tests/test_order_calculator.py`

```python
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from core.settings import Settings
from tests.test_trading_contracts import NOW
from trading.currency import CurrencyConverter
from trading.mock_mt5 import MockMT5Client, synthetic_catalogue
from trading.order_calculator import OrderCalculator
from trading.price_rules import adverse_price
from trading.types import InvalidOrder, ManualClock, RiskViolation, Side, StaleData

D = Decimal


@pytest.mark.parametrize(
    "symbol,side,sl",
    [
        ("XAUUSD", Side.BUY, "2608"),
        ("XAUUSD", Side.SELL, "2612.5"),
        ("EURUSD", Side.BUY, "1.099"),
        ("EURUSD", Side.SELL, "1.1012"),
    ],
)
async def test_sizing_and_cost_aware_plan(symbol, side, sl):
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        calculator = OrderCalculator(broker, cfg)
        plan = await calculator.plan_market_order(symbol, side, D(sl), strategy="test")
        assert plan is not None and plan.order.volume > 0
        assert 0 < plan.worst_loss_account <= plan.risk_budget_account
        assert plan.expected_net_profit_usd >= plan.target_profit_usd >= D("5")
        assert plan.reward_risk >= cfg.min_net_reward_risk
        assert plan.order.volume % (await broker.get_symbol_info(symbol)).volume_step == 0


async def test_below_minimum_lot_returns_skip_not_enlarged_risk():
    cfg = Settings(_env_file=None, paper_initial_balance=D("10"))
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "XAUUSD", Side.BUY, D("2608"), strategy="test"
        )
        assert plan is None


async def test_requested_risk_cannot_raise_configured_cap():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).calculate_lot_size(
                "XAUUSD", Side.BUY, D("2610.2"), D("2608"), risk_percent=D("1")
            )


async def test_margin_can_reduce_the_risk_sized_lot():
    cfg = Settings(_env_file=None, mock_leverage=1)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        lot = await OrderCalculator(broker, cfg).calculate_lot_size(
            "XAUUSD", Side.BUY, D("2610.2"), D("2608")
        )
        assert lot.volume == 0  # $2,610+ minimum-lot margin exceeds $300 allowance.


async def test_non_linear_native_valuation_is_rechecked_downward():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        original = broker.calculate_profit

        async def nonlinear(symbol, side, volume, entry, exit_price):
            result = await original(symbol, side, volume, entry, exit_price)
            return result * (D("2") if volume > D("0.01") else D("1"))

        broker.calculate_profit = nonlinear
        lot = await OrderCalculator(broker, cfg).calculate_lot_size(
            "XAUUSD", Side.BUY, D("2610.2"), D("2608")
        )
        assert lot.volume == D("0.01") and lot.worst_loss_account <= D("5")


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_target_on_large_native_tick_includes_slippage_and_vwap_entry(side):
    cfg = Settings(_env_file=None)
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], tick_size=D("0.25"))
    async with MockMT5Client(
        cfg, clock=ManualClock(NOW), symbols={"XAUUSD": meta}, quotes={"XAUUSD": (D("2610"), D("2610.25"))}
    ) as broker:
        calc = OrderCalculator(broker, cfg)
        entry = D("2610.375")  # A legal weighted execution entry, NOT a legal TP grid price.
        price = await calc.price_for_profit_usd(
            "XAUUSD", side, D("0.01"), entry, D("5"), include_costs=True, exit_slippage_points=10
        )
        assert price % meta.tick_size == 0
        actual = adverse_price(price, side, meta, 10, entry=False)
        assert (
            await calc.calculate_profit_usd("XAUUSD", side, D("0.01"), entry, actual, include_costs=True) >= 5
        )
        neighbor = adverse_price(price - side.sign * meta.tick_size, side, meta, 10, entry=False)
        assert (
            await calc.calculate_profit_usd("XAUUSD", side, D("0.01"), entry, neighbor, include_costs=True)
            < 5
        )
        plan = await calc.plan_market_order(
            "XAUUSD", side, D("2608") if side == Side.BUY else D("2612.5"), strategy="large_tick"
        )
        assert plan is not None and plan.order.tp % meta.tick_size == 0


async def test_net_reward_floor_rejects_small_fixed_objective_without_risk_escalation():
    cfg = Settings(_env_file=None, use_dynamic_target=False, target_profit_usd_per_trade=D("0.5"))
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).plan_market_order(
                "XAUUSD", Side.BUY, D("2608"), strategy="test"
            )


async def test_target_solver_is_bounded_on_flat_valuation():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        calls = 0

        async def flat(*args):
            nonlocal calls
            calls += 1
            return D("0")

        broker.calculate_profit = flat
        with pytest.raises(RiskViolation):
            await OrderCalculator(broker, cfg).price_for_profit_usd(
                "XAUUSD", Side.BUY, D("0.01"), D("2610"), D("5")
            )
        assert calls <= 27


async def test_eur_account_signed_bid_ask_conversion_and_net_usd_target():
    cfg = Settings(_env_file=None, account_currency="EUR", account_to_usd_symbols={"EUR": "EURUSD"})
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        converter = CurrencyConverter(broker, cfg)
        assert await converter.to_usd(D("10")) == D("11")
        assert await converter.to_usd(D("-10")) == D("-11.00120")
        assert await converter.usd_to_account(D("10")) == D("10") / D("1.10012")
        assert await converter.usd_to_account(D("-10")) == D("-10") / D("1.10000")
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "XAUUSD", Side.BUY, D("2608"), strategy="fx"
        )
        assert plan is not None and plan.expected_net_profit_usd >= plan.target_profit_usd


@pytest.mark.parametrize("mapping", [{}, {"EUR": "GBPUSD"}])
async def test_missing_or_wrong_conversion_route_fails_closed(mapping):
    cfg = Settings(_env_file=None, account_currency="EUR", account_to_usd_symbols=mapping)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        with pytest.raises(RiskViolation):
            await CurrencyConverter(broker, cfg).to_usd(D("5"))


async def test_cross_currency_via_two_signed_usd_legs_and_stale_fx_veto():
    cfg = Settings(_env_file=None, account_to_usd_symbols={"EUR": "EURUSD", "JPY": "USDJPY"})
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        converter = CurrencyConverter(broker, cfg)
        assert await converter.convert(D("10"), "EUR", "JPY") == D("11") * D("150")
        assert await converter.convert(D("-10"), "EUR", "JPY") == D("-11.00120") * D("150.020")
        await broker.set_tick("EURUSD", D("1.10"), D("1.10012"), timestamp=NOW - timedelta(seconds=11))
        with pytest.raises(StaleData):
            await converter.convert(D("5"), "EUR", "USD")


async def test_zero_or_nonfinite_target_rejected_by_planner():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        for target in (D("0"), D("NaN"), D("-1")):
            with pytest.raises(InvalidOrder):
                await OrderCalculator(broker, cfg).plan_market_order(
                    "XAUUSD", Side.BUY, D("2608"), strategy="test", target_usd=target
                )


async def test_sell_solver_clamps_positive_bound_instead_of_overshooting_a_reachable_target():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        price = await OrderCalculator(broker, cfg).price_for_profit_usd(
            "XAUUSD", Side.SELL, D("0.01"), D("100"), D("80")
        )
        assert price == D("20")
```

### `tests/test_simulated_broker.py`

```python
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from core.security import sha256_json
from core.settings import Settings
from tests.test_trading_contracts import NOW
from trading.mock_mt5 import MockMarketData, MockMT5Client
from trading.order_calculator import OrderCalculator
from trading.paper_mt5 import PaperMT5Client
from trading.symbol_manager import SymbolManager
from trading.types import (
    BrokerError,
    InvalidOrder,
    ManualClock,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    TradingDisabled,
    UnsupportedSymbol,
)

D = Decimal


def key(name):
    return sha256_json({"test": name})


async def make_plan(broker, cfg, side=Side.BUY):
    return await OrderCalculator(broker, cfg).plan_market_order(
        "XAUUSD",
        side,
        D("2608") if side == Side.BUY else D("2612.5"),
        strategy="test",
        idempotency_key=key("entry"),
    )


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_bid_ask_slippage_fees_and_complete_deal_ledger(side):
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg, side)
        result = await (
            broker.open_market_buy(plan.order) if side == Side.BUY else broker.open_market_sell(plan.order)
        )
        position = (await broker.get_positions())[0]
        assert result.status == ResultStatus.FILLED
        assert position.ticket != result.order_ticket != position.identifier
        assert position.profit < 0 and position.entry_commission < 0
        assert (await broker.get_equity()) < (await broker.get_balance()) < 1000
        target = plan.order.tp
        bid, ask = (target, target + D("0.2")) if side == Side.BUY else (target - D("0.2"), target)
        clock.advance(timedelta(seconds=1))
        await broker.set_tick("XAUUSD", bid, ask)
        assert await broker.get_positions() == ()
        deals = await broker.get_deals(NOW)
        assert len(deals) == 2 and deals[0].entry == "in" and deals[1].entry == "out"
        assert sum(deal.net for deal in deals) == (await broker.get_balance()) - D("1000")
        assert sum(deal.net for deal in deals) >= plan.target_profit_usd
        assert len(await broker.get_closed_deals(NOW)) == 1


async def test_duplicate_open_is_one_fill_even_concurrently_and_changed_body_is_rejected():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        results = await asyncio.gather(*(broker.open_market_buy(plan.order) for _ in range(8)))
        assert all(result == results[0] for result in results)
        assert len(await broker.get_positions()) == 1
        assert len(await broker.get_deals(NOW)) == 1
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(plan.order, tp=plan.order.tp + D("1")))
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(plan.order, idempotency_key=key("averaging")))


async def test_stop_gap_is_not_filled_at_favorable_original_stop():
    cfg = Settings(_env_file=None)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        closed = (await broker.get_closed_deals(NOW))[0]
        assert closed.price == D("2589.98") < plan.order.sl
        assert closed.net < -plan.worst_loss_account  # Nominal risk is not a gap guarantee.


async def test_stale_tick_does_not_trigger_ghost_stop_and_blocks_new_entry():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"), timestamp=NOW - timedelta(seconds=11))
        assert len(await broker.get_positions()) == 1
        assert (await broker.get_account_info()).quotes_stale
        second = await OrderCalculator(broker, cfg).plan_market_order(
            "EURUSD", Side.BUY, D("1.099"), strategy="test"
        )
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(second.order)
        assert len(await broker.get_deals(NOW)) == 1


async def test_wrong_identity_close_duplicate_close_and_sl_never_loosened():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        with pytest.raises(TradingDisabled):
            await broker.close_position(
                position.ticket, position_identifier=position.identifier + 1, idempotency_key=key("wrong")
            )
        with pytest.raises(RiskViolation):
            await broker.modify_sl(
                position.ticket,
                plan.order.sl - D("1"),
                position_identifier=position.identifier,
                idempotency_key=key("loosen"),
            )
        await broker.set_tick("XAUUSD", D("2613"), D("2613.2"))
        modified = await broker.modify_sl(
            position.ticket, D("2611"), position_identifier=position.identifier, idempotency_key=key("lock")
        )
        clock.advance(timedelta(seconds=1))
        assert modified == await broker.modify_sl(
            position.ticket, D("2611"), position_identifier=position.identifier, idempotency_key=key("lock")
        )
        assert (await broker.get_positions())[0].sl == D("2611")
        closed = await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        clock.advance(timedelta(seconds=1))
        assert closed == await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        assert len(await broker.get_closed_deals(NOW)) == 1


async def test_daily_count_is_maximum_not_minimum_target():
    cfg = Settings(_env_file=None, max_daily_trades=1, min_daily_trades_target=0)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("close")
        )
        second = await make_plan(broker, cfg)
        with pytest.raises(RiskViolation):
            await broker.open_market_buy(replace(second.order, idempotency_key=key("second")))


async def test_drawdown_latch_survives_restart_and_new_day():
    cfg = Settings(_env_file=None, max_drawdown_percent=D("3"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        snapshot = await broker.export_state()
        assert snapshot["drawdown_latched"]
    clock.advance(timedelta(days=1))
    async with MockMT5Client(cfg, clock=clock, restored_state=snapshot) as restored:
        plan = await make_plan(restored, cfg)
        with pytest.raises(RiskViolation):
            await restored.open_market_buy(replace(plan.order, idempotency_key=key("newday")))


async def test_snapshot_restores_exposure_and_idempotency_not_a_fresh_paper_account():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as first:
        plan = await make_plan(first, cfg)
        result = await first.open_market_buy(plan.order)
        snapshot = await first.export_state()
    async with MockMT5Client(cfg, clock=clock, restored_state=snapshot) as restored:
        assert len(await restored.get_positions()) == 1
        assert await restored.open_market_buy(plan.order) == result
        assert len(await restored.get_deals(NOW)) == 1
        assert await restored.get_balance() == D(snapshot["balance"])
    broken = dict(snapshot, config_hash="0" * 64)
    with pytest.raises(RiskViolation):
        async with MockMT5Client(cfg, clock=clock, restored_state=broken):
            pass


async def test_held_position_swap_is_charged_on_close_and_in_equity():
    cfg = Settings(_env_file=None, estimated_swap_usd_per_lot_per_day=D("10"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        clock.advance(timedelta(days=2))
        position = (await broker.get_positions())[0]
        assert position.swap == -position.volume * D("20")
        await broker.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("swap_close")
        )
        assert (await broker.get_closed_deals(NOW))[0].swap == position.swap


async def test_synthetic_candles_are_prefix_stable_and_never_depend_on_future_tick():
    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        old = await broker.get_candles("XAUUSD", "M5", 30)
        clock.advance(timedelta(minutes=5))
        await broker.set_tick("XAUUSD", D("4000"), D("4000.2"))
        future = await broker.get_candles("XAUUSD", "M5", 31)
        pd.testing.assert_frame_equal(old, future.iloc[:-1].reset_index(drop=True))
        assert future.close_time.max().to_pydatetime() <= clock.now()
        with pytest.raises(Exception, match="cannot peek"):
            await broker.get_candles("XAUUSD", "M5", as_of=clock.now() + timedelta(seconds=1))


async def test_symbol_manager_exact_aliases_disables_missing_and_never_guesses_news():
    cfg = Settings(_env_file=None, symbols=("XAUUSD", "MISSING"), symbol_aliases={"XAUUSD": "XAUUSDm"})
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        manager = SymbolManager(broker, cfg)
        await manager.initialize()
        assert manager.resolve("XAUUSD").native == "XAUUSDm"
        assert "MISSING" in manager.errors()
        assert manager.news_exposure_known("XAUUSD")
        with pytest.raises(UnsupportedSymbol):
            manager.resolve("MISSING")
    other = Settings(_env_file=None, symbols=("USDJPY",))
    async with MockMT5Client(other, clock=ManualClock(NOW)) as broker:
        manager = SymbolManager(broker, other)
        await manager.initialize()
        assert not manager.news_exposure_known("USDJPY")


async def test_paper_adapter_calls_no_source_writes_and_keeps_simulated_capital():
    cfg = Settings(_env_file=None)
    source = MockMarketData(cfg, clock=ManualClock(NOW))

    async def forbid(*args, **kwargs):
        raise AssertionError("a source write must NEVER occur")

    source.open_market_buy = source.open_market_sell = source.close_position = source.modify_sl = (
        source.modify_tp
    ) = forbid
    async with PaperMT5Client(source, cfg) as paper:
        plan = await make_plan(paper, cfg)
        await paper.open_market_buy(plan.order)
        assert paper.source_kind == SourceKind.PAPER and paper.market_source_kind == SourceKind.SYNTHETIC
        assert (await paper.get_account_info()).source == SourceKind.PAPER
        position = (await paper.get_positions())[0]
        await paper.modify_sl(
            position.ticket,
            D("2609"),
            position_identifier=position.identifier,
            idempotency_key=key("paper_sl"),
        )
        await paper.close_position(
            position.ticket, position_identifier=position.identifier, idempotency_key=key("paper_close")
        )
        assert len(await paper.get_deals(NOW)) == 2


def test_simulator_cannot_masquerade_as_demo_execution():
    cfg = Settings(_env_file=None, paper_trading=False, mt5_backend="real")
    with pytest.raises(TradingDisabled):
        PaperMT5Client(MockMarketData(cfg), cfg)
    with pytest.raises(BrokerError):
        MockMT5Client(cfg)


async def test_tp_removal_and_unapproved_extension_are_blocked_in_paper():
    cfg = Settings(_env_file=None, allow_tp_extension=True)
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        position = (await broker.get_positions())[0]
        with pytest.raises(InvalidOrder):
            await broker.modify_tp(
                position.ticket,
                D("0"),
                position_identifier=position.identifier,
                idempotency_key=key("remove_tp"),
            )
        with pytest.raises(TradingDisabled):
            await broker.modify_tp(
                position.ticket,
                plan.order.tp + D("1"),
                position_identifier=position.identifier,
                idempotency_key=key("extend_tp"),
            )


async def test_daily_loss_reset_does_not_clear_drawdown_and_carry_gap_is_counted():
    cfg = Settings(_env_file=None, max_daily_loss_percent=D("1"))
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        clock.advance(timedelta(days=1))
        await broker.set_tick("XAUUSD", D("2590"), D("2590.2"))
        state = await broker.export_state()
        assert state["daily_latched"] and not state["drawdown_latched"]
        clock.advance(timedelta(days=1))
        await broker.get_account_info()
        assert not (await broker.export_state())["daily_latched"]


@pytest.mark.parametrize(
    "case",
    ["source", "data_account", "negative_margin", "future_position", "counter", "cache", "original_tp"],
)
async def test_snapshot_integrity_rejects_wrong_scope_or_incoherent_accounting(case):
    import copy

    cfg = Settings(_env_file=None)
    clock = ManualClock(NOW)
    async with MockMT5Client(cfg, clock=clock) as broker:
        plan = await make_plan(broker, cfg)
        await broker.open_market_buy(plan.order)
        state = copy.deepcopy(await broker.export_state())
    if case == "source":
        state["source_kind"] = "mt5"
    if case == "data_account":
        state["data_account_key"] = "other-account"
    if case == "negative_margin":
        state["margins"] = {name: "-1" for name in state["margins"]}
    if case == "future_position":
        state["positions"][0]["time"] = (NOW + timedelta(days=1)).isoformat()
    if case == "counter":
        state["counters"][2] = 0
    if case == "cache":
        state["cache"][key("entry")][1]["account_key"] = "other-account"
    if case == "original_tp":
        state["original_tps"] = {name: "NaN" for name in state["original_tps"]}
    with pytest.raises(BrokerError):
        async with MockMT5Client(cfg, clock=clock, restored_state=state):
            pass
```

### `tests/test_mt5_client.py`

```python
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Event, get_ident
from types import SimpleNamespace as NS

import pytest

from core.security import sha256_json
from core.settings import Settings
from tests.fake_mt5_sdk import FakeSDK, FixtureAuthority
from tests.test_trading_contracts import NOW, order
from trading.mt5_client import MT5Client
from trading.types import (
    BrokerError,
    ConnectionUnavailable,
    IdentityChanged,
    InvalidOrder,
    ManualClock,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    TradingDisabled,
    UncertainExecution,
)

D = Decimal


def key(name):
    return sha256_json({"native_test": name})


def demo(**kwargs):
    return Settings(_env_file=None, mt5_backend="real", paper_trading=False, **kwargs)


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_native_request_math_identifiers_and_no_duplicate_send(side):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    authority = FixtureAuthority(cfg, clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        current = order(side=side)
        result = await (
            client.open_market_buy(current) if side == Side.BUY else client.open_market_sell(current)
        )
        duplicate = await (
            client.open_market_buy(current) if side == Side.BUY else client.open_market_sell(current)
        )
        assert duplicate == result and len(sdk.requests) == 1
        request = sdk.requests[0]
        assert request["type"] == (0 if side == Side.BUY else 1)
        assert request["price"] == (2610.2 if side == Side.BUY else 2610.0)
        assert request["sl"] == float(current.sl) and request["tp"] == float(current.tp)
        assert request["magic"] == cfg.mt5_magic_number and len(request["comment"]) <= 31
        assert request["type_filling"] == 0  # Capability 1 FOK maps to enum 0.
        assert result.order_ticket == 777 and result.deal_ticket == 888
        assert result.position_identifier is None and result.requires_reconciliation
        assert client.source_kind == SourceKind.TEST_SDK
        with pytest.raises(RiskViolation):
            await client.open_market_buy(replace(order(), tp=D("2617")))
        thread_ids = {thread for _, thread in sdk.calls}
        assert len(thread_ids) == 1 and get_ident() not in thread_ids


async def test_all_real_writes_default_to_deny_all_even_in_demo():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        with pytest.raises(TradingDisabled, match="no durable"):
            await client.open_market_buy(order())
        sdk.positions = (sdk.owned_position(),)
        with pytest.raises(TradingDisabled):
            await client.close_position(42, position_identifier=50042, idempotency_key=key("close"))
        with pytest.raises(TradingDisabled):
            await client.modify_sl(42, D("2609"), position_identifier=50042, idempotency_key=key("sl"))
        assert not sdk.requests and not any(name == "order_check" for name, _ in sdk.calls)


async def test_paper_native_source_is_strictly_read_only_even_with_fixture_authority():
    cfg, clock = Settings(_env_file=None, mt5_backend="real"), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=FixtureAuthority(cfg, clock)) as client:
        with pytest.raises(TradingDisabled, match="strictly read-only"):
            await client.open_market_buy(order())
        assert not sdk.requests


@pytest.mark.parametrize(
    "case",
    [
        "foreign",
        "unprotected",
        "same_symbol",
        "pending",
        "positions_none",
        "orders_none",
        "python_disabled",
        "account_disabled",
        "stale",
    ],
)
async def test_unstable_or_unmanaged_exposure_blocks_before_authorization(case):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    authority = FixtureAuthority(cfg, clock)
    if case == "foreign":
        sdk.positions = (sdk.owned_position(symbol="EURUSD", magic=1),)
    if case == "unprotected":
        sdk.positions = (sdk.owned_position(symbol="EURUSD", sl=0.0),)
    if case == "same_symbol":
        sdk.positions = (sdk.owned_position(),)
    if case == "pending":
        sdk.pending = (NS(ticket=9),)
    if case == "positions_none":
        sdk.positions = None
    if case == "orders_none":
        sdk.pending = None
    if case == "python_disabled":
        sdk.tradeapi_disabled = True
    if case == "account_disabled":
        sdk.account.trade_allowed = False
    if case == "stale":
        sdk.tick_time = NOW - timedelta(seconds=11)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests and not authority.authorized


@pytest.mark.parametrize("change", ["login", "currency", "kind"])
async def test_pinned_identity_and_mode_mismatch_latch_quarantine(change):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        if change == "login":
            sdk.account.login += 1
        if change == "currency":
            sdk.account.currency = "EUR"
        if change == "kind":
            sdk.account.trade_mode = 2
        with pytest.raises(IdentityChanged):
            await client.get_account_info()
        assert client.health()["writes_quarantined"]
        assert not sdk.requests


async def test_disconnection_read_reconnects_but_does_not_switch_accounts():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        sdk.connected = False
        await client.get_account_info()
        assert sdk.init_count == 2
        sdk.account.login += 1
        sdk.connected = False
        with pytest.raises(IdentityChanged):
            await client.get_account_info()
        assert client.health()["writes_quarantined"]


async def test_one_sdk_process_lease_and_safe_failed_initialize_cleanup():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    first = MT5Client(cfg, sdk=sdk, clock=clock)
    await first.initialize()
    second = MT5Client(cfg, sdk=sdk, clock=clock)
    with pytest.raises(ConnectionUnavailable, match="leased"):
        async with second:
            pass
    assert sdk.shutdown_count == 0  # Failed second cannot shut down first's SDK.
    await first.shutdown()
    async with MT5Client(cfg, sdk=sdk, clock=clock):
        pass
    assert sdk.shutdown_count == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_key", "different"),
        ("request_hash", "f" * 64),
        ("config_hash", "f" * 64),
        ("expires_at", NOW),
        ("max_volume", D("0")),
        ("max_loss_account", D("0")),
        ("entry_gates_verified", False),
    ],
)
async def test_authorization_binding_and_unsent_intent_outcome(field, value):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    authority.mutate_grant = lambda grant: replace(grant, **{field: value})
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests
        assert authority.results[-1].status == ResultStatus.REJECTED
        assert authority.results[-1].reason == "preflight_aborted_no_send"


@pytest.mark.parametrize("check", [None, NS(retcode=10019)])
async def test_order_check_failure_is_definitely_unsent_and_reported(check):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.check = check
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == ResultStatus.REJECTED
        assert not sdk.requests and authority.results == [result]


@pytest.mark.parametrize("mutation", ["tick_move", "exposure", "permission", "equity", "pending", "expired"])
async def test_second_snapshot_prevents_toctou_entry_during_authorization(mutation):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)

    def mutate():
        if mutation == "tick_move":
            sdk.bid, sdk.ask = 2611.0, 2611.2
        if mutation == "exposure":
            sdk.positions = (sdk.owned_position(),)
        if mutation == "permission":
            sdk.tradeapi_disabled = True
        if mutation == "equity":
            sdk.account.equity = 100.0
        if mutation == "pending":
            sdk.pending = (NS(ticket=1),)
        if mutation == "expired":
            clock.advance(timedelta(seconds=21))

    authority.after_authorize = mutate
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests and authority.latest == ResultStatus.REJECTED


@pytest.mark.parametrize(
    "code,status,quarantine",
    [
        (10009, ResultStatus.FILLED, False),
        (10010, ResultStatus.PARTIAL, True),
        (10008, ResultStatus.ACCEPTED, True),
        (10004, ResultStatus.REJECTED, False),
        (10019, ResultStatus.REJECTED, False),
        (10012, ResultStatus.UNKNOWN, True),
        (10031, ResultStatus.UNKNOWN, True),
        (43210, ResultStatus.UNKNOWN, True),
    ],
)
async def test_broker_acknowledgement_categories_never_infer_position_ticket(code, status, quarantine):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.ack.retcode = code
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == status and result.position_identifier is None
        assert client.health()["writes_quarantined"] == quarantine
        assert len(sdk.requests) == 1
        if quarantine:
            await client.reconnect()
            with pytest.raises(UncertainExecution):
                await client.open_market_buy(order(idempotency_key=key("retry")))
            assert len(sdk.requests) == 1


@pytest.mark.parametrize("case", ["none", "send_exception", "invalid_volume", "switch_after_send"])
async def test_ambiguous_send_never_retries(case):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    if case == "none":
        sdk.ack = None
    if case == "send_exception":
        sdk.send_error = RuntimeError("TEST-secret raw")
    if case == "invalid_volume":
        sdk.ack.volume = 5.0
    if case == "switch_after_send":
        sdk.send_hook = lambda: setattr(sdk.account, "login", 999)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == ResultStatus.UNKNOWN and client.health()["writes_quarantined"]
        with pytest.raises(UncertainExecution):
            await client.open_market_buy(order())
        assert len(sdk.requests) == 1


async def test_durable_callback_failure_quarantines_already_sent_fill_without_leaking_error(caplog):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    authority.fail_result = True
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(UncertainExecution) as error:
            await client.open_market_buy(order())
        assert "TEST-persistence" not in str(error.value) + caplog.text
        assert client.health()["writes_quarantined"] and len(sdk.requests) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_nonblocking_timeout_or_cancel_quarantines_and_drains_late_ack(cancel):
    cfg, clock = demo(mt5_api_timeout_seconds=0.08), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.block_send = True
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    try:
        assert await asyncio.to_thread(sdk.send_entered.wait, 1)
        # A blocked SDK worker must not block the event loop / Telegram polling.
        sentinel = asyncio.create_task(asyncio.sleep(0.001, result="responsive"))
        assert await sentinel == "responsive"
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(UncertainExecution):
                await task
        assert client.health()["writes_quarantined"]
        with pytest.raises(UncertainExecution):
            await client.open_market_buy(order())
        sdk.send_release.set()
        assert await asyncio.to_thread(authority.result_event.wait, 1)
        assert authority.latest == ResultStatus.FILLED
        authority.on_uncertain(None, "late_observer")
        assert authority.latest == ResultStatus.FILLED
        assert len(sdk.requests) == 1
    finally:
        sdk.send_release.set()
        await client.shutdown()


async def test_shutdown_timeout_retains_sdk_lease_until_worker_really_drains():
    cfg, clock = demo(mt5_api_timeout_seconds=0.05), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.block_send = True
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    assert await asyncio.to_thread(sdk.send_entered.wait, 1)
    try:
        with pytest.raises(UncertainExecution):
            await task
        with pytest.raises(ConnectionUnavailable):
            await client.shutdown()
        second = MT5Client(cfg, sdk=sdk, clock=clock)
        with pytest.raises(ConnectionUnavailable, match="leased"):
            async with second:
                pass
    finally:
        sdk.send_release.set()
        assert await asyncio.to_thread(sdk.shutdown_done.wait, 1)
    async with MT5Client(cfg, sdk=sdk, clock=clock):
        pass


async def test_read_timeout_latches_writes_and_is_not_reported_as_empty_data():
    cfg, clock = demo(mt5_api_timeout_seconds=0.05), ManualClock(NOW)
    sdk = FakeSDK(clock)
    client = MT5Client(cfg, sdk=sdk, clock=clock)
    await client.initialize()
    release, entered = Event(), Event()

    def blocked():
        entered.set()
        release.wait(2)

    sdk.account_hook = blocked
    try:
        task = asyncio.create_task(client.get_account_info())
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(ConnectionUnavailable):
            await task
        assert client.health()["writes_quarantined"]
    finally:
        sdk.account_hook = None
        release.set()
        await client.shutdown()


async def test_close_and_modify_keep_owner_identifiers_and_idempotency_after_time_changes():
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.positions = (sdk.owned_position(),)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(TradingDisabled):
            await client.close_position(42, position_identifier=50043, idempotency_key=key("wrong"))
        with pytest.raises(RiskViolation):
            await client.modify_sl(42, D("2607"), position_identifier=50042, idempotency_key=key("loosen"))
        changed = await client.modify_sl(42, D("2609"), position_identifier=50042, idempotency_key=key("sl"))
        clock.advance(timedelta(seconds=1))
        assert changed == await client.modify_sl(
            42, D("2609"), position_identifier=50042, idempotency_key=key("sl")
        )
        assert sdk.requests[0]["action"] == 6 and sdk.requests[0]["position"] == 42
        closed = await client.close_position(42, position_identifier=50042, idempotency_key=key("close"))
        clock.advance(timedelta(seconds=1))
        assert closed == await client.close_position(
            42, position_identifier=50042, idempotency_key=key("close")
        )
        assert sdk.requests[-1]["position"] == 42 and sdk.requests[-1]["type"] == 1
        assert sdk.requests[-1]["price"] == 2610.0 and len(sdk.requests) == 2


@pytest.mark.parametrize(
    "approved,target,success", [(False, "2617", False), (True, "2617", True), (True, "2618", False)]
)
async def test_tp_extension_needs_authority_and_original_target_bound(approved, target, success):
    cfg, clock = demo(allow_tp_extension=True), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.positions = (sdk.owned_position(),)
    authority.mutate_grant = lambda grant: replace(grant, allow_tp_extension=approved, original_tp=D("2616"))
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        if success:
            await client.modify_tp(42, D(target), position_identifier=50042, idempotency_key=key("tp"))
            assert len(sdk.requests) == 1
        else:
            with pytest.raises(BrokerError):
                await client.modify_tp(42, D(target), position_identifier=50042, idempotency_key=key("tp"))
            assert not sdk.requests and authority.latest == ResultStatus.REJECTED


def test_filling_capabilities_are_not_native_enums():
    from trading.mock_mt5 import synthetic_catalogue

    meta = synthetic_catalogue()[0]["XAUUSD"]
    assert MT5Client._filling(replace(meta, filling_mode=1)) == 0
    assert MT5Client._filling(replace(meta, filling_mode=2)) == 1
    with pytest.raises(InvalidOrder):
        MT5Client._filling(replace(meta, filling_mode=0, execution_mode=2))
    assert MT5Client._filling(replace(meta, filling_mode=0, execution_mode=1)) == 2


async def test_native_candles_closed_only_and_deals_include_cost_and_cash_legs():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    rows = []
    for index, (kind, entry, profit, commission) in enumerate(
        [(0, 0, 0.0, -0.07), (1, 1, 5.0, -0.07), (2, 0, 100.0, 0.0)]
    ):
        rows.append(
            NS(
                ticket=index + 1,
                order=10 + index,
                position_id=50042 if kind != 2 else 0,
                symbol="XAUUSD" if kind != 2 else "",
                type=kind,
                entry=entry,
                time=int(NOW.timestamp()),
                time_msc=int(NOW.timestamp() * 1000),
                volume=0.01 if kind != 2 else 0.0,
                price=2610.0,
                profit=profit,
                commission=commission,
                swap=0.0,
                fee=0.0,
                magic=cfg.mt5_magic_number,
                reason=0,
                comment="test",
            )
        )
    sdk.deals = tuple(rows)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        frame = await client.get_candles("XAUUSD", "M5", 5)
        assert len(frame) == 5 and frame.close_time.max().to_pydatetime() <= clock.now()
        with pytest.raises(BrokerError):
            await client.get_candles("XAUUSD", "M5", as_of=NOW + timedelta(seconds=1))
        deals = await client.get_deals(NOW - timedelta(seconds=1))
        assert len(deals) == 3 and deals[2].entry == "cash"
        assert deals[0].net + deals[1].net == D("4.86")
        assert (await client.get_closed_deals(NOW))[0].net == D("4.93")  # Not the whole trade PnL!
        sdk.deals = None
        with pytest.raises(ConnectionUnavailable):
            await client.get_deals(NOW)


async def test_live_requires_owner_gate_in_addition_to_config_flags():
    cfg = Settings(
        _env_file=None,
        mt5_backend="real",
        paper_trading=False,
        demo_mode=False,
        live_trading=True,
        telegram_bot_token="123:TEST-ONLY-NOT-A-REAL-TOKEN",
        telegram_owner_id=1,
    )
    clock, sdk = ManualClock(NOW), None
    sdk = FakeSDK(clock)
    sdk.account.trade_mode = 2
    authority = FixtureAuthority(cfg, clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(TradingDisabled, match="owner"):
            await client.open_market_buy(order(sl=D("2609.90")))
        assert not sdk.requests


def test_unmarked_sdk_injection_cannot_accidentally_use_native_module():
    with pytest.raises(TradingDisabled, match="marked test SDK"):
        MT5Client(demo(), sdk=object())


async def test_timeout_during_order_check_is_reported_unsent_when_worker_drains():
    cfg, clock = demo(mt5_api_timeout_seconds=0.08), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    entered, release = Event(), Event()

    def slow_check():
        entered.set()
        release.wait(2)

    sdk.check_hook = slow_check
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(UncertainExecution):
            await task
        release.set()
        assert await asyncio.to_thread(authority.result_event.wait, 1)
        assert authority.latest == ResultStatus.REJECTED and not sdk.requests
        assert client.health()["writes_quarantined"]
    finally:
        release.set()
        await client.shutdown()


async def test_native_close_boundary_uses_next_open_and_never_peeks_at_long_session_bar():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    hours = (NOW - timedelta(hours=3), NOW - timedelta(hours=2), NOW - timedelta(minutes=30), NOW)
    rows = [
        dict(
            time=int(instant.timestamp()),
            open=2610.0,
            high=2611.0,
            low=2609.0,
            close=2610.5,
            tick_volume=100,
            spread=20,
            real_volume=0,
        )
        for instant in hours
    ]
    sdk.copy_rates_from_pos = lambda symbol, tf, start, count: rows[-count:]
    sdk.copy_rates_from = lambda symbol, tf, cutoff, count: [
        row for row in rows if row["time"] <= cutoff.timestamp()
    ][-count:]
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        frame = await client.get_candles("XAUUSD", "H1", 3)
        assert frame.iloc[1].close_time.to_pydatetime() == NOW - timedelta(minutes=30)
        historical = await client.get_candles("XAUUSD", "H1", 3, as_of=NOW - timedelta(minutes=50))
        assert len(historical) == 1
        assert historical.iloc[0].time.to_pydatetime() == hours[0]
```

### `tests/test_trading_diagnostics.py`

```python
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_mock import run

ROOT = Path(__file__).resolve().parents[1]


async def test_smoke_is_synthetic_balanced_and_not_promotion_evidence(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("MT5_BACKEND", "real")
    monkeypatch.setenv("ACCOUNT_CURRENCY", "USC")
    monkeypatch.setenv("MT5_PASSWORD", "do-not-read-this-in-a-mock-smoke")
    report = await run()
    assert report["source"] == "synthetic" and report["simulated_only"]
    assert not report["eligible_stage_evidence"] and report["real_orders_sent"] == 0
    assert report["positions"] == 0 and report["deals"] == 2
    assert report["finalized_candles"] == 300


def test_smoke_module_cli_does_not_touch_db_or_native_sdk(tmp_path):
    before = {path.name for path in tmp_path.iterdir()}
    environment = dict(os.environ, PYTHONPATH=str(ROOT), LIVE_TRADING="true", MT5_BACKEND="real")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_mock"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["source"] == "synthetic" and report["duplicate_open_sent_once"]
    assert before == {path.name for path in tmp_path.iterdir()}


def test_readonly_diagnostic_cannot_connect_native_on_linux():
    if sys.platform == "win32":
        return  # No automatic native connection is allowed even in Windows tests.
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_mt5_readonly", "--env-file", ".env.example"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["error"] == "ConnectionUnavailable" and report["real_orders_sent"] == 0
```

### `core/__init__.py`

```python
"""Safety-critical shared infrastructure. Imports have no runtime side effects."""

__version__ = "0.2.0"
```

### `core/settings.py`

```python
"""Immutable, validated configuration. Environment flags never authorize orders."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.security import sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
Provider = Literal["ollama", "openai", "disabled"]


class FrozenDict(dict):
    """Read-only configuration maps; values are also immutable scalars/tuples."""

    def _deny(self, *args: object, **kwargs: object) -> None:
        raise TypeError("configuration maps are read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny


class OperatingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


def _valid_url(value: str, *, allow_local_http: bool = False, https_only: bool = False) -> str:
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme not in allowed or not parsed.hostname:
        raise ValueError("a valid HTTP(S) URL is required")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL credentials and fragments are forbidden")
    if allow_local_http and parsed.scheme == "http" and not local:
        raise ValueError("non-loopback AI endpoints must use HTTPS")
    return value.rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
        populate_by_name=True,
    )

    # Trusted local/test override; defaults to the checked-out repository.
    project_root: Path = Field(default=PROJECT_ROOT, exclude=True)
    backtest_mode: bool = False
    demo_mode: bool = True
    live_trading: bool = False
    paper_trading: bool = True
    start_paused: bool = True
    mt5_backend: Literal["mock", "real"] = "mock"
    mt5_terminal_path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"
    mt5_login: int | None = Field(default=None, gt=0)
    mt5_password: SecretStr = SecretStr("")
    mt5_server: str = ""
    mt5_magic_number: int = Field(default=20260217, gt=0, le=2**31 - 1)
    mt5_connect_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    mt5_reconnect_attempts: int = Field(default=3, ge=1, le=5)
    mt5_reconnect_backoff_seconds: int = Field(default=5, ge=1, le=60)
    max_tick_age_seconds: int = Field(default=10, ge=1, le=60)
    max_candle_age_seconds: int = Field(default=420, ge=60, le=86400)
    mt5_api_timeout_seconds: float = Field(default=45, gt=0, le=90)
    order_max_age_seconds: int = Field(default=30, ge=1, le=60)
    paper_initial_balance: Decimal = Field(default=Decimal("1000"), gt=0, le=Decimal("1000000000"))
    paper_slippage_points: int = Field(default=2, ge=0, le=1000)
    mock_leverage: int = Field(default=100, ge=1, le=1000)

    account_currency: str = "USD"
    max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    live_max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    max_total_open_risk_percent: Decimal = Field(default=Decimal("1.5"), gt=0, le=5)
    max_daily_loss_percent: Decimal = Field(default=Decimal("3"), gt=0, le=10)
    max_drawdown_percent: Decimal = Field(default=Decimal("10"), gt=0, le=25)
    max_margin_usage_percent: Decimal = Field(default=Decimal("30"), gt=0, le=50)
    max_daily_trades: int = Field(default=12, ge=1, le=50)
    min_daily_trades_target: int = Field(default=6, ge=0, le=50)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    max_same_symbol_positions: int = Field(default=1, ge=1, le=1)
    max_spread_points: int = Field(default=35, ge=0, le=100000)
    symbol_spread_limits: dict[str, int] = Field(
        default_factory=dict, validation_alias="SYMBOL_SPREAD_LIMITS_JSON"
    )
    max_slippage_points: int = Field(default=10, ge=0, le=1000)
    min_signal_score: float = Field(default=70, ge=50, le=100)
    require_stop_loss: bool = True
    min_net_reward_risk: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    trading_day_timezone: str = "UTC"

    target_profit_usd_per_trade: Decimal = Field(default=Decimal("5"), gt=0, le=10000)
    use_dynamic_target: bool = True
    target_r_multiple: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    commission_round_turn_usd_per_lot: Decimal = Field(default=Decimal("7"), ge=0, le=10000)
    estimated_swap_usd_per_lot_per_day: Decimal = Field(default=Decimal("0"), ge=0, le=10000)
    symbols: Annotated[tuple[str, ...], NoDecode] = ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    symbol_aliases: dict[str, str] = Field(default_factory=dict, validation_alias="SYMBOL_ALIASES_JSON")
    symbol_news_currencies: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "XAUUSD": ["USD"],
            "BTCUSD": ["USD"],
            "ETHUSD": ["USD"],
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "US30": ["USD"],
            "NAS100": ["USD"],
        },
        validation_alias="SYMBOL_NEWS_CURRENCIES_JSON",
    )
    account_to_usd_symbols: dict[str, str] = Field(
        default_factory=dict, validation_alias="ACCOUNT_TO_USD_SYMBOLS_JSON"
    )
    primary_timeframe: Timeframe = "M5"
    higher_timeframe: Timeframe = "M15"
    trend_timeframe: Timeframe = "H1"
    candle_lookback: int = Field(default=300, ge=200, le=5000)

    trailing_levels: Annotated[tuple[tuple[float, float], ...], NoDecode] = ((30, 30), (60, 60), (90, 90))
    trailing_spread_buffer_points: int = Field(default=2, ge=0, le=1000)
    atr_trailing_enabled: bool = True
    atr_trailing_multiplier: Decimal = Field(default=Decimal("1.5"), ge=1, le=5)
    allow_tp_extension: bool = False
    tp_extension_factor: Decimal = Field(default=Decimal("1.2"), ge=1, le=1.5)

    telegram_bot_token: SecretStr = SecretStr("")
    telegram_owner_id: int | None = Field(default=None, gt=0)
    telegram_miniapp_url: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    telegram_initdata_max_age_seconds: int = Field(default=300, ge=30, le=600)
    telegram_use_webhook: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1024, le=65535)
    api_rate_limit_per_minute: int = Field(default=60, ge=5, le=300)
    api_max_request_bytes: int = Field(default=16384, ge=1024, le=65536)
    api_trusted_hosts: tuple[str, ...] = Field(
        default=("127.0.0.1", "localhost"), validation_alias="API_TRUSTED_HOSTS_JSON"
    )
    api_cors_origins: tuple[str, ...] = Field(default=(), validation_alias="API_CORS_ORIGINS_JSON")

    ai_provider: Provider = "ollama"
    ai_fallback_provider: Provider = "openai"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    allow_remote_ollama: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    ai_confidence_threshold: float = Field(default=70, ge=50, le=100)
    ai_timeout_seconds: int = Field(default=12, ge=1, le=60)
    ai_max_concurrent: int = Field(default=2, ge=1, le=4)
    ai_position_review_seconds: int = Field(default=300, ge=60, le=3600)
    auto_reduce_risk: bool = False
    auto_adapt_strategy_weights: bool = False
    max_strategy_weight_step: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.02"))

    news_api_key: SecretStr = SecretStr("")
    finnhub_api_key: SecretStr = SecretStr("")
    cryptopanic_api_key: SecretStr = SecretStr("")
    use_free_news_sources: bool = True
    use_rss: bool = True
    use_economic_calendar: bool = True
    block_trading_high_impact_news: bool = True
    news_impact_threshold: Literal["medium", "high"] = "high"
    news_required_for_entry: bool = True
    rss_urls: tuple[str, ...] = Field(default=(), validation_alias="RSS_URLS_JSON")
    calendar_source_url: str = ""
    calendar_file: Path = Path("data/news/calendar.json")
    news_poll_seconds: int = Field(default=180, ge=30, le=900)
    news_max_age_seconds: int = Field(default=900, ge=60, le=3600)
    calendar_max_age_seconds: int = Field(default=21600, ge=60, le=86400)
    news_pre_event_minutes: int = Field(default=30, ge=15, le=120)
    news_post_event_minutes: int = Field(default=15, ge=5, le=120)

    require_stage_gates: bool = True
    model_min_labelled_trades: int = Field(default=300, ge=100, le=100000)
    model_walk_forward_folds: int = Field(default=5, ge=3, le=20)
    model_label_horizon_bars: int = Field(default=12, ge=1, le=1000)
    model_embargo_bars: int = Field(default=12, ge=1, le=2000)
    stage_min_paper_days: int = Field(default=14, ge=7, le=365)
    stage_min_demo_days: int = Field(default=14, ge=7, le=365)
    stage_min_trades: int = Field(default=100, ge=50, le=10000)
    stage_min_profit_factor: Decimal = Field(default=Decimal("1.1"), gt=1, le=5)
    stage_max_drawdown_percent: Decimal = Field(default=Decimal("5"), gt=0, le=10)
    live_approval_ttl_seconds: int = Field(default=3600, ge=60, le=86400)

    database_url: SecretStr = SecretStr("sqlite:///data/reflexbot.db")
    database_busy_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    signal_interval_seconds: int = Field(default=30, ge=5, le=300)
    position_interval_seconds: int = Field(default=5, ge=1, le=30)
    heartbeat_interval_seconds: int = Field(default=10, ge=5, le=30)
    watchdog_interval_seconds: int = Field(default=30, ge=15, le=120)
    watchdog_stale_seconds: int = Field(default=90, ge=30, le=600)
    watchdog_max_restarts_per_hour: int = Field(default=3, ge=1, le=5)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Path = Path("data/logs/bot.log")
    log_max_bytes: int = Field(default=5242880, ge=1048576, le=52428800)
    log_backup_count: int = Field(default=5, ge=1, le=20)
    data_dir: Path = Path("data")
    backup_dir: Path = Path("data/backups")

    @field_validator("mt5_login", "telegram_owner_id", mode="before")
    @classmethod
    def empty_int_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = json.loads(value) if value.lstrip().startswith("[") else value.split(",")
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one symbol is required")
        symbols = tuple(str(item).strip() for item in value)
        if len(symbols) > 30 or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique; maximum 30")
        if any(not re.fullmatch(r"[A-Za-z0-9_.#-]{1,64}", item) for item in symbols):
            raise ValueError("invalid symbol name")
        return symbols

    @field_validator("trailing_levels", mode="before")
    @classmethod
    def parse_trailing_levels(cls, value: object) -> tuple[tuple[float, float], ...]:
        if isinstance(value, str):
            value = [item.split(":") for item in value.split(",")]
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one trailing level is required")
        try:
            levels = tuple((float(trigger), float(lock)) for trigger, lock in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("use trigger:lock pairs") from exc
        if any(not 0 < lock <= trigger <= 100 for trigger, lock in levels):
            raise ValueError("each level must satisfy 0 < lock <= trigger <= 100")
        if any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(levels, levels[1:], strict=False)):
            raise ValueError("trailing triggers and locks must increase strictly")
        return levels

    @field_validator("account_currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3,8}", value):
            raise ValueError("invalid account currency code")
        return value

    @field_validator("trading_day_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def safety_invariants(self) -> Self:
        if not self.start_paused or not self.require_stop_loss or not self.require_stage_gates:
            raise ValueError("startup pause, broker SL and stage gates cannot be disabled")
        if self.live_trading:
            if self.demo_mode or self.paper_trading or self.backtest_mode or self.mt5_backend != "real":
                raise ValueError(
                    "live requires DEMO_MODE=false, PAPER_TRADING=false, BACKTEST_MODE=false and real backend"
                )
            if not self.telegram_bot_token.get_secret_value() or not self.telegram_owner_id:
                raise ValueError("live requires owner-only Telegram controls")
            if not (
                self.news_required_for_entry
                and self.use_economic_calendar
                and self.block_trading_high_impact_news
            ):
                raise ValueError("live requires fresh news, calendar and high-impact blocking")
        elif self.backtest_mode:
            if self.paper_trading or not self.demo_mode or self.mt5_backend != "mock":
                raise ValueError("backtest requires PAPER_TRADING=false, DEMO_MODE=true and mock backend")
        elif self.paper_trading:
            if not self.demo_mode:
                raise ValueError("paper requires DEMO_MODE=true")
        elif not self.demo_mode or self.mt5_backend != "real":
            raise ValueError("broker demo requires DEMO_MODE=true and real backend")
        if self.mode in {OperatingMode.DEMO, OperatingMode.LIVE} and self.ai_provider == "disabled":
            raise ValueError("broker execution cannot bypass AI confidence gating")
        if self.live_max_risk_percent_per_trade > self.max_risk_percent_per_trade:
            raise ValueError("live risk cap cannot exceed the account risk cap")
        if self.max_risk_percent_per_trade > self.max_daily_loss_percent:
            raise ValueError("trade risk cannot exceed daily loss cap")
        if self.max_total_open_risk_percent > self.max_drawdown_percent:
            raise ValueError("open risk cannot exceed drawdown cap")
        if self.max_daily_loss_percent > self.max_drawdown_percent:
            raise ValueError("daily loss cap cannot exceed drawdown cap")
        if self.paper_slippage_points > self.max_slippage_points:
            raise ValueError("simulated slippage must fit the configured nominal slippage budget")
        if self.min_daily_trades_target > self.max_daily_trades:
            raise ValueError("trade-count target cannot exceed daily limit")
        if self.target_r_multiple < self.min_net_reward_risk:
            raise ValueError("dynamic target multiple cannot bypass the net reward/risk floor")
        if TIMEFRAME_MINUTES[self.higher_timeframe] <= TIMEFRAME_MINUTES[self.primary_timeframe]:
            raise ValueError("higher timeframe must exceed primary timeframe")
        if TIMEFRAME_MINUTES[self.trend_timeframe] < TIMEFRAME_MINUTES[self.higher_timeframe]:
            raise ValueError("trend timeframe must be at least the higher timeframe")
        if self.model_embargo_bars < self.model_label_horizon_bars:
            raise ValueError("validation embargo must cover the full label horizon")
        if self.watchdog_stale_seconds < 3 * self.heartbeat_interval_seconds:
            raise ValueError("watchdog staleness must allow at least three heartbeats")
        credentials = (
            bool(self.mt5_login),
            bool(self.mt5_password.get_secret_value()),
            bool(self.mt5_server),
        )
        if any(credentials) and not all(credentials):
            raise ValueError("supply all MT5 login/password/server values or leave all empty")
        if bool(self.telegram_bot_token.get_secret_value()) != bool(self.telegram_owner_id):
            raise ValueError("Telegram token and owner ID must be configured together")
        for url in (self.telegram_miniapp_url, self.telegram_webhook_url):
            if url:
                _valid_url(url, https_only=True)
        if self.telegram_use_webhook and not (
            self.telegram_webhook_url and self.telegram_webhook_secret.get_secret_value()
        ):
            raise ValueError("webhook mode requires an HTTPS URL and webhook secret")
        _valid_url(self.ollama_base_url)
        if not self.allow_remote_ollama and urlparse(self.ollama_base_url).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("remote Ollama is disabled")
        if self.allow_remote_ollama:
            _valid_url(self.ollama_base_url, allow_local_http=True)
        _valid_url(self.openai_base_url, allow_local_http=True)
        for url in self.rss_urls + self.api_cors_origins:
            _valid_url(url, https_only=True)
        if self.calendar_source_url:
            _valid_url(self.calendar_source_url, https_only=True)
        if not self.api_trusted_hosts or "*" in self.api_trusted_hosts:
            raise ValueError("explicit API trusted hosts are required; wildcard is forbidden")
        for symbol, limit in self.symbol_spread_limits.items():
            if not symbol or not 0 <= limit <= 100000:
                raise ValueError("invalid per-symbol spread limit")
        for name, alias in self.symbol_aliases.items():
            # Native broker names may contain spaces/punctuation. Logical names
            # remain filename-safe; aliases are passed only as literal API values.
            if (
                name not in self.symbols
                or not alias.strip()
                or len(alias) > 64
                or any(ord(char) < 32 or ord(char) == 127 for char in alias)
            ):
                raise ValueError("aliases require an enabled logical symbol and valid broker name")
        resolved = tuple(self.symbol_aliases.get(symbol, symbol) for symbol in self.symbols)
        if len(set(resolved)) != len(resolved):
            raise ValueError("two logical symbols cannot resolve to the same broker symbol")
        for currencies in self.symbol_news_currencies.values():
            if not currencies or any(not re.fullmatch(r"[A-Z]{3}", item) for item in currencies):
                raise ValueError("news exposure requires ISO currency codes")
        for path in (self.data_dir, self.backup_dir, self.log_file, self.calendar_file):
            self.resolve_path(path)
        for name in (
            "symbol_spread_limits",
            "symbol_aliases",
            "symbol_news_currencies",
            "account_to_usd_symbols",
        ):
            object.__setattr__(self, name, FrozenDict(getattr(self, name)))
        return self

    @property
    def mode(self) -> OperatingMode:
        if self.live_trading:
            return OperatingMode.LIVE
        if self.backtest_mode:
            return OperatingMode.BACKTEST
        if self.paper_trading:
            return OperatingMode.PAPER
        return OperatingMode.DEMO

    @property
    def effective_risk_percent(self) -> Decimal:
        return (
            min(self.max_risk_percent_per_trade, self.live_max_risk_percent_per_trade)
            if self.mode == OperatingMode.LIVE
            else self.max_risk_percent_per_trade
        )

    def resolve_path(self, path: str | Path) -> Path:
        root = self.project_root.resolve()
        candidate = (root / Path(path)).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("runtime paths must remain inside the project directory")
        return candidate

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.backup_dir, self.log_file.parent, self.calendar_file.parent):
            self.resolve_path(path).mkdir(parents=True, exist_ok=True)
        for child in ("candles", "news", "models", "logs", "backups"):
            (self.resolve_path(self.data_dir) / child).mkdir(parents=True, exist_ok=True)

    def safety_snapshot(self) -> dict[str, object]:
        # Include all strategy/risk/provider settings, exclude credentials and
        # infrastructure-only fields. Effective runtime overrides must be hashed too.
        infrastructure = {
            "project_root",
            "database_url",
            "data_dir",
            "backup_dir",
            "log_file",
            "log_level",
            "log_max_bytes",
            "log_backup_count",
            "api_host",
            "api_port",
            "api_trusted_hosts",
            "api_cors_origins",
            "telegram_miniapp_url",
            "telegram_webhook_url",
            "telegram_use_webhook",
            "mt5_terminal_path",
            "database_busy_timeout_ms",
        }
        values = self.model_dump(mode="json")
        excluded = infrastructure | {
            name for name in type(self).model_fields if isinstance(getattr(self, name), SecretStr)
        }
        return {
            **{key: value for key, value in values.items() if key not in excluded},
            "mode": self.mode.value,
        }

    def safety_fingerprint(self) -> str:
        return sha256_json(self.safety_snapshot())

    def strategy_fingerprint(self) -> str:
        # Stage evidence can span paper/demo/live and different account logins.
        # Owner execution approvals use safety_fingerprint PLUS actual account/session.
        snapshot = self.safety_snapshot()
        for name in (
            "mode",
            "backtest_mode",
            "demo_mode",
            "live_trading",
            "paper_trading",
            "mt5_backend",
            "mt5_login",
            "mt5_server",
            "telegram_owner_id",
        ):
            snapshot.pop(name, None)
        return sha256_json(snapshot)

    def public_config(self) -> dict[str, object]:
        # Strict allowlist. Never expose model_dump() to an API or to logs.
        return {
            "mode": self.mode.value,
            "backend": self.mt5_backend,
            "startup": "paused",
            "symbols": list(self.symbols),
            "risk_percent": str(self.max_risk_percent_per_trade),
            "live_risk_percent": str(self.live_max_risk_percent_per_trade),
            "effective_risk_percent": str(self.effective_risk_percent),
            "daily_loss_percent": str(self.max_daily_loss_percent),
            "drawdown_percent": str(self.max_drawdown_percent),
            "max_daily_trades": self.max_daily_trades,
            "min_daily_trades_target": self.min_daily_trades_target,
            "max_open_positions": self.max_open_positions,
            "target_profit_usd": str(self.target_profit_usd_per_trade),
            "min_net_reward_risk": str(self.min_net_reward_risk),
            "timeframes": [self.primary_timeframe, self.higher_timeframe, self.trend_timeframe],
            "trailing_levels": self.trailing_levels,
            "news_required": self.news_required_for_entry,
            "telegram_configured": bool(self.telegram_bot_token.get_secret_value()),
            "ai_provider": self.ai_provider,
            "config_fingerprint": self.safety_fingerprint(),
            "strategy_fingerprint": self.strategy_fingerprint(),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

### `.env.example`

```dotenv
# MT5 AI ReflexBot — fail-closed defaults. Copy to .env; never commit .env.
# MODE: four mutually exclusive outcomes (see README mode matrix)
BACKTEST_MODE=false
DEMO_MODE=true
LIVE_TRADING=false
PAPER_TRADING=true
START_PAUSED=true
MT5_BACKEND=mock

# MT5: real integration runs on Windows in the logged-on user's session.
MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe
# Leave ALL THREE empty to attach to the terminal's existing login.
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_MAGIC_NUMBER=20260217
MT5_CONNECT_TIMEOUT_MS=10000
MT5_RECONNECT_ATTEMPTS=3
MT5_RECONNECT_BACKOFF_SECONDS=5
MAX_TICK_AGE_SECONDS=10
MAX_CANDLE_AGE_SECONDS=420
MT5_API_TIMEOUT_SECONDS=45
ORDER_MAX_AGE_SECONDS=30

# SIMULATION: synthetic/paper capital is NOT the actual broker balance.
PAPER_INITIAL_BALANCE=1000
PAPER_SLIPPAGE_POINTS=2
MOCK_LEVERAGE=100

# ACCOUNT RISK: percentages are percentage points, not fractions.
ACCOUNT_CURRENCY=USD
MAX_RISK_PERCENT_PER_TRADE=0.5
LIVE_MAX_RISK_PERCENT_PER_TRADE=0.1
MAX_TOTAL_OPEN_RISK_PERCENT=1.5
MAX_DAILY_LOSS_PERCENT=3
MAX_DRAWDOWN_PERCENT=10
MAX_MARGIN_USAGE_PERCENT=30
MAX_DAILY_TRADES=12
MIN_DAILY_TRADES_TARGET=6
MAX_OPEN_POSITIONS=3
MAX_SAME_SYMBOL_POSITIONS=1
MAX_SPREAD_POINTS=35
# Broker POINTS, not pips. Empty means use the conservative global limit.
SYMBOL_SPREAD_LIMITS_JSON={}
MAX_SLIPPAGE_POINTS=10
MIN_SIGNAL_SCORE=70
REQUIRE_STOP_LOSS=true
MIN_NET_REWARD_RISK=1.1
TRADING_DAY_TIMEZONE=UTC

# PROFIT TARGET: an objective, not guaranteed profit or a sizing instruction.
TARGET_PROFIT_USD_PER_TRADE=5
USE_DYNAMIC_TARGET=true
TARGET_R_MULTIPLE=1.1
# Replace these estimates with your broker's actual costs before evaluation.
COMMISSION_ROUND_TURN_USD_PER_LOT=7
ESTIMATED_SWAP_USD_PER_LOT_PER_DAY=0

# TRADING SYMBOLS: logical names; case-sensitive broker aliases are supported.
SYMBOLS=XAUUSD,BTCUSD,EURUSD,GBPUSD,ETHUSD
SYMBOL_ALIASES_JSON={}
# Add entries for custom symbols. Unknown news exposure blocks new entries.
SYMBOL_NEWS_CURRENCIES_JSON={"XAUUSD":["USD"],"BTCUSD":["USD"],"ETHUSD":["USD"],"EURUSD":["EUR","USD"],"GBPUSD":["GBP","USD"],"US30":["USD"],"NAS100":["USD"]}
# Used only when account currency is not USD; broker-specific names required.
ACCOUNT_TO_USD_SYMBOLS_JSON={}

# TIMEFRAMES: closed bars only; H1 bias is aligned as-of the decision time.
PRIMARY_TIMEFRAME=M5
HIGHER_TIMEFRAME=M15
TREND_TIMEFRAME=H1
CANDLE_LOOKBACK=300

# PROFIT-LOCK TRAILING: trigger percent : desired locked target percent.
TRAILING_LEVELS=30:30,60:60,90:90
TRAILING_SPREAD_BUFFER_POINTS=2
ATR_TRAILING_ENABLED=true
ATR_TRAILING_MULTIPLIER=1.5
ALLOW_TP_EXTENSION=false
TP_EXTENSION_FACTOR=1.2

# TELEGRAM: configure BOTH token and owner ID, or leave both empty.
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_ID=
TELEGRAM_MINIAPP_URL=
TELEGRAM_WEBHOOK_URL=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_INITDATA_MAX_AGE_SECONDS=300
# Polling is default; webhook and polling must never run simultaneously.
TELEGRAM_USE_WEBHOOK=false

# API: loopback default; expose only through a TLS reverse proxy/tunnel.
API_HOST=127.0.0.1
API_PORT=8000
API_RATE_LIMIT_PER_MINUTE=60
API_MAX_REQUEST_BYTES=16384
API_TRUSTED_HOSTS_JSON=["127.0.0.1","localhost"]
# An empty allowlist means no cross-origin browser API access.
API_CORS_ORIGINS_JSON=[]

# AI: absence, timeout or invalid JSON => reject new entries, not approve.
AI_PROVIDER=ollama
AI_FALLBACK_PROVIDER=openai
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
ALLOW_REMOTE_OLLAMA=false
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
AI_CONFIDENCE_THRESHOLD=70
AI_TIMEOUT_SECONDS=12
AI_MAX_CONCURRENT=2
AI_POSITION_REVIEW_SECONDS=300
AUTO_REDUCE_RISK=false
AUTO_ADAPT_STRATEGY_WEIGHTS=false
MAX_STRATEGY_WEIGHT_STEP=0.02

# NEWS: headlines are NOT a substitute for an economic calendar.
NEWS_API_KEY=
FINNHUB_API_KEY=
CRYPTOPANIC_API_KEY=
USE_FREE_NEWS_SOURCES=true
USE_RSS=true
USE_ECONOMIC_CALENDAR=true
BLOCK_TRADING_HIGH_IMPACT_NEWS=true
NEWS_IMPACT_THRESHOLD=high
NEWS_REQUIRED_FOR_ENTRY=true
# Supply feeds you are entitled to use; unavailable feeds stay unavailable.
RSS_URLS_JSON=[]
CALENDAR_SOURCE_URL=
CALENDAR_FILE=data/news/calendar.json
NEWS_POLL_SECONDS=180
NEWS_MAX_AGE_SECONDS=900
CALENDAR_MAX_AGE_SECONDS=21600
NEWS_PRE_EVENT_MINUTES=30
NEWS_POST_EVENT_MINUTES=15

# LEARNING + PROMOTION: owner approval is still required after these gates.
REQUIRE_STAGE_GATES=true
MODEL_MIN_LABELLED_TRADES=300
MODEL_WALK_FORWARD_FOLDS=5
MODEL_LABEL_HORIZON_BARS=12
MODEL_EMBARGO_BARS=12
STAGE_MIN_PAPER_DAYS=14
STAGE_MIN_DEMO_DAYS=14
STAGE_MIN_TRADES=100
STAGE_MIN_PROFIT_FACTOR=1.1
STAGE_MAX_DRAWDOWN_PERCENT=5
LIVE_APPROVAL_TTL_SECONDS=3600

# DATABASE: PostgreSQL requires the optional psycopg driver.
DATABASE_URL=sqlite:///data/reflexbot.db
# Example: postgresql+psycopg://reflexbot:password@localhost:5432/reflexbot
DATABASE_BUSY_TIMEOUT_MS=10000

# SCHEDULER / WATCHDOG
SIGNAL_INTERVAL_SECONDS=30
POSITION_INTERVAL_SECONDS=5
HEARTBEAT_INTERVAL_SECONDS=10
WATCHDOG_INTERVAL_SECONDS=30
WATCHDOG_STALE_SECONDS=90
WATCHDOG_MAX_RESTARTS_PER_HOUR=3

# LOGGING / LOCAL FILES
LOG_LEVEL=INFO
LOG_FILE=data/logs/bot.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=5
DATA_DIR=data
BACKUP_DIR=data/backups
```

---

## Executed checks and limitations

# Executed validation — release 0.2.0 (Parts 1–4)

Date: 2026-10-02. Environment: Linux x64, CPython 3.13.14, isolated venv.
Windows x64 / Python 3.11 is the reference MT5 deployment and remains **untested**.

## Results

- **199 pytest tests passed**: 60 foundation + 139 Part 4 tests/parameter cases.
- Ruff 0.16.10 formatting/lint: passed.
- Python compileall: passed for core/trading/scripts/tests/CLI.
- Full Linux dependency installation and `pip check`: no broken requirements.
  Native MetaTrader5 was skipped by the Windows platform marker.
- Foundation `check-config`, `init-db`, `status` with `.env.example`: passed;
  paper/mock, schema 1, paused, kill=false, revision=0, heartbeat=null.
- Environment-isolated synthetic smoke: passed; 300 finalized candles, duplicate
  entry sent once, both deal legs reconciled, zero open positions.
- Read-only MT5 diagnostic on Linux: structured unsupported-platform error,
  exit code 2; no connection/order attempted.

Commands executed in the repository:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q core trading scripts tests main.py config.py
.venv/bin/python -m pip check
.venv/bin/python main.py --env-file .env.example check-config
.venv/bin/python main.py --env-file .env.example init-db
.venv/bin/python main.py --env-file .env.example status
.venv/bin/python -m scripts.smoke_mock
```

The ZIP is separately integrity-checked and tested from a clean extraction
using the installed venv, with the same test/config/smoke commands. Credentials,
runtime DB/WAL/logs/market data/models, caches and venv are excluded from it.
`requirements.linux.lock.txt` is a Linux environment freeze, not a Windows lock.

## Coverage actually exercised

Foundation:

- paper/mock/paused defaults and complete configuration example;
- conflicting modes, immutable maps, hard risk/trailing/embargo controls;
- owner/token pairing, TLS/trusted hosts, path boundaries, stable policy hashes;
- SQLite UTC/Decimal/schema/transaction behavior, persisted kill state;
- append-only audits and secret/URL/header/traceback redaction;
- nonfinite/deep JSON rejection and CLI non-trading behavior.

Part 4:

- Decimal/UTC financial contract, credit-conservative capital, stable maintenance
  request hashes, tick/lot grids and adverse rounded slippage;
- BUY/SELL sizing, below-minimum skip, native non-linear revaluation, margin cap,
  dynamic/fixed net reward/risk, bounded targets, off-grid weighted entries,
  SELL physical-price bound and explicit non-USD signed conversion;
- fresh/old/future quotes, owner symbols, spread/deviation/stop/freeze limits,
  no loosened/removed SL or removed TP, separately approved TP-extension bound;
- synthetic prefix-stable finalized candles and native conservative next-opening
  close boundaries, excluding forming/future data;
- marked equity, gap-aware SL/TP, opening/closing commission legs, swap and
  whole-position net balance reconciliation;
- concurrent duplicate simulation entry, no averaging, ownership and duplicate
  maintenance, daily caps/resets/carry-gap loss, persistent drawdown latch;
- snapshot restore of exposure/cache/counters, wrong source/account/config,
  inconsistent margins/targets/time/IDs, exact aliases/unknown news exposure;
- proof by source write-bombs that PaperMT5 never calls source trade methods;
- marked fake SDK thread serialization, leases, read reconnects/account pin,
  real-mode/default-deny rules, grant binding, definitely-unsent preflight/check
  outcomes, changed exposure/quote/equity/permissions/pending/expiry before send;
- filled/partial/accepted/rejected/unknown native acknowledgement classification,
  no guessed position identifiers, durable-callback failure quarantine;
- nonblocking timeout/cancellation, late acknowledgement observation, no automatic
  resend, read timeout, timeout during check (eventually definitely unsent),
  shutdown timeout and retained SDK lease until actual worker completion;
- isolated synthetic/read-only diagnostic CLIs and credential/mode isolation.

## What this does NOT verify

No native MetaTrader5 module/Windows terminal was imported or executed. No real
broker connection, demo/live order, credential login, withdrawals/password
changes, external AI/news/Telegram request, Mini App server, complete strategy
backtest, training/model promotion or unattended paper loop was exercised.
Native execution tests use explicitly marked **fake SDKs and test-only internal
authorities**; these fixtures cannot be treated as deployment approval.

The native write authority's durable database implementation, full risk engine,
profit-lock/ATR trailing, position manager and owner/staging controls are Part 5
and subsequent runtime work. Native writes still default to `DenyAllWrites`.
PostgreSQL server integration, broker-specific margin/financing/partial fills,
Windows dependency/runtime compatibility and vulnerability auditing are untested.

A package wheel was previously verified as available for MetaTrader5 5.0.6231
on Windows CPython 3.11 x64. Availability is not installation/runtime validation.
Synthetic positive PnL is deliberately engineered accounting evidence, **not**
profitability evidence. These results are not a security audit, live-readiness
certification or trading authorization. Mandatory backtest → paper → demo →
explicitly approved small-live progression remains in force.


## Next installment

Part 5: durable risk/execution authority, risk engine, profit-lock/ATR trailing
and position manager. Strategies/AI/news/Telegram/runtime/backtester follow
in the requested order. No live trading is enabled by this installment.

Say CONTINUE to generate the next part.
