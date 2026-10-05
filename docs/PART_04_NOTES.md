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
