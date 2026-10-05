# Part 5 — durable risk, execution, trailing and position ownership

**Release 0.3.0 · cumulative Parts 1–5 · database schema 2.**

This installment supplies runnable library implementations, not pseudocode.
The full source/configuration/tests are reproduced in `PART_05.md` and supplied
as ordinary repository files. Historical Parts 1–3 and Part 4 guides are kept
unchanged; use the **current repository** for code, defaults and migrations.

**This is still not the assembled trading bot.** There is no `main.py run`,
strategy router, AI/news provider integration, Telegram/Mini App or scheduler
installed yet. Native `MT5Client` remains **DenyAllWrites by default**. Only the
trusted `ExecutionEngine` composition explicitly attaches the durable authority.
Native entries additionally require persisted real-source signals, fresh AI/news
reviews, the chronological promotion chain and, for live, expiring owner approval.
Those producers are future Parts 6–11; an environment flag cannot replace them.
No real broker orders, account changes, withdrawals or deployment were authorized
or performed for this installment. Windows/PostgreSQL/native operation is untested.

## Implemented files

| File | Working responsibility |
|---|---|
| `trading/risk_types.py` | Validated internal decision/news/review DTOs; source/code/model binding and canonical context digests |
| `trading/runtime_state.py` | Persistent single-runtime lease; paused startup; audited owner pause/resume/kill/recovery and explicit flat-baseline review |
| `trading/stage_gate.py` | Artifact/dataset integrity; actual paper/demo fill/P&L/account-journal checks; code/model/policy/owner binding; live challenge hashing/expiry |
| `trading/risk_engine.py` | Fresh quality/risk vetoes; credit-excluded, cash-adjusted sampled peaks/daily limits; balance/ledger continuity; durable counters/reservations |
| `trading/execution_authority.py` | Unique staged intents, transactional pre-send permits, final revalidation, acknowledgement handling and uncertain-write halts |
| `trading/snapshots.py` | Current portfolio stop-loss risk and explicit signed USD conversion without recursive broker callbacks |
| `trading/state_store.py` | Bounded, checksummed, fsynced atomic shadow checkpoints; corrupt/missing-state refusal |
| `trading/execution.py` | Explicit durable composition; broker calls off event loop; restart-safe duplicates and reconciliation; no blind resends |
| `trading/trade_logger.py` | Exact order/deal-to-stable-position proof; complete volume/fee/swap/net accounting; ownership and maintenance verification |
| `trading/trailing_engine.py` | Achievable 30/60/90 net-USD locks; actual paid costs; closed-bar ATR; bounded reviewed 120% TP extension |
| `trading/position_manager.py` | Manage only DB-proved ownership; keep protections active while paused/killed; authenticated-owner close |
| `core/migrations.py` | Explicit stopped-runtime, backed-up file-SQLite schema 1 → 2 migration, preserving risk/kill latches |
| `scripts/smoke_risk.py` | Complete isolated synthetic risk/pause/fill/lock/restart/kill/close test; never promotion evidence |

Existing native/simulation adapters, authorization DTOs and order calculator are
extended rather than replaced with alternate incompatible implementations.

## Configuration additions

```dotenv
PAPER_STATE_FILE=data/paper/state.json
RUNTIME_LEASE_SECONDS=90
RISK_OBSERVATION_MAX_AGE_SECONDS=30
```

- The state path must remain inside the project root. Keep both the SQLite DB
  and the matching checkpoint; never reset one without the other.
- One `BotState` lease protects the **whole database**, not merely one process's
  Python lock. SQLite uses `BEGIN IMMEDIATE`; PostgreSQL uses a row lock.
- Each reconciliation/execution cycle renews the lease. It expires closed if
  the runtime stops heartbeating. Part 10 adds scheduled heartbeat/monitoring.
- An observation must be fresh to resume. A real-source observation gap over
  four times the observation-age setting requires explicit review.
- Defaults remain paper/mock/paused with empty broker/owner credentials.
- Configure broker costs, symbol aliases and signed currency routes before
  evaluating real data. Mock contract metadata is not a broker specification.

## The executable risk policy

An entry can proceed only when all applicable gates pass:

1. Correct schema, committed unique intent, valid runtime lease/session/config.
2. Owner has resumed entries; kill, recovery, daily-loss and drawdown latches clear.
3. Current account currency, equity/credit/margin and fresh quotes are verified.
4. Complete known baseline and balance continuity: previous balance plus signed
   balance cash flow plus whole realized ledger change must explain the new balance.
5. Closed, fresh bar; technical score ≥70; AI confidence ≥70 by default.
6. Headline freshness AND economic-calendar coverage are known and safe by default.
7. Enabled symbol, known currency/news exposure, spread/deviation/stops/lot grids.
8. Per-entry risk ≤0.5% capital (initial live ≤0.1%), aggregate ≤1.5%, remaining
   daily headroom adequate, margin ≤30%, ≤3 positions and no symbol averaging.
9. ≤12 accepted entries/day. Six is only an informational target; **zero is valid**.
10. Real-source entries bind the exact persisted approved Signal: timeframe,
    strategy, bar/decision times, source, scores, news digest, code/model and
    context digest. An already reserved/executed signal cannot issue another entry.
11. Required chronological stage evidence is valid, reviewed, unrevoked and
    artifact/dataset-bound; live confirmation binds account/config/code/model/session.

Risk remains a **nominal estimate**, not a maximum realized-loss guarantee.
Portfolio risk conservatively includes the greater of original loss-to-stop and
current marked-equity downside-to-stop, with estimated fees/slippage. FX uses
adverse liability rates, not unconditional USD parity. Credit is not new risk
capital. Unprotected, foreign, unmatched or incomplete exposure blocks new risk.

### Durable daily/peak accounting

- Trading day uses configured `TRADING_DAY_TIMEZONE` (UTC default).
- A new day carries the last observed effective equity **before** new marks,
  swap or gap exits. It does not reset the baseline to post-loss equity.
- Deposits/withdrawals are read-only ledger observations; the bot cannot initiate
  them. Signed known cash flows adjust baselines/high water, not trading profit.
- Fees on cash movements remain costs; credit is excluded from effective equity.
- An unexplained balance change is not accepted as a profit or a new high water.
  It halts entries and leaves the prior consistent anchor intact. Later complete
  evidence can resolve continuity, but an owner review is still required.
- Drawdown latches survive day changes/restarts. Daily latches may reset only at
  a new day; unresolved reservations never disappear with the daily counter reset.
- Peaks are **observed/sampled**, not a reconstruction of an account's lifetime
  history or proof no between-sample drawdown occurred.

## Execution lifecycle and duplicate behavior

```text
unique prepared intent
      ↓ locked transaction, all risk/owner/stage gates
submitting + reserved risk + counted entry COMMITTED
      ↓ native order_check
fresh quote/exposure/permit bounds + durable control revalidation
      ↓ exactly one order_send attempt
acknowledged / rejected / unknown
      ↓ positive broker deal + stable position evidence
reconciled ownership / closed trade / retained uncertainty
```

`order_check` success does not authorize `order_send` by itself. The native
adapter rechecks controls after it, so a pause/revocation/expired review occurring
while the SDK checks the order can still prevent sending. No transaction can
atomically exclude a subsequent broker/manual account race. A request already
in flight when pause/kill is received may still fill. Use a dedicated account,
server SL/TP and owner-supervised demo validation.

- Use immutable `OrderPlan`/`MarketOrder` and the same key for duplicate requests.
  A cached durable result is returned without a second broker call, even after
  restart or after the original position closes. A changed semantic payload
  with the same key is rejected.
- `open()` is a convenience that plans a fresh order; do **not** treat rebuilding
  its price/volume with an old key as an idempotent replay. Retain the original
  plan and call `execute(plan, context)` for duplicate result retrieval.
- No automatic retry of definitive or uncertain rejection. A new opportunity
  needs a fresh decision/plan/key, not repeated sends to satisfy a trade quota.
- Timeout/cancellation, unknown, accepted or partial status means reconcile and
  halt. Python cannot kill a native SDK thread. Reconnect never clears quarantine.
- Positively proven late acknowledgement cannot be downgraded by a timeout.
  Contradictory positive/rejected callbacks halt and retain the positive evidence.
- Native quarantine requires a fresh client/runtime after reconciliation and
  owner review. Entry approvals never override quarantine.

## What actually proves ownership

A broker order/deal number is **not** the current position ticket. The reconciler
uses exact acknowledged order/deal IDs and all matching entry legs, then the
broker's stable `position.identifier`, actual ticket, symbol, side, magic and
remaining volume. A bounded two-second time allowance handles SDK precision;
time/comment/magic alone never prove an unknown fill.

- UNKNOWN with no IDs/positive evidence remains halted. Empty history/position
  responses are never inferred to mean no fill and never trigger a resend.
- Partial exposure remains reserved until the complete requested volume is
  proven, or authoritative final order history confirms the remaining request
  cannot still fill. Final canceled/expired/rejected/filled order states are
  queried read-only. Known final partial fills bind their actual fill volume and
  proportional original target; the system does not double the next order.
- Unexpected netting additions/reversals invalidate ownership. No adoption by
  a bot-looking comment or matching magic. A legitimate rolled position ticket
  may update only under the same proven stable identifier and volume evidence.
- A closed trade needs all matching exit volume and positive exit legs. Absence
  alone does not fake a close. Manual/server SL exits of a previously owned trade
  are logged from the complete matching legs.
- Whole-trade NET includes all entry/exit profit, commission, fee and swap legs,
  including partial exits. It is not the gross closing-deal profit.
- Historical non-USD `profit_usd` is **unverified** until timed historical FX is
  available. The schema's zero is explicitly marked `profit_usd_verified=false`
  and `historical_fx_required=true`; never sum/report it as a valid USD return.
  Current FX can value a current stop estimate, not rewrite historical returns.

## Atomic paper checkpoint contract

Managed paper/backtest execution attaches `AtomicSnapshotStore` before broker
initialization. It fsyncs a temporary file, atomically replaces the checkpoint,
and fsyncs the directory where supported. Canonical checksum, size, duplicate
JSON keys, source, account, ledger and configuration are checked on restore.

All mutations are saved, including marking, swap, read-triggered SL/TP exits,
cache updates and day/counter transitions. The durable shadow ledger/cache is
saved **before** acknowledgement is recorded in SQL. If SQL fails after the
snapshot succeeds, restart can reconcile its exact durable cached result without
re-execution. If persistence fails/cancels, the instance is quarantined.

A missing checkpoint with prior DB state or a corrupt/mismatched checkpoint is
an error, not permission to reset paper capital. These hashes detect integrity
errors; they do not authenticate against an attacker controlling local files/DB.
Secure permissions/backups, dedicated credentials and external audits are needed.

Checkpoints deliberately do not substitute old prices for fresh market data.
A synthetic fixture must explicitly provide its resumed quotes at construction;
a real-data paper run obtains new native quotes. Stop-gap behavior remains real
simulation accounting and cannot be erased by resetting a quote or the ledger.

## Profit-lock and ATR algorithm

`target_profit_usd`, original TP, actual original fill volume and actual entry
price are bound once when entry is proven. Open price may be an off-grid VWAP;
only executable stops/targets are snapped to the broker's actual tick size.

```text
signed realized NET from all paid entry/partial-exit legs
  + current remaining swap
  − estimated closing fee
  + native gross P&L at adverse rounded stop exit
  → signed fresh FX → estimated net USD at the candidate stop
```

The solver uses this explicit account-currency net offset, so opening fees are
not charged twice. Current bid/ask, adverse exit slippage and the configured
point buffer are included. Existing spread is not double-counted as a cash fee.

1. Evaluate progress against the immutable original target.
2. Try highest earned 90/60/30 desired lock first.
3. Solve the sufficient integer-tick stop, verify nominal net USD and legal
   stop/freeze distance. Defer impossible tiers or use a lower feasible tier;
   **never clamp an SL then falsely claim the original tier**.
4. Optionally compare a fresh finalized-bar ATR(14) ×1.5 candidate. BUY SL only
   rises; SELL SL only falls. ATR cannot weaken an existing stop/profit lock.
5. Submit through the same durable authority. Claim a new lock level only after
   the actual broker protection is read back and its net-dollar estimate verified.
6. 120% TP extension is disabled by default. If enabled, require fresh high AI
   confidence, continued momentum, safe volatility/news, sufficient progress and
   a legally executable price within 1.2× original entry-to-TP price distance.
   If the dollar objective cannot fit that cap, skip extension; never loosen SL.

At exactly 30% profit, a 30% net lock may be mathematically unreachable behind
market prices once distances/costs are included. A deferred tier is correct.
A broker-confirmed lock remains an **estimate**, not guaranteed net proceeds;
gaps, spread/FX changes, fees, closed markets and rejects can beat the stop.

## Owner/control boundaries

`owner_id` is an internal verified principal, **never an HTTP JSON field or an
AI suggestion**. Part 9 supplies transport authentication/rate limits. Do not wire
these service methods to unauthenticated routes.

- `claim()` always starts paused; heartbeat/session/config are required for writes.
- `pause()` blocks new entries, not known protective modifications/closure.
- `kill()` is durable and similarly preserves protective work. It does not imply
  a guaranteed or automatically forced liquidation.
- `reset_kill()` requires explicit `RESET_KILL_AND_KEEP_PAUSED`, healthy verified
  state and fresh unquarantined broker runtime. It stays paused, never resumes.
- `resume()` cannot clear loss/kill/recovery/unsettled/baseline/continuity gates.
- `acknowledge_recovery()` clears only an owner-reviewed recovery error, stays
  paused and cannot clear unresolved executions or broker quarantine.
- `review_flat_baseline()` requires fresh complete history, **flat observed and
  reconciled** exposure and `REVIEW_SAMPLED_BASELINE`. It cannot clear daily or
  drawdown latches or classify unknown corrections as deposits. Unresolved cash
  corrections, unknown fills or a drawdown latch require investigation, not a
  new DB to bypass the safeguard.

Live enablement has a random nonce stored only as SHA256, single-use confirmation,
short expiry and account/config/code/model/session/evidence/owner binding.
Evidence revocation/change/expiry is rechecked on entry and immediately pre-send.
An expired live-entry confirmation does not deny improved SL/owned closure.

## Promotion checks versus future report producers

`StageGate` validates `reflex-stage-v1` report format, file checksum, historical
dataset checksum, metrics and chronological stages. Real-data paper/demo also
need actual DB intent/fill/net-account proofs and continuous code/model/source-
bound account observations. Synthetic/TEST_SDK reports cannot qualify.

No report producer or market evaluation is fabricated in this release. Part 11
adds backtesting and reviewed report registration; Part 7 adds trained model
registry checks. The current explicit rule-based baseline hash is **not** a
trained/evaluated model. Metrics/report integrity are necessary checks, not proof
of historical provider honesty, leak-free research or future profitability.

## Run the complete mock-only demonstration

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_risk
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
.venv/bin/python -m scripts.smoke_risk
.venv/bin/python -m pytest -q
```

The diagnostic uses a temporary DB/checkpoint and a clearly TEST-only synthetic
owner/context. It ignores `.env` and host mode/credentials, imports no native
SDK, starts no network service and deletes its temporary files. It tests a
startup veto, a new authorized synthetic intent, cached duplicate, 30% verified
lock, paused restart, cached result recovery, kill and explicit owned close.
Artificial result: 0.02 lot, risk 4.8600 within budget 5, original objective
5.346 USD, confirmed historical lock estimate ≥30%, and engineered net 2.84
account units. This is **NOT** a backtest or profitability/promotion evidence.

## Upgrade from Parts 1–4

See `MIGRATIONS.md` before using an existing schema-1 DB. Normal `init-db`/runtime
never silently alter it. Stop all processes first, preserve latches and matching
shadow state, apply the explicit migration and review any legacy unverified
baseline. Fresh installations initialize schema 2 directly.

Keep the default flags and use diagnostics now. The assembled scheduler, owner
transport, live news/AI and strategy report producers are later installments.
Do not run this library as unattended/native production trading yet.
