# Part 5 — Risk, trailing and position management

**MT5 AI ReflexBot · release 0.3.0 · Parts 1–5 · schema 2**

This is the complete runnable source installment, not pseudocode. Every Python
file in the current cumulative package is reproduced below, including the
updated native/paper adapters, foundation/configuration and all tests. The ZIP
contains these as ordinary editable project files. Historical source guides
remain unchanged; do not mix old settings/models with schema-2 runtime files.

**Validated:** 444 tests on Linux CPython 3.13.14, Ruff, compileall, dependency
consistency, backed-up SQLite migration and both synthetic diagnostics.
Windows/native broker and PostgreSQL operation remain untested. No terminal
login, real order, deployment or live approval was performed.

**Still safe by default:** paper/mock/paused; direct native client DENY ALL.
Durable authority is explicitly composed, never enabled by an environment flag.
The autonomous loop and real strategy/news/AI/owner transports are later parts.
Synthetic engineered gains and authored stage tests are NOT market evaluations.

## Safe commands now

```powershell
.\.venv\Scripts\python.exe main.py --env-file .env.example check-config
.\.venv\Scripts\python.exe -m scripts.smoke_mock
.\.venv\Scripts\python.exe -m scripts.smoke_risk
.\.venv\Scripts\python.exe -m pytest -q
```

Fresh DB: `main.py init-db`. Existing schema 1: read `MIGRATIONS.md`, stop
runtimes, preserve matched DB/checkpoint, then explicitly `main.py migrate-db`.
No `main.py run` is supplied yet. Do not use TEST helpers as production authority.

## Reading order

1. Contracts, risk policy, idempotency, ownership and achievable net locks.
2. Complete actual tree and migration/deployment limitations.
3. Full Part 5 code, then refreshed configuration/foundation/broker files/tests.
4. Exact validation scope and next installment.

## Part 5 contracts and usage

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

## Complete actual archive tree

```text
ACTUAL CUMULATIVE ARCHIVE TREE — release 0.3.0 / Parts 1–5 / schema 2

Only implemented files/empty directory markers are listed here.
Future modules: see TARGET_TREE.txt; .gitkeep is not an implementation.
Excluded: .env/secrets, runtime DB/WAL/checkpoints/data/logs/backups, venv and caches.
RELEASE_04_MANIFEST.json is historical; use RELEASE_05_MANIFEST.json for this archive.

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
  core/migrations.py
  core/models.py
  core/security.py
  core/settings.py
  data/backups/.gitkeep
  data/candles/.gitkeep
  data/logs/.gitkeep
  data/models/.gitkeep
  data/news/.gitkeep
  data/paper/.gitkeep
  docs/ARCHITECTURE.md
  docs/CURRENT_TREE.txt
  docs/MIGRATIONS.md
  docs/PARTS_01_03.md
  docs/PART_04.md
  docs/PART_04_NOTES.md
  docs/PART_05.md
  docs/PART_05_NOTES.md
  docs/RELEASE_04_MANIFEST.json
  docs/RELEASE_05_MANIFEST.json
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
  scripts/smoke_risk.py
  strategy/.gitkeep
  telegram_bot/.gitkeep
  tests/__init__.py
  tests/conftest.py
  tests/fake_mt5_sdk.py
  tests/risk_helpers.py
  tests/test_database.py
  tests/test_execution.py
  tests/test_foundation_cli.py
  tests/test_migrations.py
  tests/test_mt5_client.py
  tests/test_order_calculator.py
  tests/test_risk_concurrency.py
  tests/test_risk_contracts.py
  tests/test_risk_diagnostics.py
  tests/test_risk_engine.py
  tests/test_runtime_state.py
  tests/test_security_logging.py
  tests/test_settings.py
  tests/test_simulated_broker.py
  tests/test_stage_gate.py
  tests/test_stage_ledger.py
  tests/test_state_store.py
  tests/test_trade_logger.py
  tests/test_trading_contracts.py
  tests/test_trading_diagnostics.py
  tests/test_trailing_engine.py
  trading/.gitkeep
  trading/__init__.py
  trading/authorization.py
  trading/candles.py
  trading/client_helpers.py
  trading/currency.py
  trading/execution.py
  trading/execution_authority.py
  trading/mock_mt5.py
  trading/mt5_client.py
  trading/order_calculator.py
  trading/paper_mt5.py
  trading/position_manager.py
  trading/price_rules.py
  trading/risk_engine.py
  trading/risk_types.py
  trading/runtime_state.py
  trading/simulation.py
  trading/snapshots.py
  trading/stage_gate.py
  trading/state_store.py
  trading/symbol_manager.py
  trading/trade_logger.py
  trading/trailing_engine.py
  trading/types.py
```

## Explicit schema migration

# Schema migration — release 0.3.0 / schema 2

## Never migrate a running or uncertain broker runtime

Stop the bot, scheduler, watchdog and any other process opening the database.
Keep native server-side SL/TP in place. Review broker history and durable intents
first. The automated migration refuses running/fresh-leased state and intents in
`submitting`, `acknowledged` or `unknown`. Do not relabel these as rejected to make
the migration pass. Ask the owner to investigate/reconcile the actual execution.

Normal `init-db`, `status` and execution startup **do not** alter schema 1.
A fresh install runs `init-db` and creates schema 2 directly.

## Changes

- `risk_state.metadata_json`: durable last consistent balance/equity/observation,
  ledger cash/realized anchors, baseline verification and review/gap metadata.
- `account_snapshots.metadata_json`: captured credit, true data source,
  code/model and strategy hash for sampled risk/stage evidence.
- Existing money/ticket/control columns are not overloaded. Schema-1 tables,
  audit logs, risk/daily/drawdown/kill latches and reservations are preserved.
- Legacy risk baselines are explicitly **unverified** and require review before
  entries. Legacy account observations have no source/credit proof and cannot
  be used to manufacture qualifying real-data stage coverage.

## File SQLite: explicit backed-up upgrade

After stopping processes, allow the old runtime lease to expire. Keep a separate
protected, tested backup of **both** SQLite state and the matching paper checkpoint.
Do not copy a live SQLite DB while omitting its WAL. Do not reset paper capital.

```powershell
# Use the same .env/database path as the stopped runtime. No broker is connected.
.\.venv\Scripts\python.exe main.py --env-file .env migrate-db
.\.venv\Scripts\python.exe main.py --env-file .env status
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
.venv/bin/python main.py --env-file .env migrate-db
.venv/bin/python main.py --env-file .env status
```

The command:

1. Verifies all expected schema-1 table/column names and version.
2. Takes `BEGIN IMMEDIATE`, excluding concurrent reservation/control writers.
3. Checks stopped/stale runtime and no unresolved execution.
4. Uses SQLite's backup API for a consistent committed snapshot (including WAL).
5. Runs `PRAGMA integrity_check` on the backup and records its SHA256/path.
6. Adds the two JSON columns in the same migration transaction, flags legacy
   risk baselines unverified, updates version to 2 and appends an audit event.
7. Commits and verifies the new schema/append-only audit protections.

The backup is named `data/backups/schema1-<UTC>-<random>.sqlite` by default.
Protect it like the original database. An existing schema 2 is not migrated again.
SQLite in-memory and PostgreSQL are refused by this automated command.

## Baseline review after migration

Startup remains paused. First obtain current account/position/deal evidence with
the trusted execution runtime when installed. Owner service review is permitted
only with fresh complete history, flat observed/reconciled exposure, no unresolved
intent, no unknown cash correction and no daily/drawdown latch.

`RuntimeControl.review_flat_baseline(..., confirm="REVIEW_SAMPLED_BASELINE")`
is a trusted internal owner service, **not** a public unauthenticated command.
Part 9 supplies the verified owner transport. It acknowledges sampled history,
never invents a lifetime equity peak or clears a loss/kill latch. A fresh broker
client and separate recovery acknowledgement may be needed. Resume is a separate
owner operation and still cannot bypass stage/news/AI/live-confirmation gates.

Do not manually edit the DB/hash/counter to pass risk or promotion checks.
If the previous Part 4 explicit shadow snapshot exists, keep it protected;
Part 5 uses a stricter config fingerprint and automatic checkpoint composition.
There is no silent import/rebinding of an incompatible old shadow snapshot.
Investigate existing simulated exposure/ledger and use the matching old release
for controlled reconciliation before migrating. Never discard it to claim new
paper results. Fresh diagnostic fixtures are separate temporary ledgers only.

## PostgreSQL: reviewed operator procedure (not executed here)

PostgreSQL integration and migration must first be tested against your actual
server/version/roles on a restore. Stop runtimes, review execution states, take
`pg_dump` with a dedicated admin/migration role and verify a restore. Keep backup
credentials in protected local tooling, never in chat/logs. The restricted runtime
role must lack UPDATE/DELETE on `audit_logs`.

Equivalent schema changes, to be reviewed and applied **only to verified schema 1**:

```sql
BEGIN;
LOCK TABLE bot_state, order_intents, risk_state, account_snapshots IN ACCESS EXCLUSIVE MODE;
-- Verify schema_version=1, stale/stopped BotState and zero unresolved intents
-- with the operator's reviewed migration client BEFORE changing anything.
ALTER TABLE risk_state
  ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}';
ALTER TABLE account_snapshots
  ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}';
UPDATE risk_state
  SET metadata_json = '{"baseline_verified":false,"migration_review_required":true}';
UPDATE schema_version SET version = 2 WHERE id = 1;
-- Append an audit_logs INSERT binding operator review, backup hash and migration.
COMMIT;
```

These SQL statements are a reviewed-schema procedure, not an auto-safe script.
No PostgreSQL server/backup/rollback was exercised in this build. Verify column
sets, constraints, runtime-role grants, schema version and audit protections before
restarting paused. Do not claim PostgreSQL readiness from SQLite tests.

## Restore/rollback

A code downgrade cannot read schema 2 as schema 1. Stop all processes, restore
matching backed-up DB/checkpoint and the matching old code/config as a unit,
verify integrity and remain paused. A restore must not hide orders/deals that
occurred after the backup: reconcile them against the actual broker before any
new execution. Never auto-rollback broker execution or blindly replay intents.

## Complete target architecture

# MT5 AI ReflexBot — architecture and safety contract

## Implementation status

Current release: **Parts 1–5 (0.3.0 / schema 2)**. Settings, database, logging,
non-trading CLI, broker/paper adapters and durable execution/risk/owner/stage/
ownership/trailing/position services are implemented; 444 tests pass.
Native adapters default to `DenyAllWrites`; the trusted `ExecutionEngine` binds
its durable authority explicitly. Native entries still require persisted real
signals, reviewed evidence and fresh news/AI whose producers arrive later.
There is no autonomous trading loop or authenticated owner transport installed.
Parts 6–11 below are planned, not installed empty Python implementations.
This repository is not yet a completed or independently audited live system.
See `PART_05_NOTES.md`, `MIGRATIONS.md` and `PART_05.md` for actual contracts,
upgrade instructions and the complete current source.

## Goal

Evaluate multiple configured symbols on closed M5 bars with M15/H1 context.
Prefer selective, cost-aware opportunities, server-side protection and small
configurable targets. Six entries per day is an informational target; twelve
is the default hard entry cap. Zero trades is a valid day. AI is a veto/advisory
layer, not an execution authority. No profit, win rate, fill price or maximum
realized loss is guaranteed.

## Explicit professional assumptions

1. **Windows x64 and Python 3.11 x64** are the reference broker deployment.
   Linux/macOS development uses a deterministic mock. MT5 runs in an interactive
   logged-on Windows session, not a session-0 Windows service.
2. Default flags select **paper + mock**, with **startup paused**. Choosing a
   real data backend does not authorize real orders. Demo execution additionally
   requires MT5's actual account trade mode to be DEMO; contest/live accounts
   cannot masquerade as demo. Live requires actual REAL account mode, explicit
   live flags, staged evidence and an expiring owner approval.
3. Risk per trade defaults to 0.5% of conservative capital
   (`max(0, min(balance, equity-credit))`), **0.1% maximum for initial live
   operation**, 1.5% aggregate open risk, 3% daily equity loss and 10% peak-equity
   drawdown. These are entry/exit policy thresholds, not guarantees against gaps.
   The code-level per-entry ceiling is 1%; raising it requires code review and
   revalidation, not an AI suggestion.
4. One owned position per resolved broker symbol; no averaging, martingale,
   grids or recovery sizing. Unprotected or unaccounted-for foreign positions
   block new risk. Emergency closure defaults to bot-owned positions only.
5. All cash amounts distinguish **account currency** from **USD**. MT5
   `order_calc_profit` / `order_calc_margin` are the preferred broker valuation
   primitives. Validated conversion quotes are required for non-USD accounts;
   never silently relabel EUR/JPY/USC profit as dollars.
6. Profit targets are preferences. Default net reward/risk floor is 1.1. A
   requested $5 target must still cover costs and the stop risk; lot size is
   rounded DOWN, or the setup is skipped. 0.1R–0.5R targets are configurable only
   by explicitly lowering the owner's reward/risk floor; that knowingly permits
   individual losses larger than wins and requires higher net win rates. It
   cannot simultaneously guarantee 'losses smaller than wins'.
7. News and calendar data are separate. Missing/stale coverage is **unknown**,
   not low risk. RSS headlines alone cannot prove a safe economic-event window.
   The default empty feed/calendar configuration will block new entries when
   the trading engine is installed. No scraper is assumed to be reliable or
   entitled to access ForexFactory/Investing.com feeds.
8. AI unavailability, invalid JSON, an expired tick, missing features, unknown
   risk exposure, database failure or an uncertain order acknowledgement blocks
   new entries. AI confidence is a score, not a calibrated success probability.
9. Every restart starts paused. A persisted kill/drawdown latch is never cleared
   by a watchdog. Pausing entries does NOT disable protective position management.
10. Backtest -> paper -> demo -> small live is mandatory. Reports are bound to
    code/model/data hashes and a mode-independent strategy policy hash. Live
    approvals additionally bind the full configuration, actual broker account,
    process session, owner and expiry. No environment flag counts as approval.
    Default paper/demo gates each require at least 14 days and 100 closed trades,
    profit factor at least the configured floor and drawdown within the stage cap.
    Those are necessary checks, not proof of future profitability.
11. Stop loss, kill switch and flattening cannot guarantee a fill during a closed
    market, disconnection, gap or broker rejection. Keep native SL/TP on the server.
12. The bot never withdraws/transfers funds, changes broker passwords, executes
    AI-generated code, or grants the model shell/filesystem/trading capabilities.

## Mode matrix

| Mode | BACKTEST_MODE | DEMO_MODE | PAPER_TRADING | LIVE_TRADING | MT5_BACKEND |
|---|---|---|---|---|---|
| Backtest | true | true | false | false | mock |
| Paper, synthetic development data | false | true | true | false | mock |
| Paper, real MT5 market data | false | true | true | false | real |
| Broker demo | false | true | false | false | real |
| Broker live | false | false | false | true | real |

Invalid/ambiguous combinations raise a configuration error. Configuring a mode
is distinct from having permission to submit an order in that mode.

## Component/data flow

```text
Owner Telegram chat / Telegram WebApp
        | strict owner identity, WebApp HMAC + auth_date, request limits
        v
aiogram handlers + FastAPI API (one shared application runtime)
        | services, transactional approvals, no direct order_send route
        v
Persistent BotState / RiskState / audit ledger / stage evidence
        |
APScheduler -> finalized bars + fresh bid/ask + as-of calendar/news
        |
FeatureEngineering -> strategies -> StrategyRouter -> SignalEngine
        |                        technical score 0..100
        v
AI Supervisor (Ollama -> configured cloud fallback, strict JSON)
        |                        veto / bounded risk reduction / proposal
        v
RiskEngine (account identity, caps, exposure, spread, margin, news, mode)
        |
ExecutionEngine -> commit unique OrderIntent + reserve risk
        |
one serialized MT5 worker / RLock -> order_check -> order_send
        |
reconcile broker orders/deals/position identifiers -> trades + audit
        |
PositionManager -> legal, monotonic SL improvement -> TrailingEngine
        |
closed-trade features -> purged walk-forward training -> model registry
        |
proposal/evaluation -> owner review -> staged promotion or rollback
```

No MT5 call runs on the asyncio event-loop thread. A single broker worker owns
all terminal calls; the Telegram/FastAPI loops remain responsive. Database
sessions are not shared across threads. Execution and risk reservation are
serialized; the future APScheduler jobs must use max_instances=1 and coalescing.
A persisted single-instance lease protects the entire DB runtime, and locked
transactions serialize risk reservations across processes. Inflight broker/manual
races still require dedicated-account/server protection and reconciliation.

### Idempotency and uncertain acknowledgement

```text
prepared -> submitting -> acknowledged -> reconciled
                  |             |
                  |             +-> persist actual fill / remaining exposure
                  +-> explicit rejection -> rejected (no automatic retry)
                  +-> timeout/disconnect -> unknown -> halt new entries
```

MT5 does **not** provide a native exactly-once idempotency key. The database
prevents duplicate local intents, but an accepted order whose reply is lost
must be reconciled before any retry. Broker comments may be truncated/changed;
comments alone are not definitive evidence. Match account, order/deal tickets,
position identifier, magic, time, direction and volume; an unresolved ambiguity
requires owner intervention. Never retry an unknown `order_send` blindly.

### Profit-lock feasibility

At 30%, 60% and 90% of the original target, request the corresponding lock.
At exactly $3 floating profit it is normally **impossible** to legally place an
SL estimating $3 net profit: minimum stop/freeze distances, fees and slippage
leave no room. These levels are desired protection levels, not guaranteed fills.

The requested BUY/SELL formulas are retained as price candidates:

```text
BUY:  entry + desired_profit_distance - spread_buffer
SELL: entry - desired_profit_distance + spread_buffer
```

However, subtracting a buffer alone REDUCES the lock. The distance solver must
include fees, expected exit costs and buffer compensation, then verify net PnL
at the tick-rounded candidate with broker valuation. Do not double-count the
spread when entry/exits already use executable ask/bid. A candidate inside the
broker's stop/freeze zone is deferred (or a lower feasible tier is applied);
never claim the desired tier was locked. Gaps can still produce a worse fill.
Only a strictly more protective, legal SL is submitted. ATR cannot loosen a
better profit lock. TP extension is disabled by default and at most 1.2x when
enabled with safe news/volatility. Original target is immutable per trade.

### Learning/backtesting rules

- Only features available by the decision timestamp; only closed candles.
- Higher timeframe bars join as-of their CLOSE timestamp, not their open time.
- Labels mature after their complete forward horizon; open/unmatured trades
  never enter training. Purge label overlap and embargo the full horizon.
- Fit scalers/selectors/calibrators on training folds only. No shuffled CV.
- Replay news by publication/availability time and calendar data as known then;
  later revisions/actual release values cannot leak into earlier decisions.
- Synthetic mock data cannot qualify a model for broker/live promotion.
- Historical LLM analyses cannot be fabricated as if contemporaneous. Archived
  AI decisions or explicitly labeled deterministic baseline runs are required;
  paper/demo validate the actual AI veto layer.
- OHLC-only ambiguity uses adverse SL-before-TP ordering; realistic gaps, bid/ask,
  fees, swap and slippage are included. Fine-grained tick replay is preferred.
- Model artifacts are local, hashed and owner-approved. Never load downloaded
  untrusted pickle/joblib artifacts. Automatic risk increases are forbidden.

### Persistence/security boundaries

The seven requested tables are included, plus a deduplicated broker-deal ledger,
order intents, schema version, persistent state, account snapshots, risk baselines,
deployment evidence and owner approvals. Financial columns use exact Decimal strings in SQLite and
NUMERIC in PostgreSQL. Never perform SQL SUM on SQLite decimal text columns.
Timestamps are UTC-aware at the Python boundary. Audit is append-only through
ORM hooks and SQLite triggers. PostgreSQL runtime credentials must separately
lack UPDATE/DELETE on audit_logs; a database administrator can still alter data.
Backups and external audit copies are needed; this is not tamper-proof storage.

Configuration values are validated and read-only, including nested maps. Secrets
are SecretStr fields; public settings use an explicit allowlist. Rotating file
and console formatters redact known credentials, headers, token URLs and
tracebacks. Redaction is defense in depth, not permission to log raw requests,
initData, provider bodies or secrets in new code. No secrets in Mini App URLs.

## Complete target folder tree

`[now]` means implemented in Parts 1–5. Unmarked code files are supplied in
Parts 6–11. This is the **target tree**, not a claim all modules already exist.

```text
mt5_ai_reflex_bot/
├── main.py                              [now: non-trading operator CLI / migration]
├── config.py                            [now]
├── .env.example                         [now]
├── .gitignore                           [now]
├── requirements.txt                     [now]
├── requirements.linux.lock.txt          [now: Linux validation only]
├── pyproject.toml                       [now]
├── README.md                            [now]
├── watchdog.py
├── app/
│   ├── __init__.py
│   ├── bot.py
│   ├── scheduler.py
│   ├── lifecycle.py
│   └── dependencies.py
├── core/
│   ├── __init__.py                      [now]
│   ├── settings.py                      [now]
│   ├── logging_setup.py                 [now]
│   ├── database.py                      [now]
│   ├── migrations.py                    [now: explicit schema 1→2]
│   ├── models.py                        [now]
│   └── security.py                      [now: JSON/redaction helpers]
├── trading/
│   ├── __init__.py                      [now: Part 4]
│   ├── types.py                         [now: Part 4]
│   ├── price_rules.py                   [now: Part 4]
│   ├── currency.py                      [now: Part 4]
│   ├── authorization.py                 [now: deny-all default / contract]
│   ├── candles.py                       [now: Part 4]
│   ├── client_helpers.py                [now: Part 4]
│   ├── simulation.py                    [now: Part 4+5 automatic persistence]
│   ├── mt5_client.py                    [now: Part 4+5 final gate recheck]
│   ├── mock_mt5.py                      [now: Part 4]
│   ├── paper_mt5.py                     [now: Part 4]
│   ├── risk_types.py                    [now: trusted DTOs/provenance]
│   ├── runtime_state.py                 [now: durable lease/owner controls]
│   ├── stage_gate.py                    [now: artifact/ledger/live approval]
│   ├── execution_authority.py           [now: committed permit/revalidation]
│   ├── snapshots.py                     [now: portfolio/signed FX valuation]
│   ├── state_store.py                   [now: atomic shadow checkpoint]
│   ├── execution.py                    [now: Part 5]
│   ├── risk_engine.py                  [now: Part 5]
│   ├── position_manager.py             [now: Part 5]
│   ├── trailing_engine.py              [now: Part 5]
│   ├── symbol_manager.py                [now: Part 4]
│   ├── order_calculator.py              [now: Part 4]
│   └── trade_logger.py                  [now: Part 5]
├── strategy/
│   ├── __init__.py
│   ├── base_strategy.py
│   ├── candle_patterns.py
│   ├── trend_strategy.py
│   ├── mean_reversion_strategy.py
│   ├── breakout_strategy.py
│   ├── momentum_strategy.py
│   ├── volatility_filter.py
│   ├── news_filter.py
│   ├── signal_engine.py
│   └── strategy_router.py
├── ai/
│   ├── __init__.py
│   ├── ai_router.py
│   ├── ollama_client.py
│   ├── openai_client.py
│   ├── prompt_templates.py
│   ├── trade_analyzer.py
│   ├── learning_engine.py
│   ├── feature_engineering.py
│   ├── model_trainer.py
│   ├── model_registry.py
│   ├── strategy_optimizer.py
│   └── ai_supervisor.py
├── news/
│   ├── __init__.py
│   ├── news_manager.py
│   ├── rss_parser.py
│   ├── economic_calendar.py
│   ├── sentiment_analyzer.py
│   └── news_cache.py
├── telegram_bot/
│   ├── __init__.py
│   ├── handlers.py
│   ├── commands.py
│   ├── keyboards.py
│   ├── miniapp_auth.py
│   └── notifications.py
├── miniapp/
│   ├── server.py
│   ├── static/
│   │   ├── index.html
│   │   ├── app.js
│   │   └── style.css
│   └── api/
│       ├── dashboard.py
│       ├── trades.py
│       ├── ai.py
│       ├── news.py
│       └── settings.py
├── backtesting/
│   ├── __init__.py
│   ├── backtester.py
│   ├── metrics.py
│   └── promotion.py
├── data/
│   ├── candles/                         [now: empty runtime directories]
│   ├── news/
│   ├── models/
│   ├── logs/
│   ├── paper/                           [now: managed runtime checkpoint]
│   └── backups/
├── scripts/
│   ├── __init__.py                      [now]
│   ├── smoke_mock.py                    [now: synthetic only]
│   ├── smoke_risk.py                    [now: temporary synthetic risk smoke]
│   ├── check_mt5_readonly.py            [now: local Windows diagnostic]
│   ├── run_mt5_background.vbs
│   ├── run_bot_background.vbs
│   ├── install_task_scheduler.ps1
│   ├── compile_exe.ps1
│   └── backup_db.py
├── docs/
│   ├── PART_05.md                       [now: full current source/config/tests]
│   ├── PART_05_NOTES.md                 [now: contracts/limitations]
│   ├── MIGRATIONS.md                    [now: SQLite/PostgreSQL procedures]
│   ├── RELEASE_05_MANIFEST.json          [now: release integrity, not approval]
│   ├── PART_04.md                       [now: full Part 4 source]
│   ├── PART_04_NOTES.md                 [now]
│   ├── CURRENT_TREE.txt                 [now: actual archive tree]
│   ├── RELEASE_04_MANIFEST.json          [historical: hashes of prior release]
│   ├── ARCHITECTURE.md                  [now]
│   ├── VALIDATION.md                    [now]
│   ├── TARGET_TREE.txt                  [now]
│   └── PARTS_01_03.md                   [now: prior source snapshot]
└── tests/
    ├── __init__.py                      [now]
    ├── fake_mt5_sdk.py                  [now: TEST ONLY, not runtime]
    ├── test_trading_contracts.py        [now]
    ├── test_simulated_broker.py         [now]
    ├── test_trading_diagnostics.py      [now]
    ├── conftest.py                      [now]
    ├── test_settings.py                 [now]
    ├── test_database.py                 [now]
    ├── test_security_logging.py         [now]
    ├── test_foundation_cli.py           [now]
    ├── test_mt5_client.py               [now: fake/synthetic only]
    ├── test_order_calculator.py         [now: fake/synthetic only]
    ├── risk_helpers.py                  [now: TEST ONLY synthetic fixtures]
    ├── test_runtime_state.py            [now]
    ├── test_execution.py                [now]
    ├── test_trade_logger.py             [now]
    ├── test_state_store.py              [now]
    ├── test_stage_gate.py               [now]
    ├── test_stage_ledger.py             [now: TEST ONLY authored proofs]
    ├── test_migrations.py               [now]
    ├── test_risk_contracts.py            [now]
    ├── test_risk_concurrency.py          [now: real cross-process locks]
    ├── test_risk_diagnostics.py          [now]
    ├── test_risk_engine.py             [now: Part 5]
    ├── test_trailing_engine.py         [now: Part 5]
    ├── test_signal_engine.py
    ├── test_news_filter.py
    ├── test_telegram_auth.py
    ├── test_ai_json.py
    ├── test_mock_trading_flow.py
    └── test_backtester.py
```

## Complete current source — file index

- `trading/risk_types.py`
- `trading/runtime_state.py`
- `trading/risk_engine.py`
- `trading/trailing_engine.py`
- `trading/position_manager.py`
- `trading/execution.py`
- `trading/execution_authority.py`
- `trading/trade_logger.py`
- `trading/snapshots.py`
- `trading/state_store.py`
- `trading/stage_gate.py`
- `core/migrations.py`
- `scripts/smoke_risk.py`
- `.env.example`
- `requirements.txt`
- `requirements.linux.lock.txt`
- `README.md`
- `.gitignore`
- `pyproject.toml`
- `main.py`
- `config.py`
- `core/__init__.py`
- `core/database.py`
- `core/logging_setup.py`
- `core/models.py`
- `core/security.py`
- `core/settings.py`
- `scripts/__init__.py`
- `scripts/check_mt5_readonly.py`
- `scripts/smoke_mock.py`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/fake_mt5_sdk.py`
- `tests/risk_helpers.py`
- `tests/test_database.py`
- `tests/test_execution.py`
- `tests/test_foundation_cli.py`
- `tests/test_migrations.py`
- `tests/test_mt5_client.py`
- `tests/test_order_calculator.py`
- `tests/test_risk_concurrency.py`
- `tests/test_risk_contracts.py`
- `tests/test_risk_diagnostics.py`
- `tests/test_risk_engine.py`
- `tests/test_runtime_state.py`
- `tests/test_security_logging.py`
- `tests/test_settings.py`
- `tests/test_simulated_broker.py`
- `tests/test_stage_gate.py`
- `tests/test_stage_ledger.py`
- `tests/test_state_store.py`
- `tests/test_trade_logger.py`
- `tests/test_trading_contracts.py`
- `tests/test_trading_diagnostics.py`
- `tests/test_trailing_engine.py`
- `trading/__init__.py`
- `trading/authorization.py`
- `trading/candles.py`
- `trading/client_helpers.py`
- `trading/currency.py`
- `trading/mock_mt5.py`
- `trading/mt5_client.py`
- `trading/order_calculator.py`
- `trading/paper_mt5.py`
- `trading/price_rules.py`
- `trading/simulation.py`
- `trading/symbol_manager.py`
- `trading/types.py`

## File: `trading/risk_types.py`

<!-- SHA256: 025d762411c790f062d40ed7ea7047296725f7ad14c269452b92389389735803 -->

````python
"""Trusted internal decision DTOs. None of these are an HTTP authorization token."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from core.security import canonical_json, sha256_json
from core.settings import Settings
from trading.types import ZERO, BrokerError, SourceKind, aware_utc, valid_key


def json_dict(value: object) -> dict:
    return json.loads(canonical_json(value))


def source_code_hash(root: Path) -> str:
    """Bind runnable local code, not docs/tests, credentials, caches or runtime data."""
    names = ["main.py", "config.py", "requirements.txt"]
    for directory in (
        "core",
        "trading",
        "strategy",
        "ai",
        "news",
        "telegram_bot",
        "app",
        "miniapp",
        "backtesting",
    ):
        folder = root / directory
        if folder.exists():
            names.extend(
                str(path.relative_to(root)).replace("\\", "/")
                for path in folder.rglob("*")
                if path.is_file()
                and path.suffix in {".py", ".js", ".html", ".css"}
                and "__pycache__" not in path.parts
            )
    payload = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in sorted(set(names))
        if (root / name).is_file()
    }
    return sha256_json(payload)


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    code_hash: str
    model_sha256: str
    data_source: SourceKind

    def __post_init__(self):
        valid_key(self.code_hash)
        valid_key(self.model_sha256)
        if not isinstance(self.data_source, SourceKind):
            raise BrokerError("invalid runtime data provenance")

    @classmethod
    def current(cls, settings: Settings, data_source: SourceKind, *, model_sha256: str | None = None):
        code_root = (
            settings.project_root
            if (settings.project_root / "main.py").is_file()
            else Path(__file__).resolve().parents[1]
        )
        code = source_code_hash(code_root)
        # Identifies an explicit deterministic baseline, NOT a trained model.
        model = model_sha256 or sha256_json({"baseline": "rule-based-v1", "code_hash": code})
        return cls(code, model, data_source)


@dataclass(frozen=True, slots=True)
class NewsWindow:
    known: bool = False
    safe: bool = False
    headlines_fetched_at: datetime | None = None
    calendar_fetched_at: datetime | None = None
    calendar_covered_until: datetime | None = None
    evidence_hash: str | None = None

    def __post_init__(self):
        if type(self.known) is not bool or type(self.safe) is not bool:
            raise BrokerError("news flags must be actual booleans")
        for value in (self.headlines_fetched_at, self.calendar_fetched_at, self.calendar_covered_until):
            if value is not None:
                aware_utc(value)
        if self.evidence_hash is not None:
            valid_key(self.evidence_hash)

    def allows(self, settings: Settings, now: datetime) -> bool:
        if not (self.known and self.safe and self.evidence_hash):
            return False
        for when, age in (
            (self.headlines_fetched_at, settings.news_max_age_seconds),
            (self.calendar_fetched_at, settings.calendar_max_age_seconds),
        ):
            if when is None or not -2 <= (now - when).total_seconds() <= age:
                return False
        return self.calendar_covered_until is not None and self.calendar_covered_until >= now

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        for name in ("headlines_fetched_at", "calendar_fetched_at", "calendar_covered_until"):
            if row.get(name) is not None:
                row[name] = datetime.fromisoformat(row[name])
        return cls(**row)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    observed_at: datetime
    bar_closed_at: datetime
    source: SourceKind
    signal_score: float = 0
    ai_confidence: float = 0
    news: NewsWindow = field(default_factory=NewsWindow)
    signal_id: int | None = None
    risk_percent: Decimal | None = None
    features: dict = field(default_factory=dict)

    def __post_init__(self):
        aware_utc(self.observed_at)
        aware_utc(self.bar_closed_at)
        if not isinstance(self.source, SourceKind):
            raise BrokerError("invalid decision provenance")
        for value in (self.signal_score, self.ai_confidence):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise BrokerError("invalid technical/AI confidence")
        if self.risk_percent is not None and (
            not isinstance(self.risk_percent, Decimal)
            or not self.risk_percent.is_finite()
            or self.risk_percent <= ZERO
        ):
            raise BrokerError("invalid reduced risk percent")
        if len(canonical_json(self.features).encode()) > 8192:
            raise BrokerError("decision features exceed 8 KiB")
        # Copy caller-owned containers; the persisted digest binds subsequent use.
        object.__setattr__(self, "features", json_dict(self.features))

    @property
    def digest(self) -> str:
        return sha256_json(asdict(self))

    def to_dict(self) -> dict:
        return json_dict(asdict(self))

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        row["observed_at"] = datetime.fromisoformat(row["observed_at"])
        row["bar_closed_at"] = datetime.fromisoformat(row["bar_closed_at"])
        row["source"] = SourceKind(row["source"])
        row["news"] = NewsWindow.from_dict(row["news"])
        if row.get("risk_percent") is not None:
            row["risk_percent"] = Decimal(row["risk_percent"])
        return cls(**row)


@dataclass(frozen=True, slots=True)
class PositionReview:
    observed_at: datetime
    source: SourceKind
    ai_confidence: float
    momentum_continues: bool = False
    volatility_safe: bool = False
    news: NewsWindow = field(default_factory=NewsWindow)

    def __post_init__(self):
        aware_utc(self.observed_at)
        if (
            not isinstance(self.source, SourceKind)
            or isinstance(self.ai_confidence, bool)
            or not isinstance(self.ai_confidence, (int, float))
            or not math.isfinite(self.ai_confidence)
            or not 0 <= self.ai_confidence <= 100
        ):
            raise BrokerError("invalid position-review confidence/source")
        if type(self.momentum_continues) is not bool or type(self.volatility_safe) is not bool:
            raise BrokerError("review flags must be actual booleans")

    def allows_extension(self, settings: Settings, now: datetime, source: SourceKind) -> bool:
        return (
            settings.allow_tp_extension
            and self.source == source
            and -2 <= (now - self.observed_at).total_seconds() <= settings.order_max_age_seconds
            and self.ai_confidence >= settings.ai_confidence_threshold
            and self.momentum_continues
            and self.volatility_safe
            and self.news.allows(settings, now)
        )

    @classmethod
    def from_dict(cls, values: dict):
        row = dict(values)
        row["observed_at"] = datetime.fromisoformat(row["observed_at"])
        row["source"] = SourceKind(row["source"])
        row["news"] = NewsWindow.from_dict(row["news"])
        return cls(**row)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    risk_account: Decimal = ZERO
    aggregate_risk_account: Decimal = ZERO
    risk_budget_account: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class OwnedTrade:
    trade_id: int
    ticket: int
    identifier: int
    symbol: str
    direction: str
    volume: Decimal
    original_volume: Decimal
    entry_price: Decimal
    original_tp: Decimal
    target_usd: Decimal
    entry_costs_account: Decimal
    lock_level: float
    intent_key: str
````

## File: `trading/runtime_state.py`

<!-- SHA256: 7bc2091c841b6617b604b7f259b5b684099e08fc0e7c946690c3f922d10b4c5c -->

````python
"""Persistent single-runtime lease and owner controls; auth transport is Part 9.

owner_id MUST come from verified Telegram/initData identity, never request JSON.
Synthetic diagnostics explicitly configure a test owner; no default owner exists.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from core.database import Database
from core.models import BotState, OrderIntent, RiskEvent, RiskState
from core.settings import Settings
from trading.types import Clock, TradingDisabled

TERMINAL_INTENT_STATES = {"reconciled", "rejected", "canceled"}


class RuntimeControl:
    def __init__(self, database: Database, settings: Settings, clock: Clock):
        self.database, self.settings, self.clock = database, settings, clock
        self.session_id: str | None = None

    def claim(self) -> str:
        self.database.verify_schema()
        now = self.clock.now()
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("persistent control state is missing")
            if (
                state.session_id
                and state.heartbeat
                and (now - state.heartbeat).total_seconds() < self.settings.runtime_lease_seconds
            ):
                raise TradingDisabled("another runtime still owns the persisted lease")
            state.session_id = str(uuid4())
            state.desired_state = "killed" if state.kill_switch_active else "paused"
            state.heartbeat, state.last_config_hash = now, self.settings.safety_fingerprint()
            state.revision += 1
            # Preserve last_error, kill, drawdown, daily limits and pending intents.
            self.database.add_audit(
                session, "runtime.claimed_paused", "runtime", {"session_id": state.session_id}
            )
            self.session_id = state.session_id
        return self.session_id

    def check(self, state: BotState | None) -> None:
        if (
            state is None
            or self.session_id is None
            or state.session_id != self.session_id
            or state.last_config_hash != self.settings.safety_fingerprint()
            or state.heartbeat is None
            or not -2
            <= (self.clock.now() - state.heartbeat).total_seconds()
            < self.settings.runtime_lease_seconds
        ):
            raise TradingDisabled("runtime lease/session/configuration is absent or expired")

    def heartbeat(self) -> None:
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            state.heartbeat = self.clock.now()
            state.revision += 1

    def release(self) -> None:
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state and state.session_id == self.session_id:
                state.desired_state = "killed" if state.kill_switch_active else "paused"
                state.heartbeat, state.session_id = None, None
                state.revision += 1
                self.database.add_audit(session, "runtime.released", "runtime", {})
        self.session_id = None

    def _owner(self, owner_id: int) -> None:
        if (
            self.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("authenticated configured owner required")

    def _fresh_risk(self, risk: RiskState | None) -> bool:
        try:
            return (
                risk is not None
                and 0
                <= (
                    self.clock.now() - datetime.fromisoformat(risk.metadata_json["last_observed_at"])
                ).total_seconds()
                <= self.settings.risk_observation_max_age_seconds
            )
        except (KeyError, TypeError, ValueError):
            return False

    def pause(self, owner_id: int) -> None:
        self._owner(owner_id)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(session, "owner.paused", "owner", {"owner_id": owner_id})

    def kill(self, owner_id: int) -> None:
        self._owner(owner_id)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            state.kill_switch_active, state.desired_state = True, "killed"
            state.revision += 1
            self.database.add_audit(
                session,
                "owner.killed",
                "owner",
                {"owner_id": owner_id, "protective_management_remains_enabled": True},
            )

    def resume(self, owner_id: int, *, account_key: str) -> None:
        self._owner(owner_id)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            unsettled = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
            ).all()
            # Unsent staged intents must also be canceled/expired explicitly before resume.
            if (
                state.kill_switch_active
                or state.last_error
                or risk is None
                or not risk.metadata_json.get("baseline_verified")
                or not self._fresh_risk(risk)
                or risk.metadata_json.get("observation_gap")
                or risk.metadata_json.get("unclassified_cash_flow")
                or risk.metadata_json.get("unexplained_balance_change")
                or not risk.metadata_json.get("balance_continuity_verified")
                or risk.drawdown_latched
                or risk.daily_loss_latched
                or unsettled
            ):
                raise TradingDisabled("kill/loss/recovery/baseline/unsettled-intent gate prevents resume")
            state.desired_state = "running"
            state.revision += 1
            self.database.add_audit(
                session, "owner.resumed_entries", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def acknowledge_recovery(
        self, owner_id: int, *, account_key: str, broker_writes_quarantined: bool
    ) -> None:
        self._owner(owner_id)
        if broker_writes_quarantined:
            raise TradingDisabled("fresh reconciled broker runtime required; reconnect is insufficient")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            unresolved = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if unresolved or state.kill_switch_active:
                raise TradingDisabled("recovery cannot erase unresolved executions or a kill latch")
            state.last_error, state.desired_state = None, "paused"
            state.revision += 1
            self.database.add_audit(
                session, "owner.recovery_reviewed", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def halt(self, reason: str, *, account_key: str = "unbound") -> None:
        # Fixed identifiers only; never accept provider/SDK error bodies here.
        if reason not in {
            "unknown_execution",
            "broker_unstable",
            "ledger_mismatch",
            "persistence_failure",
            "risk_observation_gap",
        }:
            raise ValueError("unknown halt reason identifier")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.last_error = reason
            state.revision += 1
            session.add(
                RiskEvent(
                    time=self.clock.now(),
                    event=reason,
                    details={"session_id": self.session_id},
                    mode=self.settings.mode.value,
                    account_key=account_key,
                )
            )
            self.database.add_audit(
                session, "runtime.halted", "runtime", {"reason": reason, "account": account_key}
            )

    def review_flat_baseline(self, owner_id: int, *, account_key: str, confirm: str) -> None:
        """Explicit review of legacy/missed observations, ONLY with a flat proven ledger.

        Cannot reset a daily/drawdown latch or silently reconstruct unknown peaks.
        """
        self._owner(owner_id)
        if confirm != "REVIEW_SAMPLED_BASELINE":
            raise TradingDisabled("explicit sampled-baseline acknowledgement required")
        with self.database.locked_session() as session:
            self.check(session.get(BotState, 1))
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            from core.models import Trade

            active = session.scalar(
                select(Trade.id)
                .where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status.in_(("open", "unknown")),
                )
                .limit(1)
            )
            pending = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if (
                risk is None
                or active
                or pending
                or risk.daily_loss_latched
                or risk.drawdown_latched
                or risk.metadata_json.get("observed_position_count", -1) != 0
            ):
                raise TradingDisabled("baseline review needs flat/reconciled exposure and no loss latch")
            meta = dict(risk.metadata_json)
            complete = meta.get("history_complete_through")
            if (
                not complete
                or not 0
                <= (self.clock.now() - datetime.fromisoformat(complete)).total_seconds()
                <= self.settings.risk_observation_max_age_seconds
                or meta.get("unclassified_cash_flow")
                or not meta.get("balance_continuity_verified")
            ):
                raise TradingDisabled("fresh complete journal and known cash flows required for review")
            meta.update(
                baseline_verified=True,
                observation_gap=False,
                unexplained_balance_change=False,
                migration_review_required=False,
                sampled_peak_only=True,
                baseline_reviewed_at=self.clock.now().isoformat(),
            )
            risk.metadata_json = meta
            self.database.add_audit(
                session,
                "owner.sampled_baseline_reviewed",
                "owner",
                {"owner_id": owner_id, "account": account_key, "latches_reset": False},
            )

    def reset_kill(
        self, owner_id: int, *, account_key: str, confirm: str, broker_writes_quarantined: bool
    ) -> None:
        self._owner(owner_id)
        if confirm != "RESET_KILL_AND_KEEP_PAUSED" or broker_writes_quarantined:
            raise TradingDisabled("explicit fresh-runtime kill-reset review required")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            unresolved = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if (
                state.last_error
                or risk is None
                or not risk.metadata_json.get("baseline_verified")
                or not self._fresh_risk(risk)
                or unresolved
                or risk.daily_loss_latched
                or risk.drawdown_latched
                or risk.metadata_json.get("observation_gap")
                or risk.metadata_json.get("unclassified_cash_flow")
                or risk.metadata_json.get("unexplained_balance_change")
                or not risk.metadata_json.get("balance_continuity_verified")
            ):
                raise TradingDisabled("kill reset cannot override risk/unknown-execution gates")
            state.kill_switch_active, state.desired_state = False, "paused"
            state.revision += 1
            self.database.add_audit(
                session, "owner.kill_reset_paused", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def cancel_expired_unsent(self) -> int:
        count = 0
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            rows = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.state == "prepared", OrderIntent.expires_at <= self.clock.now()
                )
            ).all()
            for row in rows:
                if row.request.get("authorized") is not True:
                    row.state = "canceled"
                    count += 1
            if count:
                self.database.add_audit(session, "intent.unsent_expired", "runtime", {"count": count})
        return count
````

## File: `trading/risk_engine.py`

<!-- SHA256: bb948caa3aa6735af274b9ddc804671ba91078773f3d7d182aa786dd1dbebf25 -->

````python
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
        if cfg.news_required_for_entry and not context.news.allows(cfg, now):
            reasons.append("unknown_stale_or_unsafe_news")
        if self.profile.data_source == SourceKind.MT5:
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
                or signal.features_json.get("source") != "mt5"
                or signal.features_json.get("decision_digest") != context.digest
            ):
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
        percentage = context.risk_percent if context.risk_percent is not None else cfg.effective_risk_percent
        if percentage > cfg.effective_risk_percent:
            reasons.append("risk_escalation")
        budget = account.risk_capital * min(percentage, cfg.effective_risk_percent) / Decimal("100")
        risk, margin = snapshot.worst_loss_account, snapshot.required_margin_account
        if risk <= ZERO or risk > budget:
            reasons.append("entry_risk_cap")
        if margin < ZERO or margin > min(
            account.margin_free, account.risk_capital * cfg.max_margin_usage_percent / 100 - account.margin
        ):
            reasons.append("margin_cap")
        if risk > ZERO and snapshot.expected_reward_account / risk < cfg.min_net_reward_risk:
            reasons.append("net_reward_risk")
        if row.accepted_entries_today - int(self_counted) >= cfg.max_daily_trades:
            reasons.append("daily_entry_count")
        if len(snapshot.positions) >= cfg.max_open_positions:
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
````

## File: `trading/trailing_engine.py`

<!-- SHA256: 85c5b0656da6e526bbec56b04c9a5d4578208c7c5bca6c441354039b4830cf51 -->

````python
"""Achievable, monotonic net-USD profit locks and closed-bar ATR trailing.

A lock is an ESTIMATE at a nominal stop, not insurance against gaps, FX moves,
fees or rejected broker modifications. Never claim an infeasible earned tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from core.settings import Settings
from trading.order_calculator import OrderCalculator
from trading.price_rules import adverse_price, protection_prices, snap
from trading.risk_types import OwnedTrade, PositionReview
from trading.types import (
    ZERO,
    BrokerCommand,
    BrokerError,
    MarketData,
    Operation,
    Position,
    RiskViolation,
    Side,
    SourceKind,
)


@dataclass(frozen=True, slots=True)
class TrailingPlan:
    sl: Decimal
    lock_level: float
    estimated_net_at_stop_usd: Decimal
    reason: str


class TrailingEngine:
    def __init__(self, broker: MarketData, settings: Settings, source: SourceKind):
        self.broker, self.settings, self.clock, self.source = broker, settings, broker.clock, source
        self.calculator = OrderCalculator(broker, settings)

    @staticmethod
    def closed_atr(candles: pd.DataFrame, *, period: int = 14) -> Decimal:
        if type(period) is not int or period < 2 or len(candles) < period + 1:
            raise RiskViolation("ATR requires enough finalized observations")
        needed = {"time", "close_time", "high", "low", "close"}
        if (
            not needed.issubset(candles.columns)
            or not candles["close_time"].is_monotonic_increasing
            or candles["close_time"].duplicated().any()
        ):
            raise RiskViolation("ATR candle chronology/schema is invalid")
        highs, lows, closes = (
            [Decimal(str(value)) for value in candles[name]] for name in ("high", "low", "close")
        )
        if any(
            not value.is_finite() or value <= ZERO for values in (highs, lows, closes) for value in values
        ):
            raise RiskViolation("ATR prices are invalid")
        if any(high < low for high, low in zip(highs, lows, strict=True)):
            raise RiskViolation("ATR candle high/low are inverted")
        ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(candles))
        ]
        return sum(ranges[-period:], ZERO) / period

    async def _offset(self, position: Position, owned: OwnedTrade) -> Decimal:
        if (
            owned.identifier != position.identifier
            or owned.ticket != position.ticket
            or owned.volume != position.volume
            or owned.symbol != position.symbol
            or owned.direction != position.side.value
            or owned.target_usd <= ZERO
        ):
            raise RiskViolation("trailing inputs do not match the reconciled owned fill")
        # Actual paid entry/partial-exit amounts + current remaining swap, NOT
        # an estimated opening fee charged a second time.
        closing_usd = self.settings.commission_round_turn_usd_per_lot * position.volume / 2
        closing = -(await self.calculator.currency.usd_to_account(-closing_usd))
        return owned.entry_costs_account + position.swap - closing

    async def net_at_price(
        self, position: Position, owned: OwnedTrade, price: Decimal, *, stop: bool = True
    ) -> Decimal:
        meta = await self.broker.get_symbol_info(position.symbol)
        points = self.settings.max_slippage_points + (
            self.settings.trailing_spread_buffer_points if stop else 0
        )
        executable = adverse_price(price, position.side, meta, points, entry=False)
        if executable <= ZERO:
            raise RiskViolation("nonpositive stop valuation")
        value = await self.broker.calculate_profit(
            position.symbol, position.side, position.volume, position.entry_price, executable
        )
        return await self.calculator.currency.to_usd(value + await self._offset(position, owned))

    async def plan(
        self, position: Position, owned: OwnedTrade, *, candles: pd.DataFrame | None = None
    ) -> TrailingPlan | None:
        meta, tick = (
            await self.broker.get_symbol_info(position.symbol),
            await self.broker.get_tick(position.symbol),
        )
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        offset = await self._offset(position, owned)
        current_net = await self.net_at_price(position, owned, tick.exit(position.side), stop=False)
        progress = current_net * 100 / owned.target_usd
        candidates = []
        levels = self.settings.trailing_levels
        for trigger, lock in reversed(levels):
            if progress < Decimal(str(trigger)) or lock <= owned.lock_level:
                continue
            goal = owned.target_usd * Decimal(str(lock)) / 100
            try:
                sl = await self.calculator.price_for_profit_usd(
                    position.symbol,
                    position.side,
                    position.volume,
                    position.entry_price,
                    goal,
                    net_offset_account=offset,
                    exit_slippage_points=self.settings.max_slippage_points
                    + self.settings.trailing_spread_buffer_points,
                )
                if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
                    sl = position.sl  # Verify an already-better stop without loosening it.
                command = BrokerCommand(
                    Operation.PROTECT,
                    "1" * 64,
                    self.clock.now(),
                    ticket=position.ticket,
                    position_identifier=position.identifier,
                    sl=sl,
                )
                protection_prices(command, position, meta, tick, self.settings)
                net = await self.net_at_price(position, owned, sl)
                if net >= goal:
                    candidates.append((sl, lock, net, "profit_lock"))
                    break  # Highest earned AND legally achievable tier only.
            except BrokerError:
                continue  # Defer, do not clamp below the promised dollar lock.
        if candles is not None and self.settings.atr_trailing_enabled and current_net > ZERO:
            try:
                closes = pd.to_datetime(candles["close_time"], utc=True)
                if (
                    any(closes > self.clock.now())
                    or (self.clock.now() - closes.iloc[-1].to_pydatetime()).total_seconds()
                    > self.settings.max_candle_age_seconds
                ):
                    raise RiskViolation("ATR must use fresh CLOSED candles only")
                atr = self.closed_atr(candles)
                if atr > ZERO:
                    distance = atr * self.settings.atr_trailing_multiplier
                    sl = snap(
                        tick.exit(position.side) - position.side.sign * distance,
                        meta.tick_size,
                        up=position.side == Side.SELL,
                    )
                    if position.side.sign * (sl - position.sl) > ZERO:
                        command = BrokerCommand(
                            Operation.PROTECT,
                            "2" * 64,
                            self.clock.now(),
                            ticket=position.ticket,
                            position_identifier=position.identifier,
                            sl=sl,
                        )
                        protection_prices(command, position, meta, tick, self.settings)
                        net = await self.net_at_price(position, owned, sl)
                        verified = owned.lock_level
                        for trigger, lock in levels:
                            if (
                                progress >= Decimal(str(trigger))
                                and net >= owned.target_usd * Decimal(str(lock)) / 100
                            ):
                                verified = max(verified, lock)
                        candidates.append((sl, verified, net, "closed_bar_atr"))
            except (BrokerError, KeyError, ValueError, TypeError):
                pass  # ATR unavailable is not permission to loosen a stop.
        if not candidates:
            return None
        sl, level, net, reason = max(candidates, key=lambda row: position.side.sign * row[0])
        if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
            return None
        return TrailingPlan(sl, level, net, reason)

    async def extension(
        self, position: Position, owned: OwnedTrade, review: PositionReview | None
    ) -> Decimal | None:
        if review is None or not review.allows_extension(self.settings, self.clock.now(), self.source):
            return None
        tick, meta = (
            await self.broker.get_tick(position.symbol),
            await self.broker.get_symbol_info(position.symbol),
        )
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        if await self.net_at_price(
            position, owned, tick.exit(position.side), stop=False
        ) < owned.target_usd * Decimal("0.9"):
            return None
        try:
            goal = owned.target_usd * self.settings.tp_extension_factor
            price = await self.calculator.price_for_profit_usd(
                position.symbol,
                position.side,
                position.volume,
                position.entry_price,
                goal,
                net_offset_account=await self._offset(position, owned),
                exit_slippage_points=self.settings.max_slippage_points,
            )
            cap = abs(owned.original_tp - position.entry_price) * self.settings.tp_extension_factor
            if abs(price - position.entry_price) > cap or position.side.sign * (price - position.tp) <= ZERO:
                return None
            command = BrokerCommand(
                Operation.PROTECT,
                "3" * 64,
                self.clock.now(),
                ticket=position.ticket,
                position_identifier=position.identifier,
                tp=price,
            )
            protection_prices(command, position, meta, tick, self.settings)
            return price
        except BrokerError:
            return None
````

## File: `trading/position_manager.py`

<!-- SHA256: 441536063f604facb6c366332b6715649c7a679fad0f6f4c2ad57226bc44874b -->

````python
"""Manage only reconciled ownership; protective work continues while paused/killed."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from trading.execution import ExecutionEngine
from trading.risk_types import PositionReview
from trading.types import BrokerError, TradingDisabled


class PositionManager:
    def __init__(self, engine: ExecutionEngine):
        self.engine = engine
        self._lock = asyncio.Lock()

    async def cycle(self, reviews: Mapping[int, PositionReview] | None = None) -> dict:
        async with self._lock:
            summary = await self.engine.reconcile()
            owners = await asyncio.to_thread(self.engine.logger.owned, self.engine.account_key)
            outcomes = []
            for owned in owners:
                # Fresh IDs/volume; never adopt by magic/comment/symbol alone.
                positions = await self.engine.broker.get_positions()
                position = next(
                    (
                        item
                        for item in positions
                        if item.identifier == owned.identifier
                        and item.ticket == owned.ticket
                        and item.volume == owned.volume
                    ),
                    None,
                )
                if position is None:
                    continue
                try:
                    candles = None
                    if self.engine.settings.atr_trailing_enabled:
                        try:
                            candles = await self.engine.broker.get_candles(
                                position.symbol,
                                self.engine.settings.primary_timeframe,
                                self.engine.settings.candle_lookback,
                            )
                        except BrokerError:
                            pass  # Locks still work; don't invent ATR when history is unavailable.
                    plan = await self.engine.trailing.plan(position, owned, candles=candles)
                    if plan:
                        result = await self.engine.protect_sl(
                            position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level
                        )
                        outcomes.append(
                            {
                                "identifier": position.identifier,
                                "operation": "sl",
                                "status": result.status.value,
                                "reason": plan.reason,
                                "requested_lock_level": plan.lock_level,
                            }
                        )
                    # Refresh ownership/state AFTER protection. Never pair an old
                    # SL with a TP extension or widen it to make the extension fit.
                    positions = await self.engine.broker.get_positions()
                    position = next((item for item in positions if item.identifier == owned.identifier), None)
                    if position:
                        tp = await self.engine.trailing.extension(
                            position, owned, (reviews or {}).get(owned.identifier)
                        )
                        if tp is not None:
                            result = await self.engine.extend_tp(
                                position.ticket, position.identifier, tp, reviews[owned.identifier]
                            )
                            outcomes.append(
                                {
                                    "identifier": position.identifier,
                                    "operation": "tp",
                                    "status": result.status.value,
                                }
                            )
                except BrokerError as exc:
                    # Exception class only, not raw broker/provider bodies.
                    outcomes.append(
                        {
                            "identifier": owned.identifier,
                            "operation": "deferred",
                            "error_kind": type(exc).__name__,
                        }
                    )
                    await asyncio.to_thread(
                        self.engine.database.audit, "position.protection_deferred", "positions", outcomes[-1]
                    )
            return {"reconciliation": summary, "managed_positions": len(owners), "outcomes": outcomes}

    async def close(self, identifier: int, *, owner_id: int, idempotency_key: str | None = None):
        # The caller must supply an authenticated principal from Part 9.
        if (
            self.engine.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.engine.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may request a position close")
        await self.engine.reconcile()
        owners = await asyncio.to_thread(self.engine.logger.owned, self.engine.account_key)
        owned = next((item for item in owners if item.identifier == identifier), None)
        if owned is None:
            raise TradingDisabled("requested position is not durably owned")
        await asyncio.to_thread(
            self.engine.database.audit,
            "owner.close_requested",
            "owner",
            {"identifier": identifier, "owner_id": owner_id},
        )
        return await self.engine.close_owned(owned.ticket, identifier, idempotency_key=idempotency_key)
````

## File: `trading/execution.py`

<!-- SHA256: 886ae37e4a53ec06bf16f9228752a91621f282a7957619a669f65616b81678b1 -->

````python
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
from core.settings import Settings
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

    def new_key(self, operation: str, identifier: int) -> str:
        return sha256_json(
            {
                "session_id": self.control.session_id,
                "operation": operation,
                "identifier": identifier,
                "nonce": str(uuid4()),
            }
        )

    async def close_owned(
        self, ticket: int, identifier: int, *, idempotency_key: str | None = None
    ) -> ExecutionResult:
        async with self._lock:
            command = BrokerCommand(
                Operation.CLOSE,
                idempotency_key or self.new_key("close", identifier),
                self.clock.now(),
                ticket=ticket,
                position_identifier=identifier,
            )
            return await self._execute(command)

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
````

## File: `trading/execution_authority.py`

<!-- SHA256: 6e51e78dd4376383cdc8923605e3ed02a77252a5fc27631cb72cd6305066618d -->

````python
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
from trading.risk_types import DecisionContext, PositionReview, RuntimeProfile, json_dict
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
    ) -> ExecutionResult | None:
        if command.operation == Operation.OPEN and (
            context is None
            or not isinstance(target_usd, Decimal)
            or not target_usd.is_finite()
            or target_usd <= ZERO
        ):
            raise TradingDisabled("entry requires a complete trusted decision and original USD objective")
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
                                if review is None or not review.allows_extension(
                                    cfg, now, self.profile.data_source
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
                if command.operation == Operation.PROTECT and grant.allow_tp_extension:
                    review = PositionReview.from_dict(row.request["review"])
                    failure = failure or not review.allows_extension(
                        self.settings, self.clock.now(), self.profile.data_source
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
````

## File: `trading/trade_logger.py`

<!-- SHA256: eec245b3c57454daa610ff518f5ce8e9f0de920458a09c935749147066ef979a -->

````python
"""Positive-proof ownership, deduplicated fill legs and whole-trade accounting.

An order/deal number is NOT a position ticket. Empty history is NOT a no-fill
proof. Unexplained additions, reversals, missing exits or ID conflicts halt entry.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from core.database import Database
from core.models import BotState, BrokerDeal, OrderIntent, RiskEvent, Trade
from core.security import sanitize_text
from core.settings import Settings
from trading.execution_authority import execution_dict, execution_from_dict
from trading.risk_engine import RiskEngine
from trading.risk_types import OwnedTrade, RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.types import ZERO, AccountInfo, Clock, Deal, Position, ResultStatus, RiskViolation


class TradeLogger:
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

    def history_start(self, account_key: str):
        with self.database.session() as session:
            intents = session.scalars(
                select(OrderIntent.time).where(
                    OrderIntent.account_key == account_key, OrderIntent.mode == self.settings.mode.value
                )
            ).all()
        zone = self.risk.zone
        local = self.clock.now().astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return min([midnight, *intents])

    @staticmethod
    def _deal_matches(row: BrokerDeal, deal: Deal) -> bool:
        raw = asdict(deal)
        raw.pop("reason")
        raw["position_identifier"] = raw["position_identifier"] or None
        raw["order_ticket"] = raw["order_ticket"] or None
        raw["comment"] = row.comment  # Sanitized text is NEVER identity evidence.
        for name, value in raw.items():
            if isinstance(value, Decimal):
                value = value.quantize(Decimal("1e-12") if name == "price" else Decimal("1e-8"))
            if getattr(row, name) != value:
                return False
        return True

    def reconcile(
        self,
        account: AccountInfo,
        positions: tuple[Position, ...],
        deals: tuple[Deal, ...],
        *,
        shadow_cache: dict | None = None,
        stop_net_usd: dict[int, Decimal] | None = None,
        settled_orders: frozenset[int] = frozenset(),
        history_complete: bool = True,
    ) -> dict:
        cfg, now = self.settings, self.clock.now()
        if len({position.identifier for position in positions}) != len(positions):
            raise RiskViolation("duplicate position identifiers in broker snapshot")
        if len({deal.ticket for deal in deals}) != len(deals) or len(deals) > 100000:
            raise RiskViolation("duplicate/oversized broker deal response")
        if any(deal.currency != account.currency or deal.time > now for deal in deals):
            raise RiskViolation("future or mixed-currency broker deals")
        mismatch = False
        completed = 0
        with self.database.locked_session() as session:
            self.control.check(session.get(BotState, 1))
            existing = {
                row.ticket: row
                for row in session.scalars(
                    select(BrokerDeal).where(
                        BrokerDeal.account_key == account.key, BrokerDeal.mode == cfg.mode.value
                    )
                ).all()
            }
            for deal in deals:
                if deal.ticket in existing:
                    if not self._deal_matches(existing[deal.ticket], deal):
                        mismatch = True
                    continue
                values = asdict(deal)
                values.pop("reason")
                values["comment"] = sanitize_text(values["comment"], self.database.secrets)[:128]
                values["position_identifier"] = values["position_identifier"] or None
                values["order_ticket"] = values["order_ticket"] or None
                record = BrokerDeal(**values, account_key=account.key, mode=cfg.mode.value)
                session.add(record)
                existing[deal.ticket] = record
            session.flush()
            ledger = list(existing.values())
            live = {position.identifier: position for position in positions}
            intents = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == account.key, OrderIntent.mode == cfg.mode.value
                )
            ).all()
            trades = session.scalars(
                select(Trade).where(Trade.account_key == account.key, Trade.mode == cfg.mode.value)
            ).all()
            by_intent = {trade.order_intent_id: trade for trade in trades}
            if shadow_cache:
                for intent in intents:
                    hint = shadow_cache.get(intent.idempotency_key)
                    if hint and intent.state not in {"reconciled", "rejected", "canceled"}:
                        if hint[0] != intent.request.get("request_hash"):
                            mismatch = True
                            continue
                        result = execution_from_dict(hint[1])
                        if result.account_key != account.key:
                            mismatch = True
                            continue
                        payload = dict(intent.request)
                        payload["result"] = execution_dict(result)
                        intent.request = payload
                        intent.state = (
                            "rejected" if result.status == ResultStatus.REJECTED else "acknowledged"
                        )
                        self.database.add_audit(
                            session,
                            "shadow.durable_ack_recovered",
                            "reconcile",
                            {"key": intent.idempotency_key},
                        )
            # Create entries only from positive broker acknowledgement + exact deal/order IDs.
            for intent in intents:
                if intent.request.get("command", {}).get("operation") != "open" or intent.id in by_intent:
                    continue
                data = intent.request.get("result")
                if data is None or intent.state in {"rejected", "canceled"}:
                    continue
                result = execution_from_dict(data)
                if result.status not in {
                    ResultStatus.FILLED,
                    ResultStatus.PARTIAL,
                    ResultStatus.ACCEPTED,
                    ResultStatus.UNKNOWN,
                }:
                    continue
                candidates = [
                    row
                    for row in ledger
                    if row.type in {"buy", "sell"}
                    and row.entry == "in"
                    and (
                        (result.order_ticket > 0 and row.order_ticket == result.order_ticket)
                        or (result.deal_ticket > 0 and row.ticket == result.deal_ticket)
                    )
                ]
                if not candidates:
                    continue
                order_ids = {row.order_ticket for row in candidates}
                if len(order_ids) != 1 or None in order_ids:
                    mismatch = True
                    continue
                order_id = next(iter(order_ids))
                candidates = [
                    row
                    for row in ledger
                    if row.order_ticket == order_id and row.entry == "in" and row.type in {"buy", "sell"}
                ]
                ids = {row.position_identifier for row in candidates}
                order = intent.request["command"]["order"]
                expected = Decimal(order["volume"])
                volume = sum((row.volume for row in candidates), ZERO)
                if (
                    len(ids) != 1
                    or None in ids
                    or volume <= ZERO
                    or volume > expected
                    or any(
                        row.magic != cfg.mt5_magic_number
                        or row.symbol != intent.symbol
                        or row.type != intent.direction
                        or row.time < intent.time - timedelta(seconds=2)
                        for row in candidates
                    )
                    or (result.deal_ticket and result.deal_ticket not in {row.ticket for row in candidates})
                ):
                    mismatch = True
                    continue
                final = volume == expected or order_id in settled_orders
                if not final:
                    continue  # A partial/pending order could STILL acquire exposure.
                identifier = next(iter(ids))
                position = live.get(identifier)
                exits = [
                    row
                    for row in ledger
                    if row.position_identifier == identifier
                    and row.entry in {"out", "out_by"}
                    and row.type in {"buy", "sell"}
                ]
                exited = sum((row.volume for row in exits), ZERO)
                if position is None and (not history_complete or exited != volume):
                    continue  # Never use absence alone as a fill/close proof.
                if position and (
                    position.volume != volume - exited
                    or position.symbol != intent.symbol
                    or position.side.value != intent.direction
                    or position.magic != cfg.mt5_magic_number
                ):
                    mismatch = True
                    continue
                if any(trade.position_identifier == identifier for trade in trades):
                    mismatch = True
                    continue
                vwap = sum((row.price * row.volume for row in candidates), ZERO) / volume
                context = intent.request["context"]
                execution = {
                    "version": 1,
                    "magic": cfg.mt5_magic_number,
                    "intent_key": intent.idempotency_key,
                    "order_ticket": order_id,
                    "entry_deal_tickets": [row.ticket for row in candidates],
                    "original_volume": str(volume),
                    "original_sl": order["sl"],
                    "original_tp": order["tp"],
                    "planned_volume": str(expected),
                    "original_entry_vwap": str(vwap),
                    "realized_net_account": "0",
                    "code_hash": intent.request["code_hash"],
                    "model_sha256": intent.request["model_sha256"],
                    "data_source": intent.request.get("data_source"),
                    "strategy_config_hash": intent.request.get("strategy_config_hash"),
                    "profit_usd_verified": account.currency == "USD",
                    "sampled_peak_only": True,
                    "risk_is_authorization_estimate": True,
                }
                trade = Trade(
                    account_key=account.key,
                    mode=cfg.mode.value,
                    currency=account.currency,
                    ticket=position.ticket if position else None,
                    position_identifier=identifier,
                    order_intent_id=intent.id,
                    symbol=intent.symbol,
                    direction=intent.direction,
                    volume=position.volume if position else volume,
                    entry_price=position.entry_price if position else vwap,
                    sl=position.sl if position else Decimal(order["sl"]),
                    tp=position.tp if position else Decimal(order["tp"]),
                    open_time=min(row.time for row in candidates),
                    initial_risk_usd=Decimal(intent.request["reserved_risk_usd"]) * volume / expected,
                    target_profit_usd=Decimal(intent.request["target_usd"]) * volume / expected,
                    profit_lock_level=0,
                    strategy=order["strategy"],
                    signal_score=context["signal_score"],
                    ai_score=context["ai_confidence"],
                    config_hash=intent.config_hash,
                    features_json={"decision": context, "execution": execution},
                    status="open",
                )
                session.add(trade)
                session.flush()
                by_intent[intent.id] = trade
                trades.append(trade)
                payload = dict(intent.request)
                payload["reserved"] = False
                payload["result"] = execution_dict(
                    replace(
                        result,
                        status=ResultStatus.FILLED,
                        position_identifier=identifier,
                        filled_volume=volume,
                        filled_price=position.entry_price if position else vwap,
                        reason=result.reason
                        if result.status == ResultStatus.FILLED
                        else "positive_exact_deals_reconciled",
                    )
                )
                intent.request, intent.state = payload, "reconciled"
                completed += 1
                self.database.add_audit(
                    session,
                    "entry.ownership_proved",
                    "reconcile",
                    {
                        "key": intent.idempotency_key,
                        "position_identifier": identifier,
                        "actual_position_ticket": trade.ticket,
                        "fill_volume": str(volume),
                    },
                )
            # Whole-trade P&L sums ALL legs, not the closing deal alone.
            for trade in trades:
                if trade.status == "unknown":
                    continue
                meta = dict(trade.features_json.get("execution", {}))
                if meta.get("version") != 1:
                    mismatch = True
                    continue
                legs = [row for row in ledger if row.position_identifier == trade.position_identifier]
                entries = [row for row in legs if row.entry == "in" and row.type in {"buy", "sell"}]
                exits = [
                    row for row in legs if row.entry in {"out", "out_by"} and row.type in {"buy", "sell"}
                ]
                expected = Decimal(meta["original_volume"])
                exited = sum((row.volume for row in exits), ZERO)
                if (
                    {row.ticket for row in entries} != set(meta["entry_deal_tickets"])
                    or any(row.entry == "inout" or row.type.startswith("unknown") for row in legs)
                    or exited > expected
                ):
                    trade.status, mismatch = "unknown", True
                    continue
                net = sum((row.profit + row.commission + row.swap + row.fee for row in legs), ZERO)
                trade.profit, trade.commission = net, sum((row.commission + row.fee for row in legs), ZERO)
                trade.swap = sum((row.swap for row in legs), ZERO)
                trade.profit_usd = net if trade.currency == "USD" else ZERO
                meta.update(
                    realized_net_account=str(net),
                    profit_usd_verified=trade.currency == "USD",
                    historical_fx_required=trade.currency != "USD",
                )
                position = live.get(trade.position_identifier)
                if position:
                    if (
                        position.symbol != trade.symbol
                        or position.side.value != trade.direction
                        or position.magic != cfg.mt5_magic_number
                        or position.volume != expected - exited
                        or trade.status == "closed"
                    ):
                        trade.status, mismatch = "unknown", True
                    else:
                        trade.ticket, trade.volume, trade.sl, trade.tp = (
                            position.ticket,
                            position.volume,
                            position.sl,
                            position.tp,
                        )
                        trade.entry_price = position.entry_price  # True broker VWAP may be off-grid.
                elif exited == expected and exits and history_complete:
                    if trade.status != "closed":
                        trade.status, trade.close_time = "closed", max(row.time for row in exits)
                        trade.close_reason = "broker_reconciled_exit"
                        self.database.add_audit(
                            session,
                            "trade.closed_all_legs",
                            "reconcile",
                            {
                                "trade_id": trade.id,
                                "net_account": str(net),
                                "currency": trade.currency,
                                "usd_verified": meta["profit_usd_verified"],
                            },
                        )
                elif trade.status == "open":
                    mismatch = True
                features = dict(trade.features_json)
                features["execution"] = meta
                trade.features_json = features
            # Maintenance goal must be verified against actual owned state/exit legs.
            for intent in intents:
                if intent.state not in {"acknowledged", "unknown", "submitting"}:
                    continue
                command = intent.request.get("command", {})
                operation = command.get("operation")
                if operation == "open":
                    continue
                trade = next(
                    (
                        item
                        for item in trades
                        if item.position_identifier == command.get("position_identifier")
                    ),
                    None,
                )
                if trade is None or trade.status == "unknown":
                    continue
                position = live.get(trade.position_identifier)
                data = intent.request.get("result")
                positive = data and data["status"] in {"filled", "no_change"}
                verified = False
                if (
                    operation == "close"
                    and trade.status == "closed"
                    and trade.close_time >= intent.time - timedelta(seconds=2)
                ):
                    verified = True  # Complete exit legs, even if a server SL beat the close request.
                if operation == "protect" and positive and position:
                    wanted_sl, wanted_tp = command.get("sl"), command.get("tp")
                    verified = (wanted_sl is None or position.sl == Decimal(wanted_sl)) and (
                        wanted_tp is None or position.tp == Decimal(wanted_tp)
                    )
                    level = intent.request.get("lock_level", 0)
                    estimate = (stop_net_usd or {}).get(position.identifier)
                    if verified and level > trade.profit_lock_level:
                        if (
                            estimate is not None
                            and estimate >= trade.target_profit_usd * Decimal(str(level)) / 100
                        ):
                            trade.profit_lock_level = level
                            meta = dict(trade.features_json["execution"])
                            meta.update(
                                last_verified_lock_net_usd=str(estimate),
                                last_verified_lock_at=now.isoformat(),
                            )
                            trade.features_json = {**trade.features_json, "execution": meta}
                        else:
                            # Price changed, but do NOT claim an unverified dollar lock.
                            self.database.add_audit(
                                session, "trailing.lock_not_claimed", "reconcile", {"trade_id": trade.id}
                            )
                if verified:
                    intent.state = "reconciled"
                    completed += 1
                    self.database.add_audit(
                        session,
                        "maintenance.goal_verified",
                        "reconcile",
                        {
                            "key": intent.idempotency_key,
                            "operation": operation,
                            "broker_execution_verified": bool(positive),
                        },
                    )
            row = self.risk.observe(session, account, positions, intraday_history_complete=history_complete)
            unsettled = [item for item in intents if item.state in {"submitting", "acknowledged", "unknown"}]
            known = {trade.position_identifier for trade in trades if trade.status == "open"}
            if any(position.identifier not in known for position in positions):
                mismatch = True
            if unsettled and not mismatch:
                state = session.get(BotState, 1)
                if not state.kill_switch_active:
                    state.desired_state = "paused"
                if state.last_error is None:
                    state.last_error, state.revision = "unknown_execution", state.revision + 1
                    self.database.add_audit(
                        session, "reconcile.unsettled_halt", "reconcile", {"account": account.key}
                    )
            if mismatch:
                state = session.get(BotState, 1)
                if not state.kill_switch_active:
                    state.desired_state = "paused"
                state.last_error, state.revision = "ledger_mismatch", state.revision + 1
                session.add(
                    RiskEvent(
                        time=now,
                        event="ledger_mismatch",
                        details={"unsettled_count": len(unsettled)},
                        account_key=account.key,
                        mode=cfg.mode.value,
                    )
                )
                self.database.add_audit(
                    session, "reconcile.ledger_halt", "reconcile", {"account": account.key}
                )
            summary = {
                "completed_intents": completed,
                "unsettled_intents": len(unsettled),
                "ledger_mismatch": mismatch,
                "reserved_risk_usd": str(row.reserved_risk_usd),
                "closed_trades": sum(trade.status == "closed" for trade in trades),
            }
        return summary

    def owned(self, account_key: str) -> tuple[OwnedTrade, ...]:
        result = []
        with self.database.session() as session:
            rows = session.scalars(
                select(Trade).where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status == "open",
                )
            ).all()
            for row in rows:
                meta = row.features_json.get("execution", {})
                if meta.get("version") == 1 and row.ticket:
                    result.append(
                        OwnedTrade(
                            row.id,
                            row.ticket,
                            row.position_identifier,
                            row.symbol,
                            row.direction,
                            row.volume,
                            Decimal(meta["original_volume"]),
                            row.entry_price,
                            Decimal(meta["original_tp"]),
                            row.target_profit_usd,
                            Decimal(meta["realized_net_account"]),
                            row.profit_lock_level,
                            meta["intent_key"],
                        )
                    )
        return tuple(result)
````

## File: `trading/snapshots.py`

<!-- SHA256: 06382d3a9131b9e9c1f081f87179d8b824fb6b698513cca53fc5f63b9b408226 -->

````python
"""Fresh portfolio loss-to-stop and signed account/USD snapshot valuation."""

from decimal import Decimal

from core.settings import Settings
from trading.authorization import BrokerSnapshot, PositionRisk
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price
from trading.types import ZERO, AccountInfo, MarketData, Position


async def broker_snapshot(
    market: MarketData,
    settings: Settings,
    account: AccountInfo,
    symbol,
    tick,
    positions: tuple[Position, ...],
    risk=ZERO,
    margin=ZERO,
    reward=ZERO,
    *,
    entry: bool = True,
    durable_simulation: bool = False,
) -> BrokerSnapshot:
    converter = CurrencyConverter(market, settings)
    asset, liability = None, None
    valuations = []
    if entry:
        asset = await converter.convert(Decimal("1"), account.currency, "USD")
        liability = -(await converter.convert(Decimal("-1"), account.currency, "USD"))
        for position in positions:
            meta, quote = (
                await market.get_symbol_info(position.symbol),
                await market.get_tick(position.symbol),
            )
            quote.fresh(market.clock, settings.max_tick_age_seconds)
            if position.sl <= ZERO:
                valuations.append(PositionRisk(position.identifier, account.risk_capital))
                continue
            stop_exit = adverse_price(
                position.sl, position.side, meta, settings.max_slippage_points, entry=False
            )
            at_stop = await market.calculate_profit(
                position.symbol, position.side, position.volume, position.entry_price, stop_exit
            )
            current = await market.calculate_profit(
                position.symbol,
                position.side,
                position.volume,
                position.entry_price,
                quote.exit(position.side),
            )
            cost = -(
                await converter.convert(
                    -settings.commission_round_turn_usd_per_lot * position.volume, "USD", account.currency
                )
            )
            # Count both initial nominal loss and current marked-equity downside.
            loss = max(ZERO, -at_stop - position.swap + cost, current - at_stop + cost / 2)
            valuations.append(PositionRisk(position.identifier, loss))
    return BrokerSnapshot(
        account,
        symbol,
        tick,
        positions,
        risk,
        margin,
        reward,
        market.clock.now(),
        tuple(valuations),
        asset,
        liability,
        market.source_kind,
        durable_simulation,
    )
````

## File: `trading/state_store.py`

<!-- SHA256: 0515eb676479aba00fe48328872bbbac8d94b4b55c3e8cc1951b9407f96e0695 -->

````python
"""Atomic, checksummed shadow-broker checkpoint. Not tamper-proof authentication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from core.security import canonical_json
from trading.types import RiskViolation


class AtomicSnapshotStore:
    def __init__(self, path: Path, *, max_bytes: int = 16777216):
        self.path, self.max_bytes = path, max_bytes

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or self.path.stat().st_size > self.max_bytes:
            raise RiskViolation("paper checkpoint is symlinked/oversized")
        try:

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate checkpoint key")
                    result[key] = value
                return result

            raw = self.path.read_bytes()
            if len(raw) > self.max_bytes:
                raise ValueError("checkpoint grew while loading")
            envelope = json.loads(raw, object_pairs_hook=unique)
            body = canonical_json(envelope["state"]).encode()
            if envelope.get("version") != 1 or hashlib.sha256(body).hexdigest() != envelope.get("sha256"):
                raise ValueError("checkpoint digest")
            return envelope["state"]
        except Exception:
            raise RiskViolation("paper checkpoint is corrupt; NEVER reset capital automatically") from None

    def save(self, state: dict) -> None:
        body = canonical_json(state).encode()
        payload = canonical_json(
            {"version": 1, "sha256": hashlib.sha256(body).hexdigest(), "state": state}
        ).encode()
        if len(payload) > self.max_bytes:
            raise RiskViolation("paper checkpoint exceeds the reviewed storage bound")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RiskViolation("paper checkpoint may not be a symlink")
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=".paper-", suffix=".tmp", delete=False
            ) as handle:
                name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
````

## File: `trading/stage_gate.py`

<!-- SHA256: df68268865b61e313aefe49c94bc6728d54c7a8c11ddb4ab613671d9e55ab441 -->

````python
"""Artifact-bound promotion and owner live confirmation. No reports are fabricated."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import Database
from core.models import (
    AccountSnapshot,
    BotState,
    BrokerDeal,
    DeploymentEvidence,
    OrderIntent,
    OwnerApproval,
    Trade,
)
from core.security import canonical_json, sha256_json
from core.settings import OperatingMode, Settings
from trading.risk_types import RuntimeProfile
from trading.types import AccountInfo, AccountKind, Clock, SourceKind, TradingDisabled, valid_key


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate artifact key")
        result[key] = value
    return result


def read_report(path, limit: int = 1048576, *, expected_sha256: str | None = None) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise TradingDisabled("stage artifact missing/oversized/symlinked")

    def bad_constant(value):
        raise ValueError("nonfinite stage JSON")

    try:
        raw = path.read_bytes()
        if len(raw) > limit or (
            expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError("report digest/size")
        report = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=bad_constant)
        if not isinstance(report, dict):
            raise ValueError("object required")

        # Bound recursive data as well as input bytes.
        def walk(value, depth=0):
            if depth > 16:
                raise ValueError("deep stage JSON")
            if isinstance(value, dict):
                for child in value.values():
                    walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)

        walk(report)
        canonical_json(report)
        return report
    except Exception:
        raise TradingDisabled("invalid stage artifact; raw contents suppressed") from None


@dataclass(frozen=True, slots=True)
class LiveChallenge:
    approval_id: str
    nonce: str = field(repr=False)
    request_hash: str
    expires_at: datetime


class StageGate:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile

    def _validate_evidence(self, evidence: DeploymentEvidence, stage: str, session: Session) -> None:
        cfg, profile, now = self.settings, self.profile, self.clock.now()
        if (
            evidence.stage != stage
            or not evidence.passed
            or evidence.revoked
            or cfg.telegram_owner_id is None
            or evidence.owner_reviewed_by != cfg.telegram_owner_id
            or evidence.strategy_config_hash != cfg.strategy_fingerprint()
            or evidence.code_hash != profile.code_hash
            or evidence.model_sha256 != profile.model_sha256
        ):
            raise TradingDisabled("stage evidence policy/model/owner binding is invalid")
        if not evidence.started_at < evidence.finished_at <= now or evidence.created_at > now + timedelta(
            seconds=2
        ):
            raise TradingDisabled("stage evidence dates are incomplete/future")
        path = cfg.resolve_path(evidence.artifact_path)
        report = read_report(path, expected_sha256=evidence.artifact_sha256)
        expected = {
            "format": "reflex-stage-v1",
            "stage": stage,
            "strategy_config_hash": cfg.strategy_fingerprint(),
            "code_hash": profile.code_hash,
            "model_sha256": profile.model_sha256,
            "started_at": evidence.started_at.isoformat(),
            "finished_at": evidence.finished_at.isoformat(),
            "account_key": evidence.account_key,
            "metrics": evidence.metrics_json,
        }
        if any(report.get(key) != value for key, value in expected.items()):
            raise TradingDisabled("stage report does not bind its database record")
        required_source = {"backtest": "historical_real", "paper": "mt5_real_data", "demo": "mt5_demo"}[stage]
        if report.get("source") != required_source:
            raise TradingDisabled("synthetic/test data cannot qualify for broker promotion")
        try:
            valid_key(report.get("dataset_sha256"))
        except Exception:
            raise TradingDisabled("stage dataset digest is invalid") from None
        if stage == "backtest":
            dataset = cfg.resolve_path(report.get("dataset_path", "missing-stage-dataset"))
            if dataset.is_symlink() or not dataset.is_file() or dataset.stat().st_size > 268435456:
                raise TradingDisabled("reviewed historical dataset is missing/oversized/symlinked")
            digest = hashlib.sha256()
            with dataset.open("rb") as handle:
                for block in iter(lambda: handle.read(1048576), b""):
                    digest.update(block)
            if digest.hexdigest() != report["dataset_sha256"]:
                raise TradingDisabled("historical dataset integrity mismatch")
        metrics = report["metrics"]
        try:
            count = metrics["closed_trades"]
            factor = Decimal(str(metrics["profit_factor"]))
            drawdown = Decimal(str(metrics["max_drawdown_percent"]))
            gaps = metrics["unexplained_gaps"]
            if type(count) is not int or count < cfg.stage_min_trades or type(gaps) is not int or gaps != 0:
                raise ValueError("insufficient trades/coverage")
            if (
                not factor.is_finite()
                or factor < cfg.stage_min_profit_factor
                or not drawdown.is_finite()
                or not 0 <= drawdown <= cfg.stage_max_drawdown_percent
            ):
                raise ValueError("risk/performance gate")
            if metrics.get("costs_included") is not True or metrics.get("lookahead_free") is not True:
                raise ValueError("costs/chronology not evaluated")
            minimum = {"backtest": 0, "paper": cfg.stage_min_paper_days, "demo": cfg.stage_min_demo_days}[
                stage
            ]
            if (evidence.finished_at - evidence.started_at).total_seconds() < minimum * 86400:
                raise ValueError("stage too short")
        except Exception:
            raise TradingDisabled("stage metrics do not meet the reviewed promotion policy") from None
        if stage in {"paper", "demo"}:
            self._verify_stage_ledger(session, evidence, stage)

    def _verify_stage_ledger(self, session: Session, evidence: DeploymentEvidence, stage: str) -> None:
        """Artifact assertions alone cannot turn 5 synthetic trades into promotion."""
        cfg = self.settings
        if not evidence.account_key:
            raise TradingDisabled("market stage lacks its actual account scope")
        trades = session.scalars(
            select(Trade).where(
                Trade.account_key == evidence.account_key,
                Trade.mode == stage,
                Trade.status == "closed",
                Trade.open_time >= evidence.started_at,
                Trade.close_time <= evidence.finished_at,
            )
        ).all()
        valid = []
        for trade in trades:
            meta = trade.features_json.get("execution", {})
            if (
                meta.get("data_source") == "mt5"
                and meta.get("code_hash") == self.profile.code_hash
                and meta.get("model_sha256") == self.profile.model_sha256
                and meta.get("strategy_config_hash") == cfg.strategy_fingerprint()
                and meta.get("version") == 1
                and meta.get("entry_deal_tickets")
                and trade.order_intent_id
                and trade.close_time is not None
            ):
                valid.append(trade)
        if len(valid) != evidence.metrics_json["closed_trades"] or len(valid) < cfg.stage_min_trades:
            raise TradingDisabled("stage count is not backed by real-data reconciled trade records")
        if len({trade.currency for trade in valid}) != 1:
            raise TradingDisabled("mixed-currency stage return series")
        self._verify_trade_proofs(session, evidence, stage, valid)
        wins = sum((max(Decimal("0"), trade.profit) for trade in valid), Decimal("0"))
        losses = sum((max(Decimal("0"), -trade.profit) for trade in valid), Decimal("0"))
        unallocated = session.scalars(
            select(BrokerDeal).where(
                BrokerDeal.account_key == evidence.account_key,
                BrokerDeal.mode == stage,
                BrokerDeal.time >= evidence.started_at,
                BrokerDeal.time <= evidence.finished_at,
                BrokerDeal.position_identifier.is_(None),
            )
        ).all()
        # Unattributed charges are costs too; deposits/credit are NOT profit.
        charges = sum(
            (
                row.profit + row.commission + row.swap + row.fee
                for row in unallocated
                if row.type not in {"balance", "credit"}
            ),
            Decimal("0"),
        )
        losses += max(Decimal("0"), -charges)
        if losses <= 0 or wins / losses < cfg.stage_min_profit_factor:
            raise TradingDisabled("stage lacks a finite qualifying cost-inclusive ledger profit factor")
        if abs(wins / losses - Decimal(str(evidence.metrics_json["profit_factor"]))) > Decimal("0.000001"):
            raise TradingDisabled("reported profit factor disagrees with its ledger")
        snapshots = session.scalars(
            select(AccountSnapshot)
            .where(
                AccountSnapshot.account_key == evidence.account_key,
                AccountSnapshot.mode == stage,
                AccountSnapshot.time >= evidence.started_at,
                AccountSnapshot.time <= evidence.finished_at,
            )
            .order_by(AccountSnapshot.time, AccountSnapshot.id)
            .execution_options(yield_per=1000)
        )
        maximum_gap = cfg.risk_observation_max_age_seconds * 4
        peak, previous_cash, previous_time, drawdown, count = (
            Decimal("0"),
            None,
            evidence.started_at,
            Decimal("0"),
            0,
        )
        for snapshot in snapshots:
            count += 1
            if count > 1000000:
                raise TradingDisabled("stage exceeds bounded account journal; review/archive it")
            metadata = snapshot.metadata_json
            if (
                metadata.get("version") != 1
                or metadata.get("source") != "mt5"
                or metadata.get("code_hash") != self.profile.code_hash
                or metadata.get("model_sha256") != self.profile.model_sha256
                or metadata.get("strategy_config_hash") != cfg.strategy_fingerprint()
                or (snapshot.time - previous_time).total_seconds() > maximum_gap
            ):
                raise TradingDisabled("stage account source/code/continuous coverage is unverified")
            credit = Decimal(metadata["credit"])
            effective = snapshot.equity - credit
            if not credit.is_finite() or credit < 0 or effective <= 0:
                raise TradingDisabled("stage credit-adjusted equity is invalid")
            if previous_cash is None:
                previous_cash = snapshot.cash_flow_total
            peak = max(effective, peak + snapshot.cash_flow_total - previous_cash)
            drawdown = max(drawdown, (peak - effective) * 100 / peak)
            previous_cash, previous_time = snapshot.cash_flow_total, snapshot.time
        if not count or (evidence.finished_at - previous_time).total_seconds() > maximum_gap:
            raise TradingDisabled("stage start/end account coverage is incomplete")
        if drawdown > cfg.stage_max_drawdown_percent or drawdown > Decimal(
            str(evidence.metrics_json["max_drawdown_percent"])
        ) + Decimal("0.000001"):
            raise TradingDisabled("reported drawdown understates the sampled cash/credit-adjusted ledger")

    def _verify_trade_proofs(self, session, evidence, stage, trades):
        """Recheck original intent/deal evidence; labels alone do not confer eligibility."""
        intents = {
            row.id: row
            for row in session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == evidence.account_key, OrderIntent.mode == stage
                )
            ).all()
        }
        ledger = session.scalars(
            select(BrokerDeal).where(
                BrokerDeal.account_key == evidence.account_key,
                BrokerDeal.mode == stage,
                BrokerDeal.time >= evidence.started_at - timedelta(seconds=2),
                BrokerDeal.time <= evidence.finished_at,
            )
        ).all()
        indexed = {row.ticket: row for row in ledger}
        for trade in trades:
            meta = trade.features_json["execution"]
            intent = intents.get(trade.order_intent_id)
            if (
                intent is None
                or intent.state != "reconciled"
                or intent.request.get("authorized") is not True
                or intent.request.get("data_source") != "mt5"
                or intent.request.get("code_hash") != self.profile.code_hash
                or intent.request.get("model_sha256") != self.profile.model_sha256
                or intent.request.get("strategy_config_hash") != self.settings.strategy_fingerprint()
            ):
                raise TradingDisabled("stage trade lacks its original authorized native-provenance intent")
            entries = [indexed.get(ticket) for ticket in meta["entry_deal_tickets"]]
            if not entries or any(
                row is None
                or row.position_identifier != trade.position_identifier
                or row.magic != self.settings.mt5_magic_number
                or row.entry != "in"
                or row.type != trade.direction
                or row.symbol != trade.symbol
                for row in entries
            ):
                raise TradingDisabled("stage entry ledger ownership is unproved")
            legs = [row for row in ledger if row.position_identifier == trade.position_identifier]
            exits = [row for row in legs if row.entry in {"out", "out_by"} and row.type in {"buy", "sell"}]
            original = Decimal(meta["original_volume"])
            if (
                sum((row.volume for row in entries), Decimal("0")) != original
                or sum((row.volume for row in exits), Decimal("0")) != original
                or sum((row.profit + row.commission + row.swap + row.fee for row in legs), Decimal("0"))
                != trade.profit
            ):
                raise TradingDisabled("stage P&L/volumes are not backed by complete fill legs")

    def required_evidence(self, session: Session, account: AccountInfo) -> tuple[int, ...]:
        cfg, source = self.settings, self.profile.data_source
        if cfg.mode == OperatingMode.BACKTEST:
            return ()  # Run may be synthetic, but this NEVER qualifies its report.
        if cfg.mode == OperatingMode.PAPER and source in {SourceKind.SYNTHETIC, SourceKind.TEST_SDK}:
            return ()  # Development simulation only, explicitly ineligible for promotion.
        if source != SourceKind.MT5:
            raise TradingDisabled("native promotion requires verified real MT5 provenance")
        if cfg.mode == OperatingMode.DEMO and (
            account.source != SourceKind.MT5 or account.kind != AccountKind.DEMO
        ):
            raise TradingDisabled("demo stage requires the actual native DEMO account")
        if cfg.mode == OperatingMode.LIVE and (
            account.source != SourceKind.MT5 or account.kind != AccountKind.REAL
        ):
            raise TradingDisabled("live stage requires the actual native REAL account")
        stages = {
            OperatingMode.PAPER: ("backtest",),
            OperatingMode.DEMO: ("backtest", "paper"),
            OperatingMode.LIVE: ("backtest", "paper", "demo"),
        }[cfg.mode]
        selected = []
        previous_end = None
        for stage in stages:
            rows = session.scalars(
                select(DeploymentEvidence)
                .where(
                    DeploymentEvidence.stage == stage,
                    DeploymentEvidence.passed.is_(True),
                    DeploymentEvidence.revoked.is_(False),
                    DeploymentEvidence.code_hash == self.profile.code_hash,
                    DeploymentEvidence.model_sha256 == self.profile.model_sha256,
                    DeploymentEvidence.strategy_config_hash == cfg.strategy_fingerprint(),
                )
                .order_by(DeploymentEvidence.finished_at.desc())
                .limit(100)
            ).all()
            chosen = None
            for row in rows:
                try:
                    self._validate_evidence(row, stage, session)
                    if previous_end is not None and row.started_at < previous_end:
                        continue
                    chosen = row
                    break
                except (TradingDisabled, ValueError, TypeError, KeyError, OSError):
                    continue
            if chosen is None:
                raise TradingDisabled("missing valid chronological " + stage + " promotion evidence")
            selected.append(chosen.id)
            previous_end = chosen.finished_at
        return tuple(selected)

    def live_request_hash(self, account_key: str, session_id: str, evidence_ids: tuple[int, ...]) -> str:
        return sha256_json(
            {
                "purpose": "live_enable",
                "account_key": account_key,
                "session_id": session_id,
                "config_hash": self.settings.safety_fingerprint(),
                "code_hash": self.profile.code_hash,
                "model_sha256": self.profile.model_sha256,
                "evidence_ids": evidence_ids,
            }
        )

    def live_confirmed(
        self, session: Session, account: AccountInfo, session_id: str, evidence: tuple[int, ...]
    ) -> bool:
        digest = self.live_request_hash(account.key, session_id, evidence)
        rows = session.scalars(
            select(OwnerApproval).where(
                OwnerApproval.purpose == "live_enable",
                OwnerApproval.status == "approved",
                OwnerApproval.request_hash == digest,
                OwnerApproval.session_id == session_id,
                OwnerApproval.account_key == account.key,
                OwnerApproval.config_hash == self.settings.safety_fingerprint(),
                OwnerApproval.owner_id == self.settings.telegram_owner_id,
                OwnerApproval.expires_at > self.clock.now(),
            )
        ).all()
        return any(tuple(row.evidence_ids) == evidence for row in rows)

    def request_live(self, account: AccountInfo, session_id: str, owner_id: int) -> LiveChallenge:
        if (
            self.settings.mode != OperatingMode.LIVE
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may request live confirmation")
        nonce = secrets.token_urlsafe(32)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None or state.session_id != session_id or state.kill_switch_active:
                raise TradingDisabled("live confirmation runtime/session is invalid")
            evidence = self.required_evidence(session, account)
            digest = self.live_request_hash(account.key, session_id, evidence)
            expiry = self.clock.now() + timedelta(seconds=self.settings.live_approval_ttl_seconds)
            row = OwnerApproval(
                purpose="live_enable",
                request_hash=digest,
                nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                expires_at=expiry,
                owner_id=owner_id,
                session_id=session_id,
                account_key=account.key,
                config_hash=self.settings.safety_fingerprint(),
                evidence_ids=list(evidence),
                time=self.clock.now(),
            )
            session.add(row)
            session.flush()
            self.database.add_audit(
                session, "owner.live_requested", "owner", {"approval_id": row.id, "account": account.key}
            )
            challenge = LiveChallenge(row.id, nonce, digest, expiry)
        return challenge

    def confirm_live(self, approval_id: str, nonce: str, owner_id: int) -> None:
        if (
            self.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may confirm live")
        with self.database.locked_session() as session:
            row, state = session.get(OwnerApproval, approval_id), session.get(BotState, 1)
            if (
                row is None
                or state is None
                or row.status != "pending"
                or row.purpose != "live_enable"
                or row.owner_id != owner_id
                or row.session_id != state.session_id
                or row.config_hash != self.settings.safety_fingerprint()
                or row.expires_at <= self.clock.now()
                or not hmac.compare_digest(hashlib.sha256(nonce.encode()).hexdigest(), row.nonce_hash)
            ):
                raise TradingDisabled("live confirmation nonce/session/configuration/expiry is invalid")
            expected = self.live_request_hash(row.account_key, row.session_id, tuple(row.evidence_ids))
            if row.request_hash != expected:
                raise TradingDisabled("live confirmation payload changed")
            row.status, row.decided_at = "approved", self.clock.now()
            self.database.add_audit(
                session, "owner.live_confirmed", "owner", {"approval_id": row.id, "owner_id": owner_id}
            )
````

## File: `core/migrations.py`

<!-- SHA256: b15c16d60d643e2453fb5aa386fcdc8c8efdfd2de9290809632c963fe225b9b1 -->

````python
"""Explicit, backed-up SQLite migration. Never invoked by runtime/init-db."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from core.database import SCHEMA_VERSION, Database
from core.models import Base, BotState


def migrate_v1_to_v2(database: Database) -> Path:
    if database.engine.dialect.name != "sqlite" or database.engine.url.database == ":memory:":
        raise RuntimeError(
            "automatic migration supports file SQLite only; use the reviewed PostgreSQL SQL guide"
        )
    path = Path(database.engine.url.database)
    if not path.is_file():
        raise RuntimeError("existing schema-1 database is required")
    inspector = inspect(database.engine)
    if not set(Base.metadata.tables).issubset(inspector.get_table_names()):
        raise RuntimeError("unknown/incomplete schema; refuse migration")
    for name, table in Base.metadata.tables.items():
        expected = set(table.columns.keys()) - (
            {"metadata_json"} if name in {"risk_state", "account_snapshots"} else set()
        )
        if {row["name"] for row in inspector.get_columns(name)} != expected:
            raise RuntimeError("schema-1 columns do not match; refuse migration")
    # BEGIN IMMEDIATE also excludes an overlapping lease/intent reservation writer.
    with database.locked_session() as session:
        version = session.scalar(text("SELECT version FROM schema_version WHERE id=1"))
        state = session.get(BotState, 1)
        now = datetime.now(timezone.utc)
        if version != 1 or state is None:
            raise RuntimeError("only a complete schema-1 database may be migrated")
        if state.desired_state == "running" or (
            state.heartbeat
            and (now - state.heartbeat).total_seconds() < database.settings.runtime_lease_seconds
        ):
            raise RuntimeError("stop all runtimes and wait for the lease before migration")
        unresolved = session.scalar(
            text("SELECT COUNT(*) FROM order_intents WHERE state IN ('submitting','acknowledged','unknown')")
        )
        if unresolved:
            raise RuntimeError("unresolved executions require broker reconciliation before migration")
        # Separate read connection sees committed WAL state. No ORM financial SUM.
        backup_dir = database.settings.resolve_path(database.settings.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (
            "schema1-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".sqlite"
        )
        with sqlite3.connect(str(path)) as source, sqlite3.connect(str(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity failed; migration aborted")
        session.execute(text("ALTER TABLE risk_state ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
        session.execute(
            text("ALTER TABLE account_snapshots ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'")
        )
        session.execute(
            text("UPDATE risk_state SET metadata_json=:metadata"),
            {"metadata": json.dumps({"baseline_verified": False, "migration_review_required": True})},
        )
        session.execute(
            text("UPDATE schema_version SET version=:version WHERE id=1"), {"version": SCHEMA_VERSION}
        )
        database.add_audit(
            session,
            "database.migrated",
            "operator",
            {
                "from": 1,
                "to": SCHEMA_VERSION,
                "backup": str(backup.relative_to(database.settings.project_root)),
                "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "risk_latches_preserved": True,
            },
        )
    database.verify_schema()
    return backup
````

## File: `scripts/smoke_risk.py`

<!-- SHA256: 8c99ed649dc1d259dfb783ca6596cec3aea483140f2bb5229580156adf015cea -->

````python
"""Isolated SYNTHETIC risk/trailing/restart smoke. NEVER promotion evidence.

python -m scripts.smoke_risk
No .env/host credentials, native SDK import, Telegram/API connection or real order.
Temporary SQLite/checkpoints are destroyed after the test. Engineered quotes are
accounting/permission regression evidence, NOT a profitable trading strategy.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from core.database import Database
from core.models import OrderIntent, RiskState, Trade
from core.security import sha256_json
from core.settings import Settings
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import ManualClock, Side, SourceKind, TradingDisabled


class SmokeSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # No host environment/.env/credential loading.


async def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="reflex-risk-synthetic-") as directory:
        cfg = SmokeSettings(
            _env_file=None,
            project_root=Path(directory),
            symbols=("EURUSD",),
            telegram_owner_id=1,
            telegram_bot_token="123456789:TEST_ONLY_NOT_USED",
            max_slippage_points=2,
            atr_trailing_enabled=False,
        )
        database = Database(cfg)
        database.initialize()
        clock = ManualClock(datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
        broker = MockMT5Client(cfg, clock=clock)
        engine = ExecutionEngine(broker, database, cfg)
        restarted = None
        try:
            await engine.initialize()
            context = DecisionContext(
                clock.now(),
                clock.now() - timedelta(seconds=60),
                SourceKind.SYNTHETIC,
                90,
                90,
                NewsWindow(
                    True,
                    True,
                    clock.now(),
                    clock.now(),
                    clock.now() + timedelta(hours=1),
                    sha256_json({"TEST_NEWS": 1}),
                ),
            )
            plan = await engine.calculator.plan_market_order(
                "EURUSD",
                Side.BUY,
                Decimal("1.09780"),
                strategy="synthetic_risk_smoke",
                idempotency_key=sha256_json({"synthetic_entry": 1}),
            )
            if plan is None:
                raise RuntimeError("synthetic test minimum lot cannot fit")
            blocked = False
            try:
                await engine.execute(plan, context)
            except TradingDisabled:
                blocked = True
            assert blocked and await broker.get_positions() == ()
            # Use a NEW intent after the definitive startup-pause veto, never retry it.
            plan = await engine.calculator.plan_market_order(
                "EURUSD",
                Side.BUY,
                Decimal("1.09780"),
                strategy="synthetic_risk_smoke",
                idempotency_key=sha256_json({"synthetic_entry": 2}),
            )
            engine.control.resume(1, account_key=engine.account_key)  # Synthetic test principal only.
            filled = await engine.execute(plan, context)
            assert await engine.execute(plan, context) == filled
            engine.control.pause(1)
            await broker.set_tick("EURUSD", Decimal("1.10165"), Decimal("1.10177"))
            manager = PositionManager(engine)
            await manager.cycle()
            with database.session() as session:
                trade = session.scalar(select(Trade))
                assert trade.profit_lock_level == 30
                locked_sl = trade.sl
            before = await broker.get_account_info()
            prior_session = engine.control.session_id
            await engine.shutdown()
            restarted = ExecutionEngine(
                MockMT5Client(cfg, clock=clock, quotes={"EURUSD": (Decimal("1.10165"), Decimal("1.10177"))}),
                database,
                cfg,
            )
            await restarted.initialize()
            assert (
                restarted.database.status()["state"] == "paused"
                and restarted.control.session_id != prior_session
            )
            # Synthetic fixture quotes must be restored explicitly; checkpoint is
            # shadow execution state, never a replacement for fresh market data.
            await restarted.broker.set_tick("EURUSD", Decimal("1.10165"), Decimal("1.10177"))
            assert await restarted.execute(plan, context) == filled
            owned = restarted.logger.owned(restarted.account_key)[0]
            assert owned.ticket != filled.order_ticket
            restarted.control.kill(1)
            await PositionManager(restarted).close(owned.identifier, owner_id=1)
            assert await restarted.broker.get_positions() == ()
            final = await restarted.broker.get_account_info()
            with database.session() as session:
                trade = session.scalar(select(Trade))
                risk = session.scalar(select(RiskState))
                intents = session.scalars(select(OrderIntent)).all()
                assert trade.status == "closed" and final.balance == cfg.paper_initial_balance + trade.profit
                assert risk.accepted_entries_today == 1 and risk.reserved_risk_usd == 0
                report = {
                    "source": "synthetic",
                    "simulated_only": True,
                    "eligible_stage_evidence": False,
                    "native_sdk_imported": "MetaTrader5" in sys.modules,
                    "real_orders_sent": 0,
                    "startup_pause_veto_verified": blocked,
                    "duplicate_entry_sent_once": True,
                    "restart_started_paused": True,
                    "protective_management_while_paused_and_killed": True,
                    "verified_historical_lock_level": trade.profit_lock_level,
                    "protected_sl": str(locked_sl),
                    "volume": str(plan.order.volume),
                    "risk_account": str(plan.worst_loss_account),
                    "risk_budget_account": str(plan.risk_budget_account),
                    "original_target_usd": str(plan.target_profit_usd),
                    "net_simulated_account": str(trade.profit),
                    "ending_simulated_balance": str(final.balance),
                    "pre_restart_balance": str(before.balance),
                    "entries_today": risk.accepted_entries_today,
                    "reserved_risk_usd": str(risk.reserved_risk_usd),
                    "intent_states": sorted(row.state for row in intents),
                    "positions": 0,
                    "warning": "Engineered quotes; NOT profitability/backtest/promotion/live permission.",
                }
            return report
        finally:
            if restarted is not None:
                await restarted.shutdown()
            else:
                await engine.shutdown()
            database.close()


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

## File: `.env.example`

<!-- SHA256: d6746bbf8d1d44e2653a701874b8d2e34834e84b931ca7686b734b962ee3f018 -->

````dotenv
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
# Managed shadow state: missing/corrupt checkpoint NEVER resets capital.
PAPER_STATE_FILE=data/paper/state.json
# One DB-backed runtime lease; Part 10 renews it on every scheduler cycle.
RUNTIME_LEASE_SECONDS=90
RISK_OBSERVATION_MAX_AGE_SECONDS=30

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
````

## File: `requirements.txt`

<!-- SHA256: 0f53c818ff179933352d919be792ccc545cf383d96e6ac0c998bf18b6e960fdf -->

````text
# Direct dependencies pinned to an available, Python 3.11-compatible baseline.
# Generate and audit a platform-specific transitive lock after validation;
# do not reuse a Linux lock for the Windows MT5 deployment.
pydantic==2.13.5
pydantic-settings==2.15.0
python-dotenv==1.2.4
SQLAlchemy==2.1.2
fastapi==0.142.2
uvicorn==0.54.0
aiogram==3.31.0
APScheduler==3.11.3
httpx==0.28.1
pandas==3.0.6
numpy==2.4.6
ta==0.11.0
scikit-learn==1.9.1
lightgbm==4.7.0
joblib==1.6.0
feedparser==6.0.14
defusedxml==0.7.1
psutil==7.2.2
tzdata==2026.4
pytest==9.1.1
pytest-asyncio==1.4.0
MetaTrader5==5.0.6231; sys_platform == "win32"

# Optional PostgreSQL, packaging and advanced ML; install explicitly as needed:
# psycopg[binary]==3.3.6
# pyinstaller==6.22.3
# torch  # choose a tested CPU/CUDA build for your platform; not required.
# pip-audit==2.10.1  # deployment dependency audit, not a runtime dependency.
````

## File: `requirements.linux.lock.txt`

<!-- SHA256: 702895ba0e5aee861ae9ef2a07bfd7385339b9379be277b270b5f780aaec0d20 -->

````text
aiofiles==25.1.0
aiogram==3.31.0
aiohappyeyeballs==2.7.1
aiohttp==3.14.3
aiosignal==1.4.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
APScheduler==3.11.3
attrs==26.1.0
certifi==2026.7.22
click==8.5.0
cloudpickle==3.1.2
defusedxml==0.7.1
fastapi==0.142.2
feedparser==6.0.14
feedparser-sgmllib==2.1.0
frozenlist==1.8.0
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
iniconfig==2.3.0
joblib==1.6.0
lightgbm==4.7.0
magic-filter==1.0.12
multidict==6.9.1
narwhals==2.26.0
numpy==2.4.6
opentelemetry-api==1.45.0
packaging==26.3
pandas==3.0.6
pluggy==1.6.0
propcache==0.5.4
psutil==7.2.2
pydantic==2.13.5
pydantic-settings==2.15.0
pydantic_core==2.46.5
Pygments==2.21.0
pytest==9.1.1
pytest-asyncio==1.4.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
ruff==0.16.10
scikit-learn==1.9.1
scipy==1.18.1
six==1.17.0
SQLAlchemy==2.1.2
starlette==1.7.0
ta==0.11.0
threadpoolctl==3.7.0
typing-inspection==0.4.4
typing_extensions==4.16.0
tzdata==2026.4
tzlocal==5.4.4
uvicorn==0.54.0
yarl==1.25.1
````

## File: `README.md`

<!-- SHA256: d3c71947c59a19ffcdb94af249b707945f7ae47a83fe3ac79939ff9d16701689 -->

````markdown
# MT5 AI ReflexBot

Selective, owner-controlled MT5 trading automation. **No profit guarantee.**

## Current release: Parts 1–5 (0.3.0 · schema 2)

Implemented: settings/database/logging/CLI, serialized Windows MT5 adapter,
MockMT5 and separate paper execution, cost-aware sizing, durable risk/owner/stage
controls, crash-safe execution/ownership reconciliation, atomic paper checkpoints,
net-USD profit locks/ATR trailing and position management. **444 tests pass.**
This is library code plus runnable diagnostics, **not the assembled bot**.

`main.py` has safe operator commands only: no trading loop, Telegram/Mini App
server or backtester. `MT5Client` still defaults to **DenyAllWrites**. Trusted
`ExecutionEngine` composition attaches the durable authority explicitly; native
entries cannot pass without real persisted signals, fresh news/AI and reviewed
stage evidence, whose producers arrive in Parts 6–11. Flags are not permission.
Direct unmanaged paper/mock calls remain developer simulation, not automation.

Read `docs/ARCHITECTURE.md` and `docs/PART_05_NOTES.md` for the current contracts.
`docs/PART_05.md` contains the complete Part 5 source/configuration/tests.
`docs/MIGRATIONS.md` explains the explicit backed-up schema-1 upgrade.
Historical `docs/PARTS_01_03.md` and `docs/PART_04.md` stay unchanged snapshots;
current repository files and `.env.example` are authoritative. Parts 6–11 are
planned, not fake stub implementations or claims of live readiness.

## Safety defaults

- `DEMO_MODE=true`, `LIVE_TRADING=false`, `PAPER_TRADING=true`.
- `MT5_BACKEND=mock`, `START_PAUSED=true`; no broker credentials required.
- 0.5% maximum nominal stop risk/entry; initial live cap 0.1%.
- 1.5% total nominal open risk; 3% daily equity-loss cap; 10% peak drawdown cap.
- 12 maximum entries/day, 3 simultaneous positions, one position per symbol.
- Six entries/day is a target only. No forced entries, martingale or averaging.
- SL and promotion gates cannot be disabled. Every restart is paused.
- Default $5 profit objective must pass net reward/risk >=1.1 and costs.
- No configured RSS/calendar coverage means unknown news risk, not safe.
- Kill switch/SL cannot guarantee a fill during gaps or disconnections.

## Windows local-PC setup (working foundation commands)

1. Install **Python 3.11 x64**. Enable the PATH option. Verify `py -3.11 --version`.
   Use x64 Python; the native MT5 wheel is Windows x64. Python 3.12+ is possible
   only after validating the broker package/dependencies on that version.
2. Install your broker's official MT5 terminal (Exness or another broker), not
   MT4. Launch it interactively and log into a **demo account**. Verify its server,
   actual account type and the available symbol names/suffixes.
3. Enable terminal Algo Trading. Under Expert Advisors options, ensure external
   Python API trading is **not disabled**. This is needed for later demo orders,
   not for read-only inspection. Check broker symbol trading hours/permissions.
4. Extract this repository to a local directory, for example
   `C:\Trading\mt5_ai_reflex_bot`, or clone your own private copy. No remote
   repository URL is invented or provided.
5. In PowerShell:

```powershell
Set-Location C:\Trading\mt5_ai_reflex_bot
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

Activation is optional; using the explicit venv executable avoids PowerShell
activation-policy changes. Run under your normal Windows account, not admin.

6. Edit `.env` locally. Keep paper/mock flags unchanged for this installment.
   Set `MT5_TERMINAL_PATH` to the real `terminal64.exe` path. Leave login,
   password and server ALL blank to attach to an already-authenticated terminal,
   or configure ALL three for explicit login. Never paste credentials into chat.
7. Create a bot using Telegram **@BotFather**. Put its token in
   `TELEGRAM_BOT_TOKEN` and your numeric user ID in `TELEGRAM_OWNER_ID`; configure
   both together. Keep Mini App/webhook values blank until Part 9. Telegram
   controls are not installed in this release.
8. Validate and initialize:

```powershell
.\.venv\Scripts\python.exe main.py check-config
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe -m pytest -q
```

Expected status: `mode: paper`, `state: paused`, `heartbeat: null`. Null is
intentional: there is no running trading loop. `init-db` is idempotent but never
resets existing kill state or silently migrates an incompatible schema.
For a prior schema-1 installation, stop all runtimes and follow
`docs/MIGRATIONS.md`; only then run the explicit `main.py migrate-db` command.
A fresh install does not need that command. The CLI never connects a broker.

9. Keep `.env`, data, backups and logs readable only by your Windows user. Example
   ACL tightening after creating `.env` (inspect the resulting ACLs):

```powershell
$Principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .env /inheritance:r /grant:r "${Principal}:(R,W)"
icacls data /inheritance:r /grant:r "${Principal}:(OI)(CI)F" /T
```

ACL rules depend on your organization; do not lock yourself out. Use full-disk
protection where available. Add exclusions for sensitive paths in backups/shares.

## Synthetic diagnostics and read-only inspection

```powershell
# Pure synthetic execution: no terminal, credentials, providers or network.
.\.venv\Scripts\python.exe -m scripts.smoke_mock
# Isolated durable risk / pause / trailing / restart / kill / close test.
.\.venv\Scripts\python.exe -m scripts.smoke_risk

# Explicit local WINDOWS read-only broker-data inspection; may launch/login MT5.
# Configure .env locally first; this forces paper mode and never sends orders.
.\.venv\Scripts\python.exe -m scripts.check_mt5_readonly --env-file .env
```

The mock smoke ignores host mode/credentials, makes one deliberately engineered
artificial trade, verifies duplicate handling and complete deal/balance accounting,
and labels its results **ineligible for promotion**. It is not a backtest or a
profitability demonstration. The read-only diagnostic is not live authorization.

Never install a test write authority into runtime code. Mock contract tables are
not broker specifications; native sizing uses the actual broker's valuation.
Timeouts/cancellation/partial/accepted/unknown acknowledgements quarantine writes;
reconnect does not clear the latch or justify resubmission. Python cannot safely
kill a hung native worker. Keep server-side protection and reconcile durable
intents before owner-controlled recovery.

Managed paper execution now checkpoints every mutation automatically before SQL
acknowledgement, including read-triggered exits. Missing/corrupt state with a prior
DB footprint cannot reset capital. Owner/stage/risk services and trailing are
implemented; their scheduler, authenticated transport, providers and strategy
publishers arrive later. Do not operate this library unattended/native yet.
See `docs/PART_05_NOTES.md` for exact ownership, uncertainty and achievable-lock rules.

## Mode selection (configuration validation, not trading authorization)

| Mode | BACKTEST_MODE | DEMO_MODE | PAPER_TRADING | LIVE_TRADING | MT5_BACKEND |
|---|---|---|---|---|---|
| Backtest | true | true | false | false | mock |
| Paper with mock data | false | true | true | false | mock |
| Paper with broker data | false | true | true | false | real |
| Broker demo | false | true | false | false | real |
| Broker live | false | false | false | true | real |

Broker execution is NOT enabled by this table alone. The durable authority verifies
actual account type/identity, staged evidence, protection, risk and an owner
approval. No Mini App setting will directly toggle live order permission.

## Operational deployment sequence (implemented in later parts)

Do not run the commands below until those files/modules have been supplied:

1. **Part 4–6**: configure real symbol aliases, points/tick sizes, lot steps,
   commission, swap and currency conversion. Test the MT5 adapter read-only.
   Keep `PAPER_TRADING=true`; a real data backend still uses simulated execution.
2. **Part 7–8**: run Ollama with the configured model or an explicitly configured
   cloud provider. Provider failure rejects entries; no fabricated confidence.
   Install reliable, entitled RSS/API sources and economic-calendar coverage.
   Check feed timestamps, UTC coverage horizon and high-impact windows.
3. **Part 9**: host the Mini App with HTTPS through a reverse proxy or tunnel.
   Do not forward raw port 8000 publicly. Add the exact public host to
   `API_TRUSTED_HOSTS_JSON`, put the HTTPS app URL in `TELEGRAM_MINIAPP_URL`,
   configure BotFather's menu button and open it INSIDE Telegram. Static HTML
   opened alone is not an authenticated trading panel. Test owner access and
   confirm every other user and forged/expired initData receives a denial.
   No bot token or initData may appear in the app URL; the frontend sends
   authenticated headers to same-origin relative API routes.
4. **Part 10**: run the assembled foreground runtime, inspect `/health`, then
   test `/status`, `/positions`, `/pause`, `/resume`, confirmations and kill switch.
   Test protective monitoring while entries are paused. Use polling OR webhooks.
5. **Part 11**: chronological cost-aware backtest, then >=14-day paper stage,
   then >=14-day broker-demo stage, each with sufficient trades and reviewed
   metrics. Persist hashed evidence for the same strategy/code/model. Mock-only
   reports cannot qualify as real demo/live evidence. Failed gates mean no
   promotion; do not edit the database to manufacture success.
6. Start `scripts/run_mt5_background.vbs` to launch MT5 minimized then the bot
   hidden. `scripts/run_bot_background.vbs` starts only the bot. Install the
   logon task using `scripts/install_task_scheduler.ps1`; its logon type must
   be **Interactive**, and 'run whether user is logged on or not' must be OFF.
   Hidden/minimized is not a logout-proof Windows service.
7. Disable sleep while on AC power, not Windows security lock:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

   Locking the screen preserves the session; signing out does not. Schedule
   Windows maintenance/patching outside open-trade windows; reconnect and review
   state after every restart. Maintain time synchronization, power and internet.
8. Small live only after explicit owner confirmation. Set live flags manually,
   restart paused, verify the REAL account/server, inspect evidence, and approve
   the exact account/config/session with an expiry. Start at the 0.1% cap. Risk
   increases/new models require review and renewed validation. Expiring approval
   stops entries but must not prevent safe protective modification/closure.

The deployment scripts and `main.py run` command are deliberately absent until
Part 10. Background/watchdog operation must not be simulated by an empty loop.

## Optional Windows VPS

Use a **Windows** VPS with an interactive user session for native MT5. Install
x64 Python and the official broker terminal as above. Restrict RDP through a VPN
or IP allowlist, enable updates/2FA where available, and do not share credentials.
Use logon-triggered interactive tasks, a TLS proxy for the Mini App, and firewall
rules denying inbound API/database ports. RDP disconnect normally leaves the
session running; user logoff ends this supported environment. Session policy
must be verified on your provider. Never auto-resume live trading after reboot.
A Linux VPS can host a separately secured UI/backend, but cannot run this native
MT5 Python integration; a remote broker bridge is outside this implementation.

## Linux/macOS development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python main.py check-config
.venv/bin/python main.py init-db
.venv/bin/python -m pytest -q
```

The platform marker skips MetaTrader5. The native adapter imports it lazily only
on Windows. MockMT5Client is implemented for synthetic simulation, not a bridge.
Run `.venv/bin/python -m scripts.smoke_mock` and `-m scripts.smoke_risk` for
the isolated deterministic checks. Neither consumes host credentials or qualifies
as a strategy evaluation, real-market paper stage or trading authorization.

## Database, logs and dependency reproducibility

SQLite uses WAL, FULL synchronous writes, foreign keys, busy timeout, exact
Decimal-text financial columns and UTC conversion. Aggregate money in Python
Decimal, not SQL SUM(text). Sessions are per operation/thread. Audit changes
roll back with their business transaction. SQLite triggers block audit deletion
and updates; administrators can still modify database files.

PostgreSQL is optional: install `psycopg[binary]==3.3.6`, configure a dedicated
`postgresql+psycopg://...` database, initialize with a migration/admin role, then
use a restricted runtime role. Grant only SELECT/INSERT on audit_logs; grant
needed DML on the other tables and needed sequence access. Do not use a broker
machine's administrator or database superuser credentials in `.env`.
PostgreSQL integration still requires a real-server test before deployment.

Fresh installations create schema 2. An existing schema 1 fails closed until an
explicit stopped-runtime `main.py migrate-db` upgrade. That command verifies old
columns, checks leases/unresolved intents, creates a consistent SQLite backup,
checks integrity, preserves latches and marks old risk baselines unverified.
Read `docs/MIGRATIONS.md` first; it also gives a reviewed PostgreSQL procedure.
Keep DB and matching paper checkpoint together. Do not copy a live `.db` while
omitting its WAL or reset a ledger to manufacture results. Backups/restore and
runtime-role permissions must be verified on the deployment platform.

Direct dependencies are pinned. Freeze the successfully tested deployment
venv on each platform separately, review for private index URLs, and audit it:

```powershell
.\.venv\Scripts\python.exe -m pip freeze > requirements.windows.lock.txt
.\.venv\Scripts\python.exe -m pip install pip-audit==2.10.1
.\.venv\Scripts\python.exe -m pip_audit -r requirements.windows.lock.txt
```

Resolve advisories and rerun tests before network exposure. Pins are not a
security certification; transitive versions must be locked per deployment.
Only known-secret and pattern redaction is guaranteed by tests. Never add code
that logs raw auth data, passwords, provider bodies or complete settings.

## Validation scope

See `docs/VALIDATION.md` for executed commands/results: 444 tests, Ruff,
compileall, dependency consistency, explicit SQLite migration, CLI and synthetic
risk/trailing/restart smoke. Native tests use
marked fake SDKs only. No Windows terminal, broker demo/live orders, strategy
backtest, AI/news providers or Telegram integration were exercised. No live
permission has been granted and no real broker orders have been sent.
````

## File: `.gitignore`

<!-- SHA256: bab5e88bfd0644d7264483e858ca40b4fbbc9a48ba6e9d8d97e8acd5f2d3a22a -->

````text
# Secrets
.env
.env.*
!.env.example
*.pem
*.key

# Python / tools
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage/
htmlcov/
*.egg-info/

# Local runtime state, market data, models, logs and backups
data/**
!data/**/
!data/**/.gitkeep
*.db
*.db-shm
*.db-wal
*.sqlite*

# Build artifacts / OS
build/
dist/
*.spec
.DS_Store
Thumbs.db
.vscode/
.idea/
# Lock files may contain private package-index credentials; review before commit.
requirements.*.lock.txt
````

## File: `pyproject.toml`

<!-- SHA256: 8e354759e386e10a80c28144eb3d1c009c14520805ec9f55a87f424537b99d93 -->

````toml
[tool.pytest.ini_options]
minversion = "8.2"
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.ruff]
target-version = "py311"
line-length = 110

[tool.ruff.lint]
select = ["E", "F", "I", "B"]
````

## File: `main.py`

<!-- SHA256: 35775a41d15cfffcf8583cfb3fb80af8c7a8d5b82cb539605664f9400a1113b7 -->

````python
"""Safe operator commands. This CLI never connects to MT5 or places orders."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from core.database import Database
from core.logging_setup import configure_logging
from core.settings import Settings, get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MT5 AI ReflexBot: safe non-trading operator commands")
    parser.add_argument("command", choices=("check-config", "init-db", "status", "migrate-db"))
    parser.add_argument("--env-file", type=Path, help="explicit .env path; must exist")
    args = parser.parse_args(argv)
    if args.env_file and not args.env_file.is_file():
        print("Explicit environment file does not exist.", file=sys.stderr)
        return 2
    try:
        settings = Settings(_env_file=args.env_file) if args.env_file else get_settings()
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "mode/safety invariants"
                for error in exc.errors()
            }
        )
        # Do not print pydantic error dictionaries: they can contain raw inputs.
        print("Invalid configuration in: " + ", ".join(fields), file=sys.stderr)
        return 2
    except (SettingsError, ValueError) as exc:
        print(f"Configuration loading failed ({type(exc).__name__}); check .env syntax.", file=sys.stderr)
        return 2
    database: Database | None = None
    try:
        configure_logging(settings)
        if args.command == "check-config":
            print(json.dumps(settings.public_config(), indent=2))
            return 0
        database = Database(settings)
        if args.command == "init-db":
            database.initialize()
        migration = {}
        if args.command == "migrate-db":
            from core.migrations import migrate_v1_to_v2

            backup = migrate_v1_to_v2(database)
            migration = {"migration": "1_to_2", "backup": str(backup.relative_to(settings.project_root))}
        print(json.dumps(database.status() | migration, indent=2))
        return 0
    except Exception:
        logging.getLogger("reflexbot.operator").exception(
            "Operator command failed; this CLI did not connect to a broker"
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
````

## File: `config.py`

<!-- SHA256: 690dcc945ccf55cb6c453b8b996ace5e98c2c3432c8ce8b1e57e7b865c161a46 -->

````python
"""Compatibility import; settings are loaded explicitly, never at import time."""

from core.settings import OperatingMode, Settings, get_settings

__all__ = ["OperatingMode", "Settings", "get_settings"]
````

## File: `core/__init__.py`

<!-- SHA256: 7caf0be498f8ddf6d30edb7cee9406eec51b036ed2be98620e78bf1385a75a78 -->

````python
"""Safety-critical shared infrastructure. Imports have no runtime side effects."""

__version__ = "0.3.0"
````

## File: `core/database.py`

<!-- SHA256: 89a4da9c8da3718b0f9b3d8e05df3649c523319380c8f749d9f3040064af2453 -->

````python
"""Explicit schema initialization; no runtime auto-migration or global sessions."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import AuditLog, Base, BotState, SchemaVersion
from core.security import canonical_json, sanitize_data, secret_values
from core.settings import OperatingMode, Settings

SCHEMA_VERSION = 2
AUDIT_TRIGGERS = {
    "audit_logs_no_update": "UPDATE",
    "audit_logs_no_delete": "DELETE",
}


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.secrets = secret_values(settings)
        url = make_url(settings.database_url.get_secret_value())
        options: dict[str, Any] = {"pool_pre_ping": True, "echo": False}
        if url.get_backend_name() == "sqlite":
            if url.query:
                raise ValueError("SQLite URI/query overrides are forbidden")
            if url.database == ":memory:":
                if settings.mode in {OperatingMode.DEMO, OperatingMode.LIVE}:
                    raise ValueError("broker execution requires persistent risk state")
                options["poolclass"] = StaticPool
            else:
                if not url.database:
                    raise ValueError("SQLite database path is required")
                path = settings.resolve_path(url.database)
                path.parent.mkdir(parents=True, exist_ok=True)
                url = url.set(database=str(path))
            options["connect_args"] = {
                "check_same_thread": False,
                "timeout": settings.database_busy_timeout_ms / 1000,
            }
        elif url.get_backend_name() == "postgresql":
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            if url.drivername != "postgresql+psycopg":
                raise ValueError("PostgreSQL support requires psycopg 3")
            options["connect_args"] = {"connect_timeout": 10}
        else:
            raise ValueError("only SQLite and PostgreSQL are supported")
        self.engine: Engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._sqlite_connect)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)

    def _sqlite_connect(self, connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={self.settings.database_busy_timeout_ms}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        # A new session per operation/thread; callers can atomically write a
        # decision, order intent, reserved risk and its audit entry together.
        with self.session_factory.begin() as session:
            yield session

    @contextmanager
    def locked_session(self) -> Iterator[Session]:
        """Serialize risk/intent/control writes ACROSS processes, never only an RLock."""
        with self.session_factory() as session:
            try:
                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    session.execute(select(BotState).where(BotState.id == 1).with_for_update())
                yield session
                session.flush()
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def initialize(self) -> None:
        """Operator command only. Existing schemas are checked, never altered."""
        existing = set(inspect(self.engine).get_table_names())
        if existing:
            self.verify_schema()
            return
        with self.engine.begin() as connection:
            Base.metadata.create_all(connection)
            self._install_audit_triggers(connection)
        with self.session() as session:
            session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            session.add(BotState(id=1, desired_state="paused", kill_switch_active=False))
            self.add_audit(session, "database.initialized", "operator", {"schema_version": SCHEMA_VERSION})
        self.verify_schema()

    def _install_audit_triggers(self, connection: Connection) -> None:
        if connection.dialect.name != "sqlite":
            return
        for name, operation in AUDIT_TRIGGERS.items():
            # Both identifiers are fixed constants, never user input.
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE {operation} ON audit_logs "
                "BEGIN SELECT RAISE(ABORT, 'audit logs are append-only'); END"
            )

    def verify_schema(self) -> None:
        inspector = inspect(self.engine)
        actual = set(inspector.get_table_names())
        expected = set(Base.metadata.tables)
        if not expected.issubset(actual):
            raise RuntimeError(
                "database is uninitialized or incomplete; run init-db on an empty dedicated database"
            )
        with self.session() as session:
            versions = session.scalars(select(SchemaVersion)).all()
            if len(versions) != 1 or versions[0].id != 1 or versions[0].version != SCHEMA_VERSION:
                raise RuntimeError("database schema version mismatch; apply a reviewed migration")
            if session.get(BotState, 1) is None:
                raise RuntimeError("persistent bot state is missing; refuse to trade")
        for name, table in Base.metadata.tables.items():
            if {column["name"] for column in inspector.get_columns(name)} != set(table.columns.keys()):
                raise RuntimeError("database schema drift detected; refuse to trade")
        if self.engine.dialect.name == "sqlite":
            with self.engine.connect() as connection:
                triggers = set(
                    connection.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
                )
            if not set(AUDIT_TRIGGERS).issubset(triggers):
                raise RuntimeError("append-only audit protection is missing")

    def add_audit(self, session: Session, action: str, source: str, details: dict[str, Any]) -> AuditLog:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", action) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,64}", source
        ):
            raise ValueError("invalid audit action/source")
        clean = sanitize_data(details, self.secrets)
        if len(canonical_json(clean).encode("utf-8")) > 16384:
            raise ValueError("audit payload exceeds 16 KiB")
        row = AuditLog(action=action, source=source, details=clean)
        session.add(row)
        return row

    def audit(self, action: str, source: str, details: dict[str, Any]) -> None:
        with self.session() as session:
            self.add_audit(session, action, source, details)

    def status(self) -> dict[str, Any]:
        self.verify_schema()
        with self.session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise RuntimeError("persistent bot state disappeared; refuse to trade")
            return {
                "database": "ok",
                "schema_version": SCHEMA_VERSION,
                "mode": self.settings.mode.value,
                "state": state.desired_state,
                "kill_switch": state.kill_switch_active,
                "revision": state.revision,
                "heartbeat": state.heartbeat.isoformat() if state.heartbeat else None,
            }

    def close(self) -> None:
        self.engine.dispose()
````

## File: `core/logging_setup.py`

<!-- SHA256: a07629d31f50a0047203424216b9bd2ce0c7163cee927468eff34dbf6d230d93 -->

````python
"""UTC JSON-lines rotating logs, including secret-safe tracebacks."""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from core.security import canonical_json, sanitize_text, secret_values

if TYPE_CHECKING:
    from core.settings import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        # Strict field allowlist; arbitrary logging extra/request bodies are not
        # serialized. JSON escapes newlines to prevent forged log entries.
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = "Invalid log formatting; raw body and arguments suppressed"
        payload = {
            "time": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": sanitize_text(record.name, self.secrets),
            "thread": sanitize_text(record.threadName, self.secrets),
            "message": sanitize_text(message, self.secrets),
        }
        if record.exc_info:
            payload["exception"] = sanitize_text(self.formatException(record.exc_info), self.secrets)
        if record.stack_info:
            payload["stack"] = sanitize_text(record.stack_info, self.secrets)
        return canonical_json(payload)


def configure_logging(settings: Settings) -> None:
    settings.ensure_runtime_dirs()
    formatter = RedactingFormatter(secret_values(settings))
    file_handler = RotatingFileHandler(
        settings.resolve_path(settings.log_file),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
        delay=False,
    )
    console_handler = logging.StreamHandler(sys.stderr)
    # Prevent logging internals from dumping raw args on formatter/I/O errors.
    logging.raiseExceptions = False
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.setLevel(settings.log_level)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # HTTP DEBUG logging can contain complete request data; never enable it.
    for name in ("httpx", "httpcore", "sqlalchemy.engine", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.WARNING)


def recent_log_lines(settings: Settings, limit: int = 100) -> list[str]:
    if not 1 <= limit <= 500:
        raise ValueError("log limit must be between 1 and 500")
    path = settings.resolve_path(settings.log_file)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=limit)
    secrets = secret_values(settings)
    return [sanitize_text(line.rstrip("\n"), secrets) for line in lines]
````

## File: `core/models.py`

<!-- SHA256: 663ba4ea5e131cd9f02e7786f910ec808f0c3dc3c54a6cc1f8df86637f75ac09 -->

````python
"""Portable SQLAlchemy schema with exact decimals and UTC-aware timestamps."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive timestamps are forbidden")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ExactDecimal(TypeDecorator[Decimal]):
    # SQLite NUMERIC affinity silently uses binary floats. Store decimal strings
    # there; aggregate money in Python Decimal, never SQL SUM(text_column).
    impl = String(64)
    cache_ok = True

    def __init__(self, scale: int = 8) -> None:
        if not 0 <= scale <= 12:
            raise ValueError("invalid decimal scale")
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(28, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | str | int | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (float, bool)):
            raise TypeError("use Decimal(str(value)), not floats/bools, for financial columns")
        number = Decimal(value)
        if not number.is_finite() or abs(number) >= Decimal(10) ** (28 - self.scale):
            raise ValueError("financial value is non-finite or exceeds precision")
        try:
            number = number.quantize(Decimal(1).scaleb(-self.scale), rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError("financial value exceeds precision") from exc
        return format(number, "f") if dialect.name == "sqlite" else number

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(str(value))


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    __table_args__ = (CheckConstraint("id = 1", name="ck_schema_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BrokerDeal(Base):
    """Deduplicated fill/cash-flow ledger; reads deposits, never initiates them."""

    __tablename__ = "broker_deals"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_deal_account_ticket"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_deal_mode"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    ticket: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    entry: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    magic: Mapped[int] = mapped_column(BigInteger, default=0)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(ExactDecimal(12), default=Decimal("0"))
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    fee: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    comment: Mapped[str] = mapped_column(String(128), default="")


class OrderIntent(Base):
    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_intent_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_intent_direction"),
        CheckConstraint(
            "state IN ('prepared','submitting','acknowledged','rejected','unknown','reconciled','canceled')",
            name="ck_intent_state",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="prepared", index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    broker_request_id: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_trade_account_ticket"),
        UniqueConstraint("account_key", "mode", "position_identifier", name="uq_trade_position_id"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_trade_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_trade_direction"),
        CheckConstraint("status IN ('open','closed','unknown')", name="ck_trade_status"),
        CheckConstraint("CAST(volume AS NUMERIC) > 0", name="ck_trade_volume"),
        CheckConstraint(
            "signal_score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_trade_scores"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    order_intent_id: Mapped[str] = mapped_column(ForeignKey("order_intents.id"), unique=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    sl: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    tp: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    initial_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    target_profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    profit_lock_level: Mapped[float] = mapped_column(Float, default=0)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, nullable=False)
    news_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    comment: Mapped[str] = mapped_column(String(128), default="")
    close_reason: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "mode", "symbol", "timeframe", "bar_time", "strategy", "config_hash", name="uq_signal_bar"
        ),
        CheckConstraint("direction IN ('buy','sell','wait')", name="ck_signal_direction"),
        CheckConstraint("score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_signal_scores"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    bar_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, default=0)
    final_decision: Mapped[str] = mapped_column(String(24), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class News(Base):
    __tablename__ = "news"
    __table_args__ = (
        CheckConstraint("impact IN ('low','medium','high','unknown')", name="ck_news_impact"),
        CheckConstraint("sentiment BETWEEN -1 AND 1", name="ck_news_sentiment"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(String(10), default="unknown")
    sentiment: Mapped[float] = mapped_column(Float, default=0)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AISuggestion(Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low','medium','high')", name="ck_suggestion_risk"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','expired')", name="ck_suggestion_status"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    suggestion: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), default="high")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    based_on_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_scope: Mapped[str] = mapped_column(String(12), default="candidate")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BotState(Base):
    __tablename__ = "bot_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_state_singleton"),
        CheckConstraint("desired_state IN ('paused','running','killed')", name="ck_state_name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    desired_state: Mapped[str] = mapped_column(String(10), default="paused")
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    session_id: Mapped[str | None] = mapped_column(String(36))
    settings_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_config_hash: Mapped[str | None] = mapped_column(String(64))
    heartbeat: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    balance: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RiskState(Base):
    __tablename__ = "risk_state"
    __table_args__ = (UniqueConstraint("account_key", "mode", name="uq_risk_account_mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    day_start_equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity_high_water: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    net_realized_today: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    accepted_entries_today: Mapped[int] = mapped_column(Integer, default=0)
    reserved_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    daily_loss_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    drawdown_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DeploymentEvidence(Base):
    __tablename__ = "deployment_evidence"
    __table_args__ = (CheckConstraint("stage IN ('backtest','paper','demo')", name="ck_evidence_stage"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str | None] = mapped_column(String(160))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OwnerApproval(Base):
    __tablename__ = "owner_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','consumed','expired')", name="ck_approval_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_is_append_only(*args: Any) -> None:
    raise RuntimeError("audit logs are append-only")
````

## File: `core/security.py`

<!-- SHA256: 9368dd4b4d61cc321ba0f882e2c9e7c5e70b96ed530766b872f030d189014ea6 -->

````python
"""Secret-safe JSON and logging helpers. Authentication is added in Part 9."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from pydantic import SecretStr

SENSITIVE_KEY = re.compile(
    r"password|passwd|token|api[_-]?key|secret|authorization|cookie|init[_-]?data|private[_-]?key",
    re.IGNORECASE,
)
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|token|api[_-]?key|secret|authorization|init[_-]?data)\b"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
BOT_URL_TOKEN = re.compile(r"(https?://api\.telegram\.org/(?:file/)?bot)[^/\s?]+", re.IGNORECASE)
URL_CREDENTIALS = re.compile(r"(https?://|postgresql(?:\+psycopg)?://)([^/@\s]+@)", re.IGNORECASE)


def secret_values(settings: object) -> tuple[str, ...]:
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if raw:
                values.append(raw)
    # Longer values first prevents partial redaction from exposing a suffix.
    return tuple(sorted(set(values), key=len, reverse=True))


def sanitize_text(text: object, secrets: Iterable[str] = ()) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = BEARER.sub("Bearer [REDACTED]", result)
    result = BOT_URL_TOKEN.sub(r"\1[REDACTED]", result)
    result = URL_CREDENTIALS.sub(r"\1[REDACTED]@", result)
    return ASSIGNMENT.sub(r"\1[REDACTED]", result)


def _json_default(value: object) -> object:
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite numbers are forbidden")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_data(value: Any, secrets: Iterable[str] = (), *, _depth: int = 0) -> Any:
    if _depth > 20:
        raise ValueError("audit payload exceeds nesting limit")
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("audit JSON keys must be strings")
        return {
            key: "[REDACTED]"
            if SENSITIVE_KEY.search(key)
            else sanitize_data(item, secrets, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item, secrets, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    if isinstance(value, (Decimal, datetime, Enum)):
        return _json_default(value)
    if value is None or isinstance(value, (bool, int, float)):
        # Final canonical_json check rejects NaN/Infinity.
        return value
    raise TypeError(f"unsupported audit type: {type(value).__name__}")
````

## File: `core/settings.py`

<!-- SHA256: 0b8bd903b37fe3746b3669b6ee9fc66af1e78a005ca047228710520ea5cff682 -->

````python
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
    paper_state_file: Path = Path("data/paper/state.json")
    runtime_lease_seconds: int = Field(default=90, ge=30, le=300)
    risk_observation_max_age_seconds: int = Field(default=30, ge=5, le=120)

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
        for path in (
            self.data_dir,
            self.backup_dir,
            self.log_file,
            self.calendar_file,
            self.paper_state_file,
        ):
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
        for child in ("candles", "news", "models", "logs", "backups", "paper"):
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
````

## File: `scripts/__init__.py`

<!-- SHA256: d8ac0dce6ba1119ad529f294cab0743f8b9ac5515f090798d50d63249f5ec119 -->

````python
"""Explicitly-invoked diagnostics. Importing this package starts nothing."""
````

## File: `scripts/check_mt5_readonly.py`

<!-- SHA256: a4facb2c11c97f335869d7e5c2a29315818668f750fad27918977ee2c73b5b1d -->

````python
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
````

## File: `scripts/smoke_mock.py`

<!-- SHA256: a39db3f1a9ae4303eb3216284b7cc755ac4e43ec386087252dd4a57bc5f323b8 -->

````python
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
````

## File: `tests/__init__.py`

<!-- SHA256: 03e5bffa78d5bfd503da0d9ab2620b069df3daba8af5bd912e712ef43b1a9e3e -->

````python
"""Isolated tests and TEST-ONLY broker SDK/authority fixtures. Not runtime code."""
````

## File: `tests/conftest.py`

<!-- SHA256: b497ad00f3c761a4f41728a9ba205a6a66e991d96a1ac8698e698a8395e6c274 -->

````python
"""Isolate tests from any real credentials or mode flags in the host environment."""

import pytest

from core.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # Env-file loading is disabled explicitly in each test.
    import os

    names = {name.lower() for name in Settings.model_fields}
    names |= {
        field.validation_alias.lower()
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }
    for name in list(os.environ):
        if name.lower() in names:
            monkeypatch.delenv(name)
````

## File: `tests/fake_mt5_sdk.py`

<!-- SHA256: 7838e4fd1a184696a933e5142e60575f063d6548fd76cd416307133e1c1de3a2 -->

````python
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
````

## File: `tests/risk_helpers.py`

<!-- SHA256: f92d9bb8257008b41efc1702b355a00c56d2018304d562a96f9ad7439eea3309 -->

````python
"""TEST ONLY synthetic contexts/owner. Never promotion evidence or real auth."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.database import Database
from core.settings import Settings
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import ManualClock, Side, SourceKind

D = Decimal
MOMENT = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
OWNER = 42


def config(tmp_path, **kwargs):
    values = dict(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD",),
        telegram_owner_id=OWNER,
        telegram_bot_token="123456789:TEST_ONLY_NEVER_CONTACT_TELEGRAM",
        max_slippage_points=2,
        atr_trailing_enabled=False,
    )
    if "max_daily_trades" in kwargs:
        values["min_daily_trades_target"] = min(6, kwargs["max_daily_trades"])
    values.update(kwargs)
    return Settings(**values)


def safe_context(clock, **kwargs):
    values = dict(
        observed_at=clock.now(),
        bar_closed_at=clock.now() - timedelta(seconds=60),
        source=SourceKind.SYNTHETIC,
        signal_score=90,
        ai_confidence=90,
        news=NewsWindow(True, True, clock.now(), clock.now(), clock.now() + timedelta(hours=1), "a" * 64),
    )
    values.update(kwargs)
    return DecisionContext(**values)


async def make_engine(tmp_path, *, clock=None, state_store=None, **kwargs):
    settings = config(tmp_path, **kwargs)
    database = Database(settings)
    database.initialize()
    broker = MockMT5Client(settings, clock=clock or ManualClock(MOMENT))
    engine = ExecutionEngine(broker, database, settings, state_store=state_store)
    await engine.initialize()
    return engine


async def open_one(engine, *, side=Side.BUY, key="1" * 64, resume=True):
    if resume:
        engine.control.resume(OWNER, account_key=engine.account_key)
    stop = D("1.09780") if side == Side.BUY else D("1.10232")
    plan = await engine.calculator.plan_market_order(
        "EURUSD", side, stop, strategy="TEST_SYNTHETIC", idempotency_key=key
    )
    assert plan is not None
    result = await engine.execute(plan, safe_context(engine.clock))
    return plan, result
````

## File: `tests/test_database.py`

<!-- SHA256: 477ae54bf7e0e527156903ed7a5832213a5636dd594bfa9974604cb5ae2c0491 -->

````python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, StatementError

from core.database import Database
from core.models import AccountSnapshot, AuditLog, BotState, SchemaVersion
from core.settings import Settings


@pytest.fixture
def database(tmp_path):
    settings = Settings(_env_file=None, project_root=tmp_path)
    db = Database(settings)
    db.initialize()
    yield db
    db.close()


def test_new_database_paused_and_idempotent(database):
    assert database.status()["state"] == "paused"
    assert database.status()["heartbeat"] is None
    database.initialize()
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_persistent_kill_switch_survives_reopen(database):
    with database.session() as session:
        state = session.get(BotState, 1)
        state.desired_state = "killed"
        state.kill_switch_active = True
    second = Database(database.settings)
    try:
        second.initialize()
        assert second.status()["kill_switch"]
        assert second.status()["state"] == "killed"
    finally:
        second.close()


def test_exact_decimals_and_utc_round_trip(database):
    moment = datetime(2026, 1, 1, 12, 34, 56, tzinfo=timezone.utc)
    money = Decimal("123456789012345.12345678")
    with database.session() as session:
        session.add(
            AccountSnapshot(
                time=moment,
                account_key="paper:test",
                mode="paper",
                currency="USD",
                balance=money,
                equity=money,
                margin=Decimal("0"),
                free_margin=money,
            )
        )
    with database.session() as session:
        row = session.scalar(select(AccountSnapshot))
        assert row.balance == money
        assert row.time == moment
        assert row.time.tzinfo is not None


def test_naive_timestamp_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(AuditLog(time=datetime(2026, 1, 1), action="invalid", source="test", details={}))


def test_financial_float_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(
                AccountSnapshot(
                    account_key="paper:test",
                    mode="paper",
                    currency="USD",
                    balance=1.23,
                    equity=Decimal("1.23"),
                    margin=Decimal("0"),
                    free_margin=Decimal("1.23"),
                )
            )


def test_audit_redacted(database):
    database.audit("test.event", "test", {"MT5_PASSWORD": "secret", "safe": "value"})
    with database.session() as session:
        row = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert row.details == {"MT5_PASSWORD": "[REDACTED]", "safe": "value"}


@pytest.mark.parametrize("statement", ["UPDATE audit_logs SET action='changed'", "DELETE FROM audit_logs"])
def test_sqlite_audit_append_only(database, statement):
    with pytest.raises(DBAPIError):
        with database.engine.begin() as connection:
            connection.execute(text(statement))


def test_schema_version_mismatch_refuses_startup(database):
    with database.session() as session:
        session.execute(update(SchemaVersion).values(version=999))
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        database.verify_schema()


def test_missing_state_refuses_startup(database):
    with database.session() as session:
        session.delete(session.get(BotState, 1))
    with pytest.raises(RuntimeError, match="persistent bot state"):
        database.verify_schema()


def test_transaction_rollback(database):
    with pytest.raises(RuntimeError):
        with database.session() as session:
            database.add_audit(session, "test.rollback", "test", {})
            raise RuntimeError("rollback")
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_unknown_schema_is_not_silently_modified(tmp_path):
    db = Database(Settings(_env_file=None, project_root=tmp_path))
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated (id INTEGER)"))
        with pytest.raises(RuntimeError):
            db.initialize()
    finally:
        db.close()
````

## File: `tests/test_execution.py`

<!-- SHA256: b4793efab870ff54842b6014ddf86cfb5021f77977282ffd45474f9681f7cdf7 -->

````python
import asyncio
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import AuditLog, BrokerDeal, OrderIntent, RiskEvent, RiskState, Trade
from tests.risk_helpers import OWNER, D, config, make_engine, open_one, safe_context
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import NewsWindow
from trading.types import BrokerCommand, Operation, ResultStatus, Side, TradingDisabled, UncertainExecution


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    yield result
    await result.shutdown()
    result.database.close()


async def test_paused_engine_rejects_and_logs_without_a_fill(engine):
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(TradingDisabled, match="paused"):
        await engine.execute(plan, safe_context(engine.clock))
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "rejected" and not row.request["reserved"] and not row.request["counted"]
        assert session.scalar(select(RiskEvent)).event == "entry_vetoed"
        assert any(row.action == "intent.authority_veto" for row in session.scalars(select(AuditLog)))


@pytest.mark.parametrize("context", ["unknown_news", "unsafe_news", "low_ai", "low_technical", "stale"])
async def test_quality_veto_never_forces_the_daily_target(engine, context):
    engine.control.resume(OWNER, account_key=engine.account_key)
    values = {
        "unknown_news": {"news": NewsWindow()},
        "unsafe_news": {"news": NewsWindow(known=True, safe=False)},
        "low_ai": {"ai_confidence": 0},
        "low_technical": {"signal_score": 0},
        "stale": {"observed_at": engine.clock.now() - timedelta(seconds=31)},
    }[context]
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(TradingDisabled):
        await engine.execute(plan, safe_context(engine.clock, **values))
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        assert session.scalar(select(RiskState)).accepted_entries_today == 0


async def test_exact_durable_duplicate_sends_once_under_concurrent_callers(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order(
        "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="1" * 64
    )
    results = await asyncio.gather(*(engine.execute(plan, safe_context(engine.clock)) for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert len(await engine.broker.get_positions()) == 1
    with engine.database.session() as session:
        assert len(session.scalars(select(OrderIntent)).all()) == 1
        assert len(session.scalars(select(BrokerDeal)).all()) == 1
        assert session.scalar(select(RiskState)).accepted_entries_today == 1
        trade = session.scalar(select(Trade))
        assert trade.ticket == 100001 and trade.position_identifier == 500001
        assert trade.ticket != results[0].order_ticket != trade.position_identifier


async def test_idempotency_payload_conflict_never_resends(engine):
    plan, _ = await open_one(engine)
    changed = replace(plan, order=replace(plan.order, sl=plan.order.sl - D("0.00001")))
    with pytest.raises(TradingDisabled, match="payload"):
        await engine.execute(changed, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1


async def test_restart_restores_equity_ownership_counters_and_cached_fill(engine):
    plan, filled = await open_one(engine)
    account = await engine.broker.get_account_info()
    before = engine.control.session_id
    await engine.shutdown()
    broker = MockMT5Client(engine.settings, clock=engine.clock)
    restarted = ExecutionEngine(broker, engine.database, engine.settings)
    try:
        await restarted.initialize()
        assert restarted.control.session_id != before and restarted.database.status()["state"] == "paused"
        assert (await broker.get_account_info()).balance == account.balance
        duplicate = await restarted.execute(plan, safe_context(engine.clock))
        assert duplicate == filled
        assert len(await broker.get_positions()) == 1
        with engine.database.session() as session:
            assert session.scalar(select(RiskState)).accepted_entries_today == 1
    finally:
        await restarted.shutdown()


async def test_missing_checkpoint_with_existing_database_is_not_a_capital_reset(engine):
    await open_one(engine)
    await engine.shutdown()
    engine.store.path.unlink()
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    with pytest.raises(TradingDisabled, match="NEVER reset"):
        await restarted.initialize()
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "open"


async def test_corrupt_checkpoint_refuses_restart_without_writing_over_it(engine):
    await open_one(engine)
    await engine.shutdown()
    engine.store.path.write_text("corrupt", encoding="utf-8")
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    with pytest.raises(Exception, match="corrupt"):
        await restarted.initialize()
    assert engine.store.path.read_text() == "corrupt"


@pytest.mark.parametrize("control", ["pause", "kill"])
async def test_maintenance_remains_enabled_while_paused_or_killed(engine, control):
    _, _ = await open_one(engine)
    owned = engine.logger.owned(engine.account_key)[0]
    getattr(engine.control, control)(OWNER)
    result = await engine.protect_sl(owned.ticket, owned.identifier, D("1.09900"))
    assert result.status == ResultStatus.FILLED
    closed = await PositionManager(engine).close(owned.identifier, owner_id=OWNER)
    assert closed.status == ResultStatus.FILLED
    assert await engine.broker.get_positions() == ()
    assert engine.database.status()["state"] in {"paused", "killed"}
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.status == "closed"
        rows = session.scalars(select(BrokerDeal)).all()
        assert trade.profit == sum(row.profit + row.commission + row.swap + row.fee for row in rows)
        assert trade.commission == D("-0.14")


async def test_manually_tagged_position_is_never_adopted_or_closed(engine):
    # Deliberate TEST-only foreign activity using a different standalone shadow ledger.
    foreign = MockMT5Client(engine.settings, clock=engine.clock)
    await foreign.initialize()
    try:
        plan = await foreign._calculator().plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="MANUAL_TEST"
        )
        await foreign.open_market_buy(plan.order)
        position = (await foreign.get_positions())[0]
        engine.broker._positions[position.identifier] = position
        engine.broker._margins[position.identifier] = D("22")
        engine.broker._original_tps[position.identifier] = position.tp
        summary = await engine.reconcile()
        assert summary["ledger_mismatch"] and not engine.logger.owned(engine.account_key)
        with pytest.raises(TradingDisabled):
            await PositionManager(engine).close(position.identifier, owner_id=OWNER)
    finally:
        await foreign.shutdown()


async def test_owner_close_requires_authenticated_owner_and_proved_ids(engine):
    await open_one(engine)
    owned = engine.logger.owned(engine.account_key)[0]
    with pytest.raises(TradingDisabled):
        await PositionManager(engine).close(owned.identifier, owner_id=OWNER + 1)
    with pytest.raises(TradingDisabled):
        await engine.close_owned(owned.ticket + 1, owned.identifier)
    assert len(await engine.broker.get_positions()) == 1


async def test_cancellation_retains_reservation_and_never_blindly_retries(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order(
        "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="b" * 64
    )
    entered, release = Event(), Event()
    original = engine.authority.before_send

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(engine.authority, "before_send", blocked)
    task = asyncio.create_task(engine.execute(plan, safe_context(engine.clock)))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.broker.health()["writes_quarantined"]
    assert await engine.broker.get_positions() == ()
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "unknown" and row.request["reserved"] and row.request["counted"]
        assert session.scalar(select(RiskState)).reserved_risk_usd > 0
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))


async def test_checkpoint_failure_after_mutation_quarantines_and_retains_risk(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)
    real_save = engine.store.save

    def fail_after_entry(state):
        if state["positions"]:
            raise OSError("TEST disk full password=do_not_log")
        return real_save(state)

    monkeypatch.setattr(engine.store, "save", fail_after_entry)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))
    assert engine.broker.health()["writes_quarantined"]
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "unknown" and row.request["reserved"]
        assert session.scalar(select(Trade)) is None
    assert engine.store.load()["positions"] == []


async def test_snapshot_precedes_ack_and_recovers_a_db_callback_crash(engine, monkeypatch):
    engine.control.resume(OWNER, account_key=engine.account_key)

    def crash(*args):
        raise RuntimeError("TEST callback crash")

    monkeypatch.setattr(engine.authority, "on_result", crash)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    with pytest.raises(UncertainExecution):
        await engine.execute(plan, safe_context(engine.clock))
    checkpoint = engine.store.load()
    assert checkpoint["positions"] and checkpoint["cache"]
    await engine.shutdown()
    restarted = ExecutionEngine(
        MockMT5Client(engine.settings, clock=engine.clock), engine.database, engine.settings
    )
    try:
        summary = await restarted.initialize()
        assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
        assert len(restarted.logger.owned(restarted.account_key)) == 1
        assert restarted.database.status()["state"] == "paused"
        restarted.control.acknowledge_recovery(
            OWNER, account_key=restarted.account_key, broker_writes_quarantined=False
        )
        restarted.control.resume(OWNER, account_key=restarted.account_key)
        result = await restarted.execute(plan, safe_context(engine.clock))
        assert result.status == ResultStatus.FILLED and len(await restarted.broker.get_positions()) == 1
    finally:
        await restarted.shutdown()


async def test_automatic_tp_is_persisted_even_when_triggered_by_read(engine):
    plan, _ = await open_one(engine)
    await engine.broker.market.set_tick("EURUSD", plan.order.tp, plan.order.tp + D("0.00012"))
    assert await engine.broker.get_positions() == ()
    checkpoint = engine.store.load()
    assert checkpoint["positions"] == [] and len(checkpoint["deals"]) == 2
    await engine.reconcile()
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "closed"
    assert await engine.execute(
        plan, safe_context(engine.clock)
    )  # Old ID still cannot reopen a closed position.
    assert await engine.broker.get_positions() == ()


async def test_post_authorization_owner_pause_veto_is_committed_and_releases_unsent_risk(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order)
    from trading.snapshots import broker_snapshot

    snapshot = await broker_snapshot(
        engine.broker.market,
        engine.settings,
        await engine.broker.get_account_info(),
        await engine.broker.get_symbol_info("EURUSD"),
        await engine.broker.get_tick("EURUSD"),
        (),
        plan.worst_loss_account,
        plan.margin_account,
        D("5.36"),
        durable_simulation=True,
    )
    engine.authority.stage(
        command, snapshot.account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
    )
    grant = engine.authority.authorize(command, snapshot)
    engine.control.pause(OWNER)
    with pytest.raises(TradingDisabled, match="changed before send"):
        engine.authority.before_send(command, snapshot, grant)
    from trading.types import ExecutionResult

    engine.authority.on_result(
        command,
        ExecutionResult(
            Operation.OPEN,
            command.idempotency_key,
            engine.account_key,
            ResultStatus.REJECTED,
            reason="TEST no send",
        ),
    )
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "rejected"
        assert session.scalar(select(RiskState)).reserved_risk_usd == 0
        assert session.scalar(select(RiskState)).accepted_entries_today == 0


async def test_one_daily_entry_is_a_limit_not_a_quota(tmp_path):
    engine = await make_engine(tmp_path, max_daily_trades=1)
    try:
        await open_one(engine)
        owned = engine.logger.owned(engine.account_key)[0]
        await engine.close_owned(owned.ticket, owned.identifier)
        plan = await engine.calculator.plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="TEST", idempotency_key="2" * 64
        )
        with pytest.raises(Exception, match="daily|count"):
            await engine.execute(plan, safe_context(engine.clock))
        assert await engine.broker.get_positions() == ()
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_unachievable_minimum_lot_is_skipped_never_rounded_up(tmp_path):
    engine = await make_engine(tmp_path, paper_initial_balance=D("10"))
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        result = await engine.open(
            "EURUSD", Side.BUY, D("1.09000"), safe_context(engine.clock), strategy="TEST"
        )
        assert result is None and await engine.broker.get_positions() == ()
    finally:
        await engine.shutdown()
        engine.database.close()


def test_managed_execution_requires_persistent_database(tmp_path):
    cfg = config(tmp_path, database_url="sqlite:///:memory:")
    db = Database(cfg)
    db.initialize()
    engine = ExecutionEngine(MockMT5Client(cfg), db, cfg)
    try:
        with pytest.raises(TradingDisabled, match="persistent"):
            asyncio.run(engine.initialize())
    finally:
        db.close()


async def test_post_ack_reconciliation_failure_halts_and_preserves_durable_fill(engine, monkeypatch):
    from core.models import BotState
    from trading.types import ConnectionUnavailable

    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    real_reconcile, calls = engine._reconcile, 0

    async def fail_after_ack():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionUnavailable("TEST post-ack history unavailable")
        return await real_reconcile()

    monkeypatch.setattr(engine, "_reconcile", fail_after_ack)
    with pytest.raises(ConnectionUnavailable, match="post-ack"):
        await engine.execute(plan, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1
    assert engine.store.load()["positions"] and engine.store.load()["cache"]
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "acknowledged" and row.request["result"]["status"] == "filled"
        assert row.request["reserved"] and session.scalar(select(RiskState)).reserved_risk_usd > 0
        assert session.get(BotState, 1).last_error == "unknown_execution"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    monkeypatch.setattr(engine, "_reconcile", real_reconcile)
    summary = await engine.reconcile()
    assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
    assert await engine.execute(plan, safe_context(engine.clock))
    assert len(await engine.broker.get_positions()) == 1
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
````

## File: `tests/test_foundation_cli.py`

<!-- SHA256: dea1599db0887eac59de43b73e755b2824fdbd0816f90bc5cce6a8faed13cf66 -->

````python
import json

from main import main


def test_check_config_does_not_init_database(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text(f'PROJECT_ROOT="{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "paper" and output["startup"] == "paused"
    assert not (tmp_path / "data/reflexbot.db").exists()


def test_explicit_missing_env_file_fails(tmp_path):
    assert main(["check-config", "--env-file", str(tmp_path / "missing.env")]) == 2


def test_invalid_config_output_does_not_expose_input(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text('TELEGRAM_OWNER_ID="not-an-id-secret"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 2
    assert "not-an-id-secret" not in capsys.readouterr().err
````

## File: `tests/test_migrations.py`

<!-- SHA256: 301472f7fd823c488ec7e5e25c4b2387716457f62fcc716d1a9011cbe9d7a817 -->

````python
import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select, text

from core.database import SCHEMA_VERSION, Database
from core.migrations import migrate_v1_to_v2
from core.models import BotState, RiskState
from tests.risk_helpers import MOMENT, D, config


def legacy(tmp_path, *, running=False, fresh_heartbeat=False):
    db = Database(config(tmp_path))
    db.initialize()
    with db.session() as session:
        state = session.get(BotState, 1)
        state.kill_switch_active, state.desired_state = not running, "running" if running else "killed"
        if fresh_heartbeat:
            state.heartbeat = datetime.now(timezone.utc)
        session.add(
            RiskState(
                account_key="paper:legacy",
                mode="paper",
                day=MOMENT.date(),
                day_start_equity=D("1000"),
                equity_high_water=D("1200"),
                daily_loss_latched=True,
                drawdown_latched=True,
                reserved_risk_usd=D("0"),
            )
        )
    with db.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE risk_state DROP COLUMN metadata_json")
        connection.exec_driver_sql("ALTER TABLE account_snapshots DROP COLUMN metadata_json")
        connection.exec_driver_sql("UPDATE schema_version SET version=1 WHERE id=1")
    return db


def test_schema1_requires_explicit_backed_up_migration(tmp_path):
    db = legacy(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            db.initialize()
        backup = migrate_v1_to_v2(db)
        assert backup.is_file() and backup.parent.name == "backups"
        assert len(hashlib.sha256(backup.read_bytes()).hexdigest()) == 64
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
            assert "metadata_json" not in {
                row[1] for row in connection.execute("PRAGMA table_info(risk_state)")
            }
        assert db.status()["schema_version"] == SCHEMA_VERSION == 2
        assert db.status()["kill_switch"] and db.status()["state"] == "killed"
        with db.session() as session:
            row = session.scalar(select(RiskState))
            assert row.daily_loss_latched and row.drawdown_latched and row.equity_high_water == 1200
            assert row.metadata_json["baseline_verified"] is False
            assert row.metadata_json["migration_review_required"] is True
    finally:
        db.close()


@pytest.mark.parametrize("case", ["running", "heartbeat", "unresolved", "drift"])
def test_migration_refuses_active_unknown_or_drifted_state(tmp_path, case):
    db = legacy(tmp_path, running=case == "running", fresh_heartbeat=case == "heartbeat")
    try:
        if case == "unresolved":
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO order_intents (id,idempotency_key,time,updated_at,expires_at,"
                        "mode,account_key,symbol,direction,state,request,config_hash) VALUES "
                        "('legacy',:key,:time,:time,:time,'paper','paper:legacy','EURUSD','buy','unknown','{}',:key)"
                    ),
                    {"key": "a" * 64, "time": MOMENT.replace(tzinfo=None).isoformat(sep=" ")},
                )
        if case == "drift":
            with db.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE risk_state ADD COLUMN unexpected INTEGER")
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT version FROM schema_version")) == 1
        assert "metadata_json" not in {row["name"] for row in inspect(db.engine).get_columns("risk_state")}
    finally:
        db.close()


def test_migration_is_not_silently_repeated(tmp_path):
    db = legacy(tmp_path)
    try:
        migrate_v1_to_v2(db)
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
    finally:
        db.close()


def test_inmemory_migration_is_forbidden(tmp_path):
    db = Database(config(tmp_path, database_url="sqlite:///:memory:"))
    try:
        db.initialize()
        with pytest.raises(RuntimeError):
            migrate_v1_to_v2(db)
    finally:
        db.close()


def test_operator_migration_prints_one_valid_json_response_without_broker(tmp_path, capsys):
    import json

    from main import main

    db = legacy(tmp_path)
    db.close()
    env = tmp_path / ".env"
    env.write_text(f'PROJECT_ROOT="{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["migrate-db", "--env-file", str(env)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["migration"] == "1_to_2" and result["schema_version"] == 2
    assert result["state"] == "killed" and result["kill_switch"]
    assert (tmp_path / result["backup"]).is_file()
````

## File: `tests/test_mt5_client.py`

<!-- SHA256: e6319ce2a40f8d1a24a0f5c6d484e75ace03da26551bcf7e35b121733c1329d3 -->

````python
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


async def test_post_check_hook_can_veto_a_previously_valid_permit_before_native_send():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)

    class LateVetoAuthority(FixtureAuthority):
        def before_send(self, command, snapshot, grant):
            assert snapshot.data_source == SourceKind.TEST_SDK
            assert "order_check" in [name for name, _ in sdk.calls]
            raise TradingDisabled("TEST-only owner paused after order_check")

    authority = LateVetoAuthority(cfg, clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(TradingDisabled, match="after order_check"):
            await client.open_market_buy(order())
        assert not sdk.requests and authority.latest == ResultStatus.REJECTED


@pytest.mark.parametrize(
    "state,expected", [(2, True), (4, True), (5, True), (6, True), (0, False), (1, False), (3, False)]
)
async def test_native_order_finality_requires_actual_final_history_not_pending_absence(state, expected):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    sdk.history_orders_get = lambda *, ticket: (
        NS(ticket=ticket, magic=cfg.mt5_magic_number, state=state, volume_initial=0.02),
    )
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        assert await client.get_settled_orders((777,)) == (frozenset({777}) if expected else frozenset())
````

## File: `tests/test_order_calculator.py`

<!-- SHA256: 97fb66a709ea96daaad3e3e42e5e9f2d9ac897c0f281235ed215e3a48215ca0b -->

````python
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
````

## File: `tests/test_risk_concurrency.py`

<!-- SHA256: 4e642213b46cda18f8fb3e894f37aa5559ec4dba337e4ccb5b1682be4dd26c01 -->

````python
"""Cross-PROCESS reservation test: grants only; NO SDK/order submission."""

import asyncio
import multiprocessing
from dataclasses import replace

from sqlalchemy import select

from core.database import Database
from core.models import OrderIntent, RiskState
from tests.risk_helpers import MOMENT, OWNER, config, make_engine, safe_context
from trading.execution_authority import DurableWriteAuthority
from trading.risk_types import RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.snapshots import broker_snapshot
from trading.types import BrokerCommand, ManualClock, Operation, Side, SourceKind, TradingDisabled


def competing_reservation(root, session_id, command, snapshot, queue):
    # Spawned child knows only explicitly constructed TEST synthetic settings.
    settings = config(root)
    database = Database(settings)
    control = RuntimeControl(database, settings, ManualClock(MOMENT))
    control.session_id = session_id
    authority = DurableWriteAuthority(
        database, settings, control.clock, control, RuntimeProfile.current(settings, SourceKind.SYNTHETIC)
    )
    try:
        authority.authorize(command, snapshot)
        queue.put("granted")
    except TradingDisabled:
        queue.put("vetoed")
    except BaseException as exc:
        queue.put("unexpected_" + type(exc).__name__)
    finally:
        database.close()


async def test_cross_process_locked_reservations_cannot_both_use_same_free_equity(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        plan = await engine.calculator.plan_market_order(
            "EURUSD", Side.BUY, __import__("decimal").Decimal("1.09780"), strategy="TEST"
        )
        commands = [
            BrokerCommand(
                Operation.OPEN,
                key * 64,
                engine.clock.now(),
                order=replace(plan.order, idempotency_key=key * 64),
            )
            for key in ("1", "2")
        ]
        account = await engine.broker.get_account_info()
        snapshot = await broker_snapshot(
            engine.broker.market,
            engine.settings,
            account,
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            plan.worst_loss_account,
            plan.margin_account,
            plan.expected_net_profit_usd,
            durable_simulation=True,
        )
        for command in commands:
            engine.authority.stage(
                command, account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
            )
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=competing_reservation,
                args=(tmp_path, engine.control.session_id, command, snapshot, queue),
            )
            for command in commands
        ]
        for process in processes:
            process.start()
        for process in processes:
            await asyncio.to_thread(process.join, 15)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
            assert process.exitcode == 0
        outcomes = sorted(queue.get(timeout=3) for _ in processes)
        assert outcomes == ["granted", "vetoed"]
        queue.close()
        queue.join_thread()
        with engine.database.session() as session:
            rows = session.scalars(select(OrderIntent)).all()
            assert sorted(row.state for row in rows) == ["rejected", "submitting"]
            risk = session.scalar(select(RiskState))
            assert risk.accepted_entries_today == 1 and risk.reserved_risk_usd == plan.worst_loss_account
    finally:
        await engine.shutdown()
        engine.database.close()
````

## File: `tests/test_risk_contracts.py`

<!-- SHA256: 049774bf32aaeb3a33ae43598fb808a46b34f09a8b9e3b232cf05f4d17ae077b -->

````python
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import OrderIntent, Signal
from tests.risk_helpers import OWNER, D, make_engine, safe_context
from trading.authorization import BrokerSnapshot, PositionRisk
from trading.risk_engine import RiskEngine
from trading.risk_types import DecisionContext, NewsWindow, PositionReview, RuntimeProfile, source_code_hash
from trading.types import BrokerCommand, BrokerError, Operation, Side, SourceKind


@pytest.mark.parametrize(
    "change",
    [
        {"signal_score": float("nan")},
        {"signal_score": True},
        {"ai_confidence": 101},
        {"ai_confidence": "90"},
        {"risk_percent": D("0")},
        {"risk_percent": 0.1},
        {"risk_percent": D("NaN")},
        {"source": "mt5"},
    ],
)
def test_decision_dto_rejects_ambiguous_numeric_and_provenance_fields(tmp_path, change):
    from tests.risk_helpers import MOMENT
    from trading.types import ManualClock

    with pytest.raises(BrokerError):
        safe_context(ManualClock(MOMENT), **change)


@pytest.mark.parametrize("confidence", [True, "90", float("nan"), -1, 101])
def test_review_dto_is_not_a_boolean_or_nan_approval(confidence):
    from tests.risk_helpers import MOMENT

    with pytest.raises(BrokerError):
        PositionReview(MOMENT, SourceKind.SYNTHETIC, confidence)


@pytest.mark.parametrize("field", ["known", "safe"])
def test_news_flags_are_actual_booleans(field):
    with pytest.raises(BrokerError):
        NewsWindow(**{field: 1})


def test_code_hash_changes_for_runnable_code_and_pinned_dependencies_not_notes(tmp_path):
    (tmp_path / "main.py").write_text("print('TEST ONLY')")
    (tmp_path / "config.py").write_text("# TEST")
    (tmp_path / "requirements.txt").write_text("pytest==9.1.1")
    first = source_code_hash(tmp_path)
    (tmp_path / "notes.md").write_text("text")
    assert source_code_hash(tmp_path) == first
    (tmp_path / "requirements.txt").write_text("pytest==9.0.0")
    assert source_code_hash(tmp_path) != first


async def test_features_are_copied_and_durable_context_does_not_mutate_with_caller(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        original = {"nested": {"value": 1}}
        context = safe_context(engine.clock, features=original)
        original["nested"]["value"] = 2
        assert context.features["nested"]["value"] == 1
        roundtrip = DecisionContext.from_dict(context.to_dict())
        assert roundtrip.digest == context.digest
        plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        command = BrokerCommand(
            Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order
        )
        engine.authority.stage(
            command,
            await engine.broker.get_account_info(),
            context=context,
            target_usd=plan.target_profit_usd,
        )
        context.features["nested"]["value"] = 3
        with engine.database.session() as session:
            assert session.scalar(select(OrderIntent)).request["context"]["features"]["nested"]["value"] == 1
    finally:
        await engine.shutdown()
        engine.database.close()


@pytest.mark.parametrize("fault", ["strategy", "code", "model", "digest", "source", "news", "bar", "score"])
async def test_native_signal_binding_rejects_changed_persisted_inputs(tmp_path, fault):
    # Evaluation-only handcrafted native tags; no native client/execution is used.
    engine = await make_engine(tmp_path)
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
        risk = RiskEngine(engine.database, engine.settings, engine.clock, profile)
        plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        command = BrokerCommand(
            Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order
        )
        with engine.database.session() as session:
            signal = Signal(
                time=engine.clock.now(),
                bar_time=engine.clock.now() - timedelta(minutes=6),
                mode="paper",
                symbol="EURUSD",
                timeframe="M5",
                strategy="TEST",
                direction="buy",
                score=90,
                ai_score=90,
                final_decision="approved",
                config_hash=engine.settings.safety_fingerprint(),
                features_json={},
            )
            session.add(signal)
            session.flush()
            sid = signal.id
            context = safe_context(engine.clock, source=SourceKind.MT5, signal_id=sid)
            features = {
                "source": "mt5",
                "bar_closed_at": context.bar_closed_at.isoformat(),
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "news_hash": context.news.evidence_hash,
                "decision_digest": context.digest,
            }
            if fault in {"code", "model", "digest", "source", "news"}:
                key = {
                    "code": "code_hash",
                    "model": "model_sha256",
                    "digest": "decision_digest",
                    "source": "source",
                    "news": "news_hash",
                }[fault]
                features[key] = "wrong"
            if fault == "strategy":
                signal.strategy = "OTHER"
            if fault == "bar":
                signal.bar_time = engine.clock.now()
            if fault == "score":
                signal.score = 89
            signal.features_json = features
        snapshot = BrokerSnapshot(
            await engine.broker.get_account_info(),
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            plan.worst_loss_account,
            plan.margin_account,
            D("5.36"),
            engine.clock.now(),
            (),
            D("1"),
            D("1"),
            SourceKind.MT5,
            True,
        )
        from core.models import RiskState

        with engine.database.session() as session:
            row = session.scalar(select(RiskState))
            result = risk.evaluate(session, command, snapshot, context, row)
            assert "unbound_persisted_signal" in result.reasons
    finally:
        await engine.shutdown()
        engine.database.close()


def test_position_risk_is_finite_and_positive_identity():
    with pytest.raises(BrokerError):
        PositionRisk(1, D("NaN"))
    with pytest.raises(BrokerError):
        PositionRisk(1, 1.0)
    with pytest.raises(BrokerError):
        PositionRisk(0, D("1"))


async def test_snapshot_duplicate_risk_ids_are_not_silently_dropped(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        snapshot = BrokerSnapshot(
            await engine.broker.get_account_info(),
            await engine.broker.get_symbol_info("EURUSD"),
            await engine.broker.get_tick("EURUSD"),
            (),
            D("1"),
            D("1"),
            D("2"),
            engine.clock.now(),
        )
        with pytest.raises(BrokerError):
            replace(snapshot, position_risks=(PositionRisk(1, D("1")), PositionRisk(1, D("2"))))
        with pytest.raises(BrokerError):
            replace(snapshot, durable_simulation=1)
    finally:
        await engine.shutdown()
        engine.database.close()
````

## File: `tests/test_risk_diagnostics.py`

<!-- SHA256: de9a0006fc7685665ca232f4599a843b87e943062ca71bf76de1d29d2f0c42ce -->

````python
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_risk import run

ROOT = Path(__file__).resolve().parents[1]


async def test_risk_smoke_is_isolated_synthetic_and_not_promotion(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("MT5_BACKEND", "real")
    monkeypatch.setenv("MT5_PASSWORD", "NEVER_READ_THIS")
    report = await run()
    assert report["source"] == "synthetic" and not report["eligible_stage_evidence"]
    assert report["real_orders_sent"] == 0 and not report["native_sdk_imported"]
    assert report["entries_today"] == 1 and report["reserved_risk_usd"] == "0E-8"
    assert report["startup_pause_veto_verified"] and report["restart_started_paused"]
    assert report["verified_historical_lock_level"] == 30 and report["positions"] == 0


def test_smoke_cli_ignores_env_and_leaves_no_local_files(tmp_path):
    before = set(tmp_path.iterdir())
    process = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_risk"],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT), LIVE_TRADING="true", MT5_BACKEND="real"),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    report = json.loads(process.stdout)
    assert report["simulated_only"] and report["real_orders_sent"] == 0
    assert report["duplicate_entry_sent_once"] and set(tmp_path.iterdir()) == before
````

## File: `tests/test_risk_engine.py`

<!-- SHA256: e778f53db3f07159f3ce29716abe2e2b3d58509df2ad3c54802958a81f5532e9 -->

````python
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import BotState, BrokerDeal, RiskState
from tests.risk_helpers import MOMENT, D, config, safe_context
from trading.authorization import BrokerSnapshot
from trading.mock_mt5 import MockMT5Client
from trading.risk_engine import RiskEngine
from trading.risk_types import RuntimeProfile
from trading.types import (
    BrokerCommand,
    ManualClock,
    Operation,
    Side,
    SourceKind,
    TradingDisabled,
)


@pytest.fixture
async def sample(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    broker = MockMT5Client(cfg, clock=clock)
    await broker.initialize()
    account = await broker.get_account_info()
    meta, tick = await broker.get_symbol_info("EURUSD"), await broker.get_tick("EURUSD")
    plan = (
        await broker._calculator().plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
        if hasattr(broker, "_calculator")
        else None
    )
    from trading.order_calculator import OrderCalculator

    if plan is None:
        plan = await OrderCalculator(broker, cfg).plan_market_order(
            "EURUSD", Side.BUY, D("1.09780"), strategy="TEST"
        )
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, clock.now(), order=plan.order)
    snapshot = BrokerSnapshot(
        account,
        meta,
        tick,
        (),
        D("4.86"),
        D("22"),
        D("5.36"),
        clock.now(),
        (),
        D("1"),
        D("1"),
        SourceKind.SYNTHETIC,
        True,
    )
    risk = RiskEngine(db, cfg, clock, RuntimeProfile.current(cfg, SourceKind.SYNTHETIC))
    with db.locked_session() as session:
        session.get(BotState, 1).desired_state = "running"  # TEST ONLY, never an execution permit.
        risk.observe(session, account, (), intraday_history_complete=True)
    yield cfg, db, clock, risk, snapshot, command
    await broker.shutdown()
    db.close()


def evaluate(sample, snapshot=None, context=None):
    cfg, db, clock, risk, original, command = sample
    with db.locked_session() as session:
        state = session.scalar(select(RiskState))
        return risk.evaluate(session, command, snapshot or original, context or safe_context(clock), state)


def test_quality_not_minimum_trade_quota(sample):
    assert evaluate(sample).approved
    denied = evaluate(sample, context=safe_context(sample[2], signal_score=0))
    assert not denied.approved and "low_signal_score" in denied.reasons


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"signal_score": 69}, "low_signal_score"),
        ({"ai_confidence": 69}, "low_ai_confidence"),
        ({"source": SourceKind.MT5}, "data_provenance"),
        ({"risk_percent": D("0.6")}, "risk_escalation"),
        ({"risk_percent": D("0.1")}, "entry_risk_cap"),
    ],
)
def test_context_vetoes(sample, change, reason):
    decision = evaluate(sample, context=safe_context(sample[2], **change))
    assert not decision.approved and reason in decision.reasons


@pytest.mark.parametrize(
    "field,delta,reason",
    [
        ("observed_at", -31, "stale_signal"),
        ("observed_at", 3, "stale_signal"),
        ("bar_closed_at", 1, "unfinished_or_stale_candle"),
        ("bar_closed_at", -421, "unfinished_or_stale_candle"),
    ],
)
def test_signal_chronology(sample, field, delta, reason):
    context = safe_context(sample[2], **{field: MOMENT + timedelta(seconds=delta)})
    assert reason in evaluate(sample, context=context).reasons


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("worst_loss_account", D("0"), "entry_risk_cap"),
        ("worst_loss_account", D("5.01"), "entry_risk_cap"),
        ("required_margin_account", D("301"), "margin_cap"),
        ("required_margin_account", D("-1"), "margin_cap"),
        ("expected_reward_account", D("4"), "net_reward_risk"),
        ("data_source", SourceKind.TEST_SDK, "data_provenance"),
    ],
)
def test_fresh_financial_vetoes(sample, field, value, reason):
    assert reason in evaluate(sample, snapshot=replace(sample[4], **{field: value})).reasons


@pytest.mark.parametrize("age", [-3, 11])
def test_snapshot_freshness(sample, age):
    snapshot = replace(sample[4], observed_at=MOMENT - timedelta(seconds=age))
    assert "stale_broker_snapshot" in evaluate(sample, snapshot=snapshot).reasons


def test_paused_and_killed_are_independent(sample):
    with sample[1].session() as session:
        state = session.get(BotState, 1)
        state.desired_state = "paused"
        state.kill_switch_active = True
    result = evaluate(sample)
    assert {"paused", "kill_switch"}.issubset(result.reasons)


def test_existing_unsettled_reservation_is_not_free_capital(sample):
    with sample[1].session() as session:
        session.scalar(select(RiskState)).reserved_risk_usd = D("11")
    decision = evaluate(sample)
    assert {"aggregate_risk_cap", "unsettled_risk_reservation"}.issubset(decision.reasons)


def test_last_permitted_count_before_send_excludes_only_own_reservation(sample):
    cfg, db, clock, risk, snapshot, command = sample
    with db.locked_session() as session:
        state = session.scalar(select(RiskState))
        state.accepted_entries_today = cfg.max_daily_trades
        state.reserved_risk_usd = D("4.86")
        denied = risk.evaluate(session, command, snapshot, safe_context(clock), state)
        allowed = risk.evaluate(
            session,
            command,
            snapshot,
            safe_context(clock),
            state,
            self_reservation=D("4.86"),
            self_counted=True,
        )
        assert "daily_entry_count" in denied.reasons and allowed.approved


def test_new_day_uses_pre_gap_equity_and_preserves_drawdown(sample):
    cfg, db, clock, risk, snapshot, _ = sample
    clock.advance(timedelta(days=1))
    account = replace(snapshot.account, equity=D("850"), margin_free=D("850"))
    with db.locked_session() as session:
        row = risk.observe(session, account, ())
        assert row.day_start_equity == D("1000")
        assert row.equity_high_water == D("1000")
        assert row.daily_loss_latched and row.drawdown_latched
    reopened = Database(cfg)
    try:
        reopened.verify_schema()
        with reopened.session() as session:
            assert session.scalar(select(RiskState)).drawdown_latched
    finally:
        reopened.close()


def test_daily_resets_do_not_reset_lifetime_high_water(sample):
    _, db, clock, risk, snapshot, _ = sample
    with db.locked_session() as session:
        risk.observe(session, replace(snapshot.account, equity=D("1200")), ())
    clock.advance(timedelta(days=1))
    with db.locked_session() as session:
        row = risk.observe(session, replace(snapshot.account, equity=D("1120")), ())
        assert row.day_start_equity == D("1200") and row.equity_high_water == D("1200")
        assert row.daily_loss_latched and not row.drawdown_latched


@pytest.mark.parametrize("cash", [D("500"), D("-200")])
def test_signed_external_balance_cash_adjusts_baselines_not_profit(sample, cash):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=99,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=cash,
            )
        )
        session.flush()
        account = replace(snapshot.account, balance=D("1000") + cash, equity=D("1000") + cash)
        row = risk.observe(session, account, ())
        assert row.day_start_equity == D("1000") + cash
        assert row.equity_high_water == D("1000") + cash
        assert row.net_realized_today == 0
        assert not row.daily_loss_latched and not row.drawdown_latched


def test_credit_is_excluded_from_risk_and_drawdown(sample):
    _, db, _, risk, snapshot, _ = sample
    funded = replace(snapshot.account, equity=D("1500"), credit=D("500"))
    with db.locked_session() as session:
        row = risk.observe(session, funded, ())
        assert row.equity_high_water == 1000 and not row.drawdown_latched
    assert funded.risk_capital == 1000
    assert (
        "entry_risk_cap"
        in evaluate(sample, snapshot=replace(snapshot, account=funded, worst_loss_account=D("5.01"))).reasons
    )


@pytest.mark.parametrize("kind", ["correction", "bonus", "unknown_42"])
def test_unclassified_cash_is_a_persistent_veto(sample, kind):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=98,
                time=MOMENT,
                type=kind,
                entry="cash",
                currency="USD",
                profit=D("0"),
            )
        )
        session.flush()
        risk.observe(session, snapshot.account, ())
    assert "unreviewed_observation_or_cash" in evaluate(sample).reasons


def test_native_observation_gap_requires_review(sample):
    cfg, db, clock, _, snapshot, _ = sample
    risk = RiskEngine(db, cfg, clock, RuntimeProfile.current(cfg, SourceKind.MT5))
    clock.advance(timedelta(seconds=121))
    with db.locked_session() as session:
        row = risk.observe(session, snapshot.account, ())
        assert row.metadata_json["observation_gap"] is True


def test_dollars_use_signed_verified_fx(sample):
    snapshot = replace(
        sample[4],
        account=replace(sample[4].account, currency="EUR"),
        usd_asset_rate=D("1.10"),
        usd_liability_rate=D("1.11"),
    )
    assert snapshot.dollars(D("5")) == D("5.50")
    assert snapshot.dollars(D("-5")) == D("-5.55")
    with pytest.raises(TradingDisabled):
        replace(snapshot, usd_asset_rate=None).dollars(D("5"))


def test_daily_remaining_headroom_counts_all_risk(sample):
    with sample[1].session() as session:
        row = session.scalar(select(RiskState))
        row.day_start_equity = D("970") / D("0.974")  # Remaining loss headroom < new trade risk.
    assert (
        "daily_loss_headroom"
        in evaluate(
            sample, snapshot=replace(sample[4], account=replace(sample[4].account, equity=D("970")))
        ).reasons
    )


@pytest.mark.parametrize("cash", [D("500"), D("-200")])
def test_unreported_balance_change_is_not_profit_or_a_fake_high_water(sample, cash):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        changed = replace(snapshot.account, balance=D("1000") + cash, equity=D("1000") + cash)
        row = risk.observe(session, changed, ())
        assert row.metadata_json["unexplained_balance_change"]
        assert not row.metadata_json["balance_continuity_verified"]
        assert not row.metadata_json["baseline_verified"]
        assert row.equity_high_water == 1000
        assert (
            not row.daily_loss_latched and not row.drawdown_latched
        )  # Unknown cash is NOT classified as trading P&L.


def test_delayed_deposit_ledger_does_not_double_adjust_peak_and_requires_review(sample):
    cfg, db, clock, risk, snapshot, _ = sample
    changed = replace(snapshot.account, balance=D("1500"), equity=D("1500"))
    with db.locked_session() as session:
        risk.observe(session, changed, ())
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=999,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=D("500"),
            )
        )
        session.flush()
        row = risk.observe(session, changed, (), intraday_history_complete=True)
        assert row.metadata_json["balance_continuity_verified"]
        assert row.equity_high_water == 1500 and row.day_start_equity == 1500
        assert not row.metadata_json["baseline_verified"]
    from trading.runtime_state import RuntimeControl

    control = RuntimeControl(db, cfg, clock)
    control.claim()
    control.review_flat_baseline(42, account_key=snapshot.account.key, confirm="REVIEW_SAMPLED_BASELINE")
    control.acknowledge_recovery(42, account_key=snapshot.account.key, broker_writes_quarantined=False)
    control.resume(42, account_key=snapshot.account.key)
    control.release()


def test_deposit_fee_is_account_cost_not_external_capital(sample):
    _, db, _, risk, snapshot, _ = sample
    with db.locked_session() as session:
        session.add(
            BrokerDeal(
                account_key=snapshot.account.key,
                mode="paper",
                ticket=997,
                time=MOMENT,
                type="balance",
                entry="cash",
                currency="USD",
                profit=D("500"),
                fee=D("-5"),
            )
        )
        session.flush()
        changed = replace(snapshot.account, balance=D("1495"), equity=D("1495"))
        row = risk.observe(session, changed, ())
        assert row.cash_flow_total == 500 and row.net_realized_today == -5
        assert row.day_start_equity == 1500 and row.metadata_json["balance_continuity_verified"]


def test_same_day_and_new_day_reservations_do_not_vanish_with_the_counter_reset(sample):
    _, db, clock, risk, snapshot, command = sample
    from core.models import OrderIntent

    with db.locked_session() as session:
        session.add(
            OrderIntent(
                idempotency_key=command.idempotency_key,
                time=MOMENT,
                expires_at=MOMENT + timedelta(seconds=30),
                mode="paper",
                account_key=snapshot.account.key,
                symbol="EURUSD",
                direction="buy",
                state="unknown",
                config_hash="a" * 64,
                request={
                    "reserved": True,
                    "counted": True,
                    "count_day": MOMENT.date().isoformat(),
                    "reserved_risk_usd": "4.86",
                },
            )
        )
        session.flush()
        row = risk.observe(session, snapshot.account, ())
        assert row.accepted_entries_today == 1 and row.reserved_risk_usd == D("4.86")
    clock.advance(timedelta(days=1))
    with db.locked_session() as session:
        row = risk.observe(session, snapshot.account, ())
        assert row.accepted_entries_today == 0 and row.reserved_risk_usd == D("4.86")
````

## File: `tests/test_runtime_state.py`

<!-- SHA256: 5ad3293d2ca459e921453885c685568ae11192eff244cfe0e218fb2c1561a62b -->

````python
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import BotState, RiskState
from tests.risk_helpers import MOMENT, OWNER, config, make_engine, open_one
from trading.runtime_state import RuntimeControl
from trading.types import ManualClock, TradingDisabled


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    yield result
    await result.shutdown()
    result.database.close()


def test_new_runtime_is_paused_not_an_owner_approval(engine):
    assert engine.database.status()["state"] == "paused"
    assert engine.control.session_id is not None


@pytest.mark.parametrize("owner", [None, 0, 43, True, 42.0, "42"])
def test_owner_id_requires_authenticated_integer(engine, owner):
    with pytest.raises(TradingDisabled):
        engine.control.resume(owner, account_key=engine.account_key)
    assert engine.database.status()["state"] == "paused"


def test_missing_owner_is_deny_all(tmp_path):
    cfg = config(tmp_path, telegram_owner_id=None, telegram_bot_token="")
    db = Database(cfg)
    db.initialize()
    control = RuntimeControl(db, cfg, ManualClock(MOMENT))
    try:
        control.claim()
        with pytest.raises(TradingDisabled):
            control.pause(OWNER)
    finally:
        control.release()
        db.close()


async def test_unexpired_cross_process_lease_cannot_be_taken(engine):
    other = Database(engine.settings)
    try:
        contender = RuntimeControl(other, engine.settings, engine.clock)
        with pytest.raises(TradingDisabled, match="another runtime"):
            await asyncio.to_thread(contender.claim)
    finally:
        other.close()


async def test_expired_lease_creates_new_paused_session_and_old_cannot_renew(engine):
    first = engine.control.session_id
    engine.clock.advance(timedelta(seconds=engine.settings.runtime_lease_seconds))
    other = RuntimeControl(engine.database, engine.settings, engine.clock)
    second = other.claim()
    assert first != second and engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.heartbeat()
    other.release()


async def test_shutdown_restart_is_paused_even_if_previously_running(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    before = engine.control.session_id
    engine.control.release()
    engine.control.claim()
    assert engine.control.session_id != before
    assert engine.database.status()["state"] == "paused"


async def test_kill_persists_and_requires_separate_explicit_reset(engine):
    engine.control.resume(OWNER, account_key=engine.account_key)
    engine.control.kill(OWNER)
    engine.control.release()
    engine.control.claim()
    assert engine.database.status()["kill_switch"]
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.reset_kill(
            OWNER, account_key=engine.account_key, confirm="yes", broker_writes_quarantined=False
        )
    engine.control.reset_kill(
        OWNER,
        account_key=engine.account_key,
        confirm="RESET_KILL_AND_KEEP_PAUSED",
        broker_writes_quarantined=False,
    )
    assert not engine.database.status()["kill_switch"] and engine.database.status()["state"] == "paused"


@pytest.mark.parametrize("field", ["daily_loss_latched", "drawdown_latched"])
def test_owner_resume_never_erases_loss_latches(engine, field):
    with engine.database.session() as session:
        setattr(session.scalar(select(RiskState)), field, True)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with engine.database.session() as session:
        assert getattr(session.scalar(select(RiskState)), field)


def test_unstable_broker_halt_is_durable(engine):
    engine.control.halt("broker_unstable", account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.acknowledge_recovery(
            OWNER, account_key=engine.account_key, broker_writes_quarantined=True
        )
    engine.control.acknowledge_recovery(
        OWNER, account_key=engine.account_key, broker_writes_quarantined=False
    )
    assert engine.database.status()["state"] == "paused"
    engine.control.resume(OWNER, account_key=engine.account_key)


def test_flat_baseline_review_cannot_reset_loss_flags(engine):
    with engine.database.session() as session:
        row = session.scalar(select(RiskState))
        row.metadata_json = {
            **row.metadata_json,
            "baseline_verified": False,
            "migration_review_required": True,
        }
    engine.control.review_flat_baseline(
        OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
    )
    with engine.database.session() as session:
        row = session.scalar(select(RiskState))
        assert row.metadata_json["baseline_verified"] and row.metadata_json["sampled_peak_only"]
        row.drawdown_latched = True
    with pytest.raises(TradingDisabled):
        engine.control.review_flat_baseline(
            OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
        )


async def test_baseline_review_is_denied_with_open_exposure(engine):
    await open_one(engine)
    with pytest.raises(TradingDisabled):
        engine.control.review_flat_baseline(
            OWNER, account_key=engine.account_key, confirm="REVIEW_SAMPLED_BASELINE"
        )


async def test_stale_risk_observation_prevents_resume(engine):
    engine.clock.advance(timedelta(seconds=engine.settings.risk_observation_max_age_seconds + 1))
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)


def test_halt_accepts_identifiers_not_untrusted_sdk_error_text(engine):
    with pytest.raises(ValueError):
        engine.control.halt("password=secret")


async def test_pause_does_not_lower_kill(engine):
    engine.control.kill(OWNER)
    engine.control.pause(OWNER)
    with engine.database.session() as session:
        state = session.get(BotState, 1)
        assert state.kill_switch_active and state.desired_state == "killed"
````

## File: `tests/test_security_logging.py`

<!-- SHA256: c5f20b96f04b4be2d0aaeab0f97002e6397bd87102576eb98f89980d5f76a479 -->

````python
import io
import logging
import math
from decimal import Decimal

import pytest

from core.logging_setup import RedactingFormatter
from core.security import canonical_json, sanitize_data, sanitize_text, sha256_json


def test_nested_audit_secret_redaction():
    result = sanitize_data(
        {"password": "dont-store", "nested": {"initData": "raw-init-data"}, "reason": "secret-from-env"},
        ("secret-from-env",),
    )
    assert result == {"password": "[REDACTED]", "nested": {"initData": "[REDACTED]"}, "reason": "[REDACTED]"}


def test_traceback_and_message_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(("env-secret",)))
    logger = logging.getLogger("reflexbot.test.redaction")
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    try:
        raise RuntimeError("password=env-secret https://api.telegram.org/bot123:LEAK/sendMessage")
    except RuntimeError:
        logger.exception("Authorization: Bearer abc123")
    text = stream.getvalue()
    assert "env-secret" not in text
    assert "123:LEAK" not in text
    assert "abc123" not in text
    assert "Traceback" in text
    logger.handlers.clear()
    handler.close()


def test_url_and_unknown_assignment_redacted():
    value = sanitize_text("postgresql+psycopg://user:pass@localhost/db api_key=abcd")
    assert "user:pass" not in value and "abcd" not in value


def test_canonical_hash_stable():
    assert sha256_json({"b": 2, "a": 1}) == sha256_json({"a": 1, "b": 2})
    assert canonical_json({"money": Decimal("0.10")}) == '{"money":"0.10"}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, Decimal("NaN")])
def test_nonfinite_json_rejected(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


def test_deep_audit_payload_rejected():
    value = {}
    for _ in range(22):
        value = {"nested": value}
    with pytest.raises(ValueError):
        sanitize_data(value)


def test_log_message_newline_does_not_forge_another_entry():
    import json

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "line one\nFAKE ENTRY", (), None)
    output = RedactingFormatter().format(record)
    assert len(output.splitlines()) == 1
    assert json.loads(output)["message"] == "line one\nFAKE ENTRY"


def test_malformed_logging_does_not_dump_secret_arguments():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "%d", ("raw-secret",), None)
    output = RedactingFormatter().format(record)
    assert "raw-secret" not in output
    assert "suppressed" in output
````

## File: `tests/test_settings.py`

<!-- SHA256: 6f0e285da76303e3a0bc0e4eee90f2ddf9b3f176bbfc03a9911326a16a6749dc -->

````python
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.settings import OperatingMode, Settings


def config(**values):
    return Settings(_env_file=None, **values)


def test_safe_defaults():
    settings = config()
    assert settings.mode == OperatingMode.PAPER
    assert settings.mt5_backend == "mock"
    assert settings.demo_mode and settings.start_paused and settings.require_stage_gates
    assert not settings.live_trading
    assert settings.live_max_risk_percent_per_trade == Decimal("0.1")


def test_complete_env_example_is_valid():
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env.example")
    assert settings.mode == OperatingMode.PAPER
    assert settings.symbols == ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    assert settings.trailing_levels == ((30, 30), (60, 60), (90, 90))


@pytest.mark.parametrize(
    "flags",
    [
        {"live_trading": True},
        {"demo_mode": False},
        {"backtest_mode": True},
        {"paper_trading": False},
        {"start_paused": False},
        {"require_stage_gates": False},
        {"require_stop_loss": False},
    ],
)
def test_ambiguous_or_unsafe_flags_are_rejected(flags):
    with pytest.raises(ValidationError):
        config(**flags)


def test_explicit_modes_do_not_imply_authorization():
    assert config(backtest_mode=True, paper_trading=False).mode == OperatingMode.BACKTEST
    assert config(paper_trading=False, mt5_backend="real").mode == OperatingMode.DEMO
    live = config(
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
    )
    assert live.mode == OperatingMode.LIVE
    assert live.public_config()["startup"] == "paused"


@pytest.mark.parametrize(
    "values",
    [
        {"max_risk_percent_per_trade": "1.01"},
        {"live_max_risk_percent_per_trade": "0.6"},
        {"min_daily_trades_target": 13},
        {"max_same_symbol_positions": 2},
        {"model_embargo_bars": 11},
        {"max_daily_loss_percent": "11"},
        {"target_r_multiple": "1"},
        {"trailing_levels": "60:60,30:30"},
        {"trailing_levels": "30:40"},
        {"symbols": "XAUUSD,XAUUSD"},
        {"symbols": "../secret"},
        {"telegram_bot_token": "token-without-owner"},
        {"log_file": "../outside.log"},
        {"ollama_base_url": "http://remote.example"},
        {"openai_base_url": "http://remote.example/v1"},
        {"API_TRUSTED_HOSTS_JSON": ["*"]},
    ],
)
def test_risk_and_security_configuration_rejected(values):
    with pytest.raises(ValidationError):
        config(**values)


def test_config_is_frozen():
    settings = config()
    with pytest.raises(ValidationError):
        settings.live_trading = True


def test_secrets_excluded_from_public_config_and_hash():
    first = config(telegram_bot_token="123:TOP-SECRET-A", telegram_owner_id=123)
    second = config(telegram_bot_token="123:TOP-SECRET-B", telegram_owner_id=123)
    assert "TOP-SECRET" not in str(first.public_config())
    assert first.safety_fingerprint() == second.safety_fingerprint()
    assert first.safety_fingerprint() != config(max_daily_trades=11).safety_fingerprint()


def test_unknown_dotenv_key_is_rejected(tmp_path):
    path = tmp_path / ".env"
    path.write_text("LIVE_TRDAING=true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=path)


def test_nested_config_is_read_only():
    settings = config(symbol_aliases={"XAUUSD": "XAUUSDm"})
    with pytest.raises(TypeError):
        settings.symbol_aliases["XAUUSD"] = "other"
    with pytest.raises(TypeError):
        settings.symbol_news_currencies["XAUUSD"] = ("EUR",)
    assert isinstance(settings.symbol_news_currencies["XAUUSD"], tuple)
    assert isinstance(settings.rss_urls, tuple)


def test_stage_fingerprint_spans_modes_but_approval_fingerprint_does_not():
    paper = config(telegram_bot_token="123:test-token", telegram_owner_id=123)
    live = config(
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
    )
    assert paper.strategy_fingerprint() == live.strategy_fingerprint()
    assert paper.safety_fingerprint() != live.safety_fingerprint()
    assert live.effective_risk_percent == Decimal("0.1")


def test_lower_r_targets_require_explicit_lower_reward_risk_floor():
    with pytest.raises(ValidationError):
        config(target_r_multiple="0.35")
    lab = config(target_r_multiple="0.35", min_net_reward_risk="0.3")
    assert lab.target_r_multiple == Decimal("0.35")


def test_alias_collision_rejected():
    with pytest.raises(ValidationError):
        config(symbols="XAUUSD,GOLD", symbol_aliases={"GOLD": "XAUUSD"})


def test_literal_broker_alias_with_spaces_supported():
    settings = config(symbols="V75", symbol_aliases={"V75": "Volatility 75 Index"})
    assert settings.symbol_aliases["V75"] == "Volatility 75 Index"
    with pytest.raises(ValidationError):
        config(symbols="V75", symbol_aliases={"V75": "Volatility\n75"})
````

## File: `tests/test_simulated_broker.py`

<!-- SHA256: 057da7b9ba8599df0a384c836cfd4bc2d5027e18ed2f59d40d94e443cd939953 -->

````python
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
````

## File: `tests/test_stage_gate.py`

<!-- SHA256: 89a1e1108c3eeb0a640d55ca36e1f35336d78d870a661c395c71e5b2433272b9 -->

````python
"""TEST ONLY hand-authored reports/accounts. No broker approval or promotion run.

Nonce tests stub evidence selection to isolate confirmation binding; runtime
composition NEVER uses these stubs, and TEST_SDK remains promotion-ineligible.
"""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from core.database import Database
from core.models import BotState, DeploymentEvidence, OwnerApproval
from core.security import canonical_json
from tests.risk_helpers import MOMENT, OWNER, D, config
from trading.risk_types import RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.stage_gate import StageGate, read_report
from trading.types import AccountInfo, AccountKind, ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")  # Still paper; no native client is constructed.
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
    gate = StageGate(db, cfg, clock, profile)
    yield cfg, db, clock, profile, gate
    db.close()


def report(sample, **overrides):
    cfg, db, clock, profile, gate = sample
    started, finished = MOMENT - timedelta(days=45), MOMENT - timedelta(days=30)
    dataset = cfg.project_root / "reports/TEST_ONLY.csv"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("TEST ONLY fixture, NOT market data\n")
    metrics = {
        "closed_trades": 100,
        "profit_factor": "2",
        "max_drawdown_percent": "2",
        "unexplained_gaps": 0,
        "costs_included": True,
        "lookahead_free": True,
    }
    row = DeploymentEvidence(
        stage="backtest",
        created_at=MOMENT,
        started_at=started,
        finished_at=finished,
        strategy_config_hash=cfg.strategy_fingerprint(),
        code_hash=profile.code_hash,
        model_sha256=profile.model_sha256,
        account_key=None,
        metrics_json=metrics,
        artifact_path="reports/backtest.json",
        artifact_sha256="a" * 64,
        passed=True,
        owner_reviewed_by=OWNER,
        revoked=False,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    body = {
        "format": "reflex-stage-v1",
        "stage": row.stage,
        "source": "historical_real",
        "strategy_config_hash": row.strategy_config_hash,
        "code_hash": row.code_hash,
        "model_sha256": row.model_sha256,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat(),
        "account_key": row.account_key,
        "metrics": row.metrics_json,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "dataset_path": "reports/TEST_ONLY.csv",
    }
    path = cfg.resolve_path(row.artifact_path)
    path.write_text(canonical_json(body))
    row.artifact_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with db.session() as session:
        session.add(row)
    return row, path


def paper_account():
    return AccountInfo(
        1,
        "TEST-PAPER-ACCOUNT",
        "USD",
        AccountKind.SIMULATED,
        SourceKind.PAPER,
        D("1000"),
        D("1000"),
        D("0"),
        D("1000"),
    )


def test_structurally_valid_reviewed_backtest_is_artifact_and_dataset_bound(sample):
    row, path = report(sample)
    with sample[1].session() as session:
        assert sample[4].required_evidence(session, paper_account()) == (row.id,)
    # This verifies policy format only. The fixture is NOT real-market evidence.
    sample[0].resolve_path("reports/TEST_ONLY.csv").write_text("changed")
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"revoked": True},
        {"owner_reviewed_by": OWNER + 1},
        {"code_hash": "e" * 64},
        {"model_sha256": "e" * 64},
        {"strategy_config_hash": "e" * 64},
        {"finished_at": MOMENT + timedelta(seconds=1)},
    ],
)
def test_unreviewed_revoked_changed_or_future_evidence_is_denied(sample, change):
    report(sample, **change)
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize(
    "field,value",
    [
        ("closed_trades", 99),
        ("closed_trades", True),
        ("profit_factor", "1.0"),
        ("profit_factor", "NaN"),
        ("max_drawdown_percent", "5.01"),
        ("max_drawdown_percent", "-1"),
        ("unexplained_gaps", 1),
        ("costs_included", False),
        ("lookahead_free", False),
    ],
)
def test_stage_metrics_fail_closed(sample, field, value):
    metrics = {
        "closed_trades": 100,
        "profit_factor": "2",
        "max_drawdown_percent": "2",
        "unexplained_gaps": 0,
        "costs_included": True,
        "lookahead_free": True,
        field: value,
    }
    report(sample, metrics_json=metrics)
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize("kind", ["hash", "source", "schema", "dataset_digest", "artifact_deleted"])
def test_bad_artifact_is_never_an_approval(sample, kind):
    row, path = report(sample)
    if kind == "artifact_deleted":
        path.unlink()
    elif kind == "hash":
        path.write_text(path.read_text() + " ")
    else:
        body = json.loads(path.read_text())
        if kind == "source":
            body["source"] = "synthetic"
        elif kind == "schema":
            body["format"] = "wrong"
        else:
            body["dataset_sha256"] = "invalid"
        path.write_text(canonical_json(body))
        with sample[1].session() as session:
            session.get(DeploymentEvidence, row.id).artifact_sha256 = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', "[]", '"scalar"'])
def test_report_parser_rejects_duplicate_nonfinite_or_wrong_root(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(TradingDisabled):
        read_report(path)


def test_deep_and_oversized_reports_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"a":' * 18 + "1" + "}" * 18)
    with pytest.raises(TradingDisabled):
        read_report(path)
    path.write_bytes(b" " * (1048576 + 1))
    with pytest.raises(TradingDisabled):
        read_report(path)


@pytest.mark.parametrize("source", [SourceKind.SYNTHETIC, SourceKind.TEST_SDK, SourceKind.PAPER])
def test_test_synthetic_or_unproven_paper_source_never_promotes_to_broker(sample, source):
    cfg, db, clock, profile, _ = sample
    demo = config(cfg.project_root, mt5_backend="real", paper_trading=False)
    gate = StageGate(db, demo, clock, replace(profile, data_source=source))
    account = replace(paper_account(), source=source, kind=AccountKind.DEMO)
    with db.session() as session:
        with pytest.raises(TradingDisabled, match="provenance"):
            gate.required_evidence(session, account)


def test_paper_demo_claims_require_actual_trade_and_account_ledger(sample):
    cfg, db, _, _, gate = sample
    row, _ = report(sample, stage="paper", account_key="paper:TEST")
    with db.session() as session:
        with pytest.raises(TradingDisabled, match="trade records"):
            gate._verify_stage_ledger(session, row, "paper")


@pytest.fixture
def live(tmp_path, monkeypatch):
    cfg = config(tmp_path, mt5_backend="real", paper_trading=False, demo_mode=False, live_trading=True)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    control = RuntimeControl(db, cfg, clock)
    control.claim()
    gate = StageGate(db, cfg, clock, RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5))
    # TEST ONLY isolates nonce logic; actual required_evidence never has this bypass.
    monkeypatch.setattr(gate, "required_evidence", lambda session, account: (1, 2, 3))
    account = replace(paper_account(), source=SourceKind.MT5, kind=AccountKind.REAL)
    yield cfg, db, clock, control, gate, account
    control.release()
    db.close()


def test_live_nonce_is_hashed_single_use_session_account_code_bound(live):
    cfg, db, _, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    assert challenge.nonce not in repr(challenge)
    with db.session() as session:
        stored = session.get(OwnerApproval, challenge.approval_id)
        assert stored.nonce_hash != challenge.nonce and stored.status == "pending"
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, "wrong", OWNER)
    gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)
    with db.session() as session:
        assert gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
        assert not gate.live_confirmed(session, account, "other-session", (1, 2, 3))
        assert not gate.live_confirmed(
            session, replace(account, server="different"), control.session_id, (1, 2, 3)
        )
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 4))
        changed = StageGate(db, cfg, live[2], RuntimeProfile("e" * 64, "d" * 64, SourceKind.MT5))
        assert not changed.live_confirmed(session, account, control.session_id, (1, 2, 3))
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)


@pytest.mark.parametrize("case", ["owner", "expiry", "session", "configuration", "payload"])
def test_live_confirmation_cannot_override_binding(live, case):
    cfg, db, clock, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    owner = OWNER + 1 if case == "owner" else OWNER
    if case == "expiry":
        clock.advance(timedelta(seconds=cfg.live_approval_ttl_seconds + 1))
    with db.session() as session:
        row = session.get(OwnerApproval, challenge.approval_id)
        if case == "session":
            session.get(BotState, 1).session_id = "new-session"
        if case == "configuration":
            row.config_hash = "f" * 64
        if case == "payload":
            row.evidence_ids = [9]
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, challenge.nonce, owner)


def test_confirmed_live_expires_without_rearming(live):
    cfg, db, clock, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)
    clock.advance(timedelta(seconds=cfg.live_approval_ttl_seconds + 1))
    with db.session() as session:
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
````

## File: `tests/test_stage_ledger.py`

<!-- SHA256: 43b1fef9d46289d716a03651dd436cf895dbe04a732efad9baa5643ee2b94bd1 -->

````python
"""TEST ONLY hand-authored native-tagged DB proof rows; no trading/provenance claim."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import AccountSnapshot, BrokerDeal, DeploymentEvidence, OrderIntent, Trade
from tests.risk_helpers import MOMENT, OWNER, D, config
from trading.risk_types import RuntimeProfile
from trading.stage_gate import StageGate
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")
    db = Database(cfg)
    db.initialize()
    profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
    gate = StageGate(db, cfg, ManualClock(MOMENT), profile)
    scope = "paper:TEST_ONLY_NEVER_PROMOTION"
    start, finish = MOMENT - timedelta(seconds=120), MOMENT
    meta = {
        "version": 1,
        "data_source": "mt5",
        "source": "mt5",
        "code_hash": profile.code_hash,
        "model_sha256": profile.model_sha256,
        "strategy_config_hash": cfg.strategy_fingerprint(),
        "credit": "0",
        "original_volume": "0.02",
    }
    with db.session() as session:
        evidence = DeploymentEvidence(
            stage="paper",
            started_at=start,
            finished_at=finish,
            created_at=MOMENT,
            account_key=scope,
            strategy_config_hash=cfg.strategy_fingerprint(),
            code_hash=profile.code_hash,
            model_sha256=profile.model_sha256,
            artifact_path="TEST_ONLY",
            artifact_sha256="f" * 64,
            metrics_json={"closed_trades": 100, "profit_factor": "2", "max_drawdown_percent": "0"},
            passed=True,
            owner_reviewed_by=OWNER,
        )
        session.add(evidence)
        for index in range(100):
            net = D("2") if index < 50 else D("-1")
            ticket, pid, iid = 9000 + index * 2, 8000 + index, "TEST-" + str(index)
            request = {
                "authorized": True,
                "data_source": "mt5",
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "strategy_config_hash": cfg.strategy_fingerprint(),
            }
            intent = OrderIntent(
                id=iid,
                idempotency_key=f"{index:064x}",
                time=start,
                expires_at=finish,
                mode="paper",
                account_key=scope,
                symbol="EURUSD",
                direction="buy",
                state="reconciled",
                request=request,
                config_hash=cfg.safety_fingerprint(),
            )
            session.add(intent)
            session.flush()
            session.add(
                Trade(
                    order_intent_id=iid,
                    account_key=scope,
                    mode="paper",
                    currency="USD",
                    symbol="EURUSD",
                    direction="buy",
                    ticket=pid,
                    position_identifier=pid,
                    volume=D("0.02"),
                    entry_price=D("1.1"),
                    sl=D("1.09"),
                    tp=D("1.12"),
                    open_time=start + timedelta(seconds=1),
                    close_time=finish - timedelta(seconds=1),
                    profit=net,
                    profit_usd=net,
                    initial_risk_usd=D("1"),
                    target_profit_usd=D("2"),
                    strategy="TEST",
                    signal_score=90,
                    ai_score=90,
                    status="closed",
                    config_hash=cfg.safety_fingerprint(),
                    features_json={"execution": {**meta, "entry_deal_tickets": [ticket]}},
                )
            )
            session.add(
                BrokerDeal(
                    account_key=scope,
                    mode="paper",
                    ticket=ticket,
                    order_ticket=ticket,
                    position_identifier=pid,
                    time=start + timedelta(seconds=1),
                    type="buy",
                    entry="in",
                    symbol="EURUSD",
                    currency="USD",
                    magic=cfg.mt5_magic_number,
                    volume=D("0.02"),
                    price=D("1.1"),
                    commission=D("-0.07"),
                )
            )
            session.add(
                BrokerDeal(
                    account_key=scope,
                    mode="paper",
                    ticket=ticket + 1,
                    order_ticket=ticket + 1,
                    position_identifier=pid,
                    time=finish - timedelta(seconds=1),
                    type="sell",
                    entry="out",
                    symbol="EURUSD",
                    currency="USD",
                    magic=cfg.mt5_magic_number,
                    volume=D("0.02"),
                    price=D("1.11"),
                    profit=net + D("0.14"),
                    commission=D("-0.07"),
                )
            )
        for offset in (0, 60, 120):
            session.add(
                AccountSnapshot(
                    time=start + timedelta(seconds=offset),
                    account_key=scope,
                    mode="paper",
                    currency="USD",
                    balance=D("1000"),
                    equity=D("1000"),
                    margin=D("0"),
                    free_margin=D("1000"),
                    cash_flow_total=D("0"),
                    metadata_json=meta,
                )
            )
        session.flush()
        eid = evidence.id
    yield cfg, db, gate, eid, scope
    db.close()


def verify(sample):
    _, db, gate, eid, _ = sample
    with db.session() as session:
        gate._verify_stage_ledger(session, session.get(DeploymentEvidence, eid), "paper")


def test_ledger_proof_calculates_finite_cost_inclusive_pf_and_coverage(sample):
    verify(sample)  # Isolated ledger verifier only; 120 seconds cannot qualify a 14-day stage.


@pytest.mark.parametrize(
    "fault",
    ["source", "code", "volume", "intent", "fee", "snapshot_source", "coverage", "credit", "pf", "drawdown"],
)
def test_proof_metrics_and_source_fail_closed_when_changed(sample, fault):
    cfg, db, _, eid, _ = sample
    with db.session() as session:
        trade = session.scalars(select(Trade).order_by(Trade.id)).first()
        if fault in {"source", "code"}:
            key = "data_source" if fault == "source" else "code_hash"
            trade.features_json = {"execution": {**trade.features_json["execution"], key: "synthetic"}}
        if fault == "volume":
            session.scalar(select(BrokerDeal).where(BrokerDeal.entry == "out").limit(1)).volume = D("0.01")
        if fault == "intent":
            session.get(OrderIntent, trade.order_intent_id).request = {"authorized": False}
        if fault == "fee":
            session.scalar(select(BrokerDeal).where(BrokerDeal.entry == "out").limit(1)).commission = D("0")
        snapshot = session.scalars(select(AccountSnapshot).order_by(AccountSnapshot.id)).first()
        if fault == "snapshot_source":
            snapshot.metadata_json = {**snapshot.metadata_json, "source": "test_sdk"}
        if fault == "coverage":
            session.get(DeploymentEvidence, eid).started_at -= timedelta(seconds=600)
        if fault == "credit":
            snapshot.metadata_json = {**snapshot.metadata_json, "credit": "1000"}
        if fault == "pf":
            row = session.get(DeploymentEvidence, eid)
            row.metrics_json = {**row.metrics_json, "profit_factor": "100"}
        if fault == "drawdown":
            later = session.scalars(select(AccountSnapshot).order_by(AccountSnapshot.id.desc())).first()
            later.equity = D("990")
    with pytest.raises(TradingDisabled):
        verify(sample)
````

## File: `tests/test_state_store.py`

<!-- SHA256: 635ac6c9d727157fa33234826632db2823413b615972b40de3c52270559ce4f8 -->

````python
import json
import os

import pytest

from trading.state_store import AtomicSnapshotStore
from trading.types import RiskViolation


def test_missing_atomic_store_is_not_an_implicit_reset(tmp_path):
    assert AtomicSnapshotStore(tmp_path / "state.json").load() is None


def test_checksummed_roundtrip_and_atomic_replacement(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "nested/state.json")
    store.save({"balance": "1000", "positions": []})
    first = store.path.read_bytes()
    store.save({"balance": "999.93", "positions": [{"identifier": 1}]})
    assert store.path.read_bytes() != first
    assert store.load()["balance"] == "999.93"
    assert list(store.path.parent.glob(".paper-*.tmp")) == []


def test_modified_finances_do_not_pass_digest(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "state.json")
    store.save({"balance": "1000"})
    value = json.loads(store.path.read_text())
    value["state"]["balance"] = "1000000"
    store.path.write_text(json.dumps(value))
    with pytest.raises(RiskViolation, match="corrupt"):
        store.load()


@pytest.mark.parametrize(
    "contents", ["", "garbage", "{}", "[]", '{"version":1,"version":1,"state":{},"sha256":"wrong"}']
)
def test_malformed_and_duplicate_checkpoint_json_is_rejected(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents)
    with pytest.raises(RiskViolation):
        AtomicSnapshotStore(path).load()


def test_checkpoint_size_is_bounded_on_read_and_write(tmp_path):
    store = AtomicSnapshotStore(tmp_path / "state.json", max_bytes=150)
    with pytest.raises(RiskViolation):
        store.save({"oversize": "x" * 200})
    store.path.write_bytes(b"x" * 151)
    with pytest.raises(RiskViolation):
        store.load()


def test_failed_replace_preserves_last_valid_snapshot_and_removes_temp(tmp_path, monkeypatch):
    store = AtomicSnapshotStore(tmp_path / "state.json")
    store.save({"balance": "1000"})

    def failure(*args):
        raise OSError("TEST disk failure")

    monkeypatch.setattr(os, "replace", failure)
    with pytest.raises(OSError):
        store.save({"balance": "999"})
    assert store.load() == {"balance": "1000"}
    assert not list(tmp_path.glob(".paper-*.tmp"))


def test_symlink_checkpoints_are_not_followed(tmp_path):
    target = tmp_path / "other.json"
    target.write_text("{}")
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege not available on this host")
    store = AtomicSnapshotStore(link)
    with pytest.raises(RiskViolation):
        store.load()
    with pytest.raises(RiskViolation):
        store.save({})
    assert target.read_text() == "{}"
````

## File: `tests/test_trade_logger.py`

<!-- SHA256: 7760e99d92683b3b1da056e60780e03e88e2f1f1875f79499d43c43b49cf2ec1 -->

````python
"""TEST ONLY exact-ID reconciliation DTOs, no native SDK/broker calls."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from core.models import BotState, BrokerDeal, OrderIntent, RiskState, Trade
from tests.risk_helpers import OWNER, D, make_engine, safe_context
from trading.snapshots import broker_snapshot
from trading.types import (
    BrokerCommand,
    Deal,
    ExecutionResult,
    Operation,
    Position,
    ResultStatus,
    Side,
    TradingDisabled,
)


@pytest.fixture
async def sample(tmp_path):
    engine = await make_engine(tmp_path)
    engine.control.resume(OWNER, account_key=engine.account_key)
    plan = await engine.calculator.plan_market_order("EURUSD", Side.BUY, D("1.09780"), strategy="TEST")
    command = BrokerCommand(Operation.OPEN, plan.order.idempotency_key, engine.clock.now(), order=plan.order)
    account = await engine.broker.get_account_info()
    snapshot = await broker_snapshot(
        engine.broker.market,
        engine.settings,
        account,
        await engine.broker.get_symbol_info("EURUSD"),
        await engine.broker.get_tick("EURUSD"),
        (),
        plan.worst_loss_account,
        plan.margin_account,
        D("5.36"),
        durable_simulation=True,
    )
    engine.authority.stage(
        command, account, context=safe_context(engine.clock), target_usd=plan.target_profit_usd
    )
    engine.authority.authorize(command, snapshot)
    position = Position(
        8001,
        777777,
        "EURUSD",
        Side.BUY,
        plan.order.volume,
        D("1.10014"),
        plan.order.sl,
        plan.order.tp,
        engine.clock.now(),
        engine.settings.mt5_magic_number,
    )
    deal = Deal(
        9002,
        9001,
        position.identifier,
        "EURUSD",
        "buy",
        "in",
        engine.clock.now(),
        position.volume,
        position.entry_price,
        D("0"),
        D("-0.07"),
        D("0"),
        D("0"),
        position.magic,
        "USD",
        "TEST",
        "TEST",
    )
    account = replace(account, balance=D("999.93"), equity=D("999.93"), margin_free=D("999.93"))
    ack = ExecutionResult(
        Operation.OPEN,
        command.idempotency_key,
        account.key,
        ResultStatus.FILLED,
        9001,
        9002,
        None,
        position.volume,
        position.entry_price,
    )
    yield engine, plan, command, account, position, deal, ack
    await engine.shutdown()
    engine.database.close()


def prove(sample, *, position=None, deal=None, ack=None, settled=frozenset()):
    engine, _, command, account, original_pos, original_deal, original_ack = sample
    engine.authority.on_result(command, ack or original_ack)
    return engine.logger.reconcile(
        account, (position or original_pos,), (deal or original_deal,), settled_orders=settled
    )


def test_order_or_deal_id_is_not_mislabeled_as_position_ticket(sample):
    summary = prove(sample)
    engine, _, _, _, position, _, ack = sample
    assert summary["completed_intents"] == 1 and summary["reserved_risk_usd"] == "0"
    owned = engine.logger.owned(engine.account_key)[0]
    assert owned.ticket == position.ticket != ack.order_ticket and owned.identifier == position.identifier
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert (
            row.state == "reconciled" and row.request["result"]["position_identifier"] == position.identifier
        )
        assert session.scalar(select(RiskState)).accepted_entries_today == 1


def test_deduplication_does_not_double_fees(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    for _ in range(4):
        engine.logger.reconcile(account, (position,), (deal,))
    with engine.database.session() as session:
        assert len(session.scalars(select(BrokerDeal)).all()) == 1
        assert session.scalar(select(Trade)).profit == D("-0.07")


@pytest.mark.parametrize(
    "change", [{"profit": D("1")}, {"order_ticket": 9004}, {"position_identifier": 999}, {"magic": 1}]
)
def test_changed_existing_deal_ticket_halts_instead_of_rewriting_evidence(sample, change):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    summary = engine.logger.reconcile(account, (position,), (replace(deal, **change),))
    assert summary["ledger_mismatch"]
    with engine.database.session() as session:
        assert session.scalar(select(BrokerDeal)).profit == 0
        assert session.get(BotState, 1).last_error == "ledger_mismatch"


@pytest.mark.parametrize("kind", [ResultStatus.UNKNOWN, ResultStatus.ACCEPTED, ResultStatus.FILLED])
def test_absent_history_and_positions_is_never_a_no_fill_proof(sample, kind):
    engine, _, command, account, _, _, ack = sample
    engine.authority.on_result(command, replace(ack, status=kind))
    summary = engine.logger.reconcile(replace(account, balance=D("1000"), equity=D("1000")), (), ())
    assert summary["unsettled_intents"] == 1 and D(summary["reserved_risk_usd"]) > 0
    assert not engine.logger.owned(engine.account_key)
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)


def test_comment_time_and_magic_alone_never_prove_unknown_ownership(sample):
    engine, _, command, account, position, deal, ack = sample
    unknown = replace(ack, status=ResultStatus.UNKNOWN, order_ticket=0, deal_ticket=0)
    engine.authority.on_result(command, unknown)
    summary = engine.logger.reconcile(account, (position,), (deal,))
    assert summary["ledger_mismatch"] and summary["unsettled_intents"] == 1
    assert not engine.logger.owned(engine.account_key)


def test_unknown_with_exact_ids_and_complete_positive_deals_can_be_reconciled(sample):
    summary = prove(sample, ack=replace(sample[6], status=ResultStatus.UNKNOWN))
    assert not summary["ledger_mismatch"] and summary["unsettled_intents"] == 0
    with sample[0].database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "reconciled" and row.request["result"]["status"] == "filled"


def test_two_second_sdk_clock_precision_allowance_does_not_replace_id_proof(sample):
    position = replace(sample[4], time=sample[4].time - timedelta(seconds=1))
    deal = replace(sample[5], time=sample[5].time - timedelta(seconds=1))
    assert prove(sample, position=position, deal=deal)["completed_intents"] == 1


@pytest.mark.parametrize(
    "field,value", [("magic", 1), ("type", "sell"), ("symbol", "GBPUSD"), ("position_identifier", 888888)]
)
def test_entry_ownership_conflicts_are_not_adopted(sample, field, value):
    summary = prove(sample, deal=replace(sample[5], **{field: value}))
    assert summary["ledger_mismatch"] and not sample[0].logger.owned(sample[0].account_key)


def test_partial_without_final_order_proof_retains_risk_and_may_still_fill(sample):
    position, deal, ack = sample[4:]
    summary = prove(
        sample,
        position=replace(position, volume=D("0.01")),
        deal=replace(deal, volume=D("0.01")),
        ack=replace(ack, status=ResultStatus.PARTIAL, filled_volume=D("0.01")),
    )
    assert summary["unsettled_intents"] == 1 and D(summary["reserved_risk_usd"]) > 0
    assert not sample[0].logger.owned(sample[0].account_key)


def test_final_order_cancellation_can_prove_a_partial_fill_without_resending(sample):
    position, deal, ack = sample[4:]
    summary = prove(
        sample,
        position=replace(position, volume=D("0.01")),
        deal=replace(deal, volume=D("0.01")),
        ack=replace(ack, status=ResultStatus.PARTIAL, filled_volume=D("0.01")),
        settled=frozenset({9001}),
    )
    assert summary["unsettled_intents"] == 0 and summary["reserved_risk_usd"] == "0"
    owned = sample[0].logger.owned(sample[0].account_key)[0]
    assert owned.original_volume == D("0.01") and owned.target_usd == sample[1].target_profit_usd / 2


def test_manual_netting_addition_invalidates_ownership_not_adopts_more_volume(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    added = replace(deal, ticket=9010, order_ticket=9011, volume=D("0.01"))
    summary = engine.logger.reconcile(account, (replace(position, volume=D("0.03")),), (deal, added))
    assert summary["ledger_mismatch"] and not engine.logger.owned(engine.account_key)
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "unknown"


def test_rollover_position_ticket_updates_only_under_stable_identifier_proof(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    rolled = replace(position, ticket=8123)
    engine.logger.reconcile(account, (rolled,), (deal,))
    assert engine.logger.owned(engine.account_key)[0].ticket == 8123
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).position_identifier == position.identifier


def test_partial_and_final_exits_sum_whole_trade_fees_swap_and_net(sample):
    prove(sample)
    engine, _, _, account, position, deal, _ = sample
    out1 = replace(
        deal,
        ticket=9003,
        order_ticket=9004,
        type="sell",
        entry="out",
        volume=D("0.01"),
        profit=D("1"),
        commission=D("-0.035"),
        swap=D("-0.01"),
    )
    account = replace(account, balance=account.balance + D("0.955"), equity=account.equity + D("0.955"))
    engine.logger.reconcile(account, (replace(position, volume=D("0.01")),), (deal, out1))
    owned = engine.logger.owned(engine.account_key)[0]
    assert owned.volume == D("0.01") and owned.original_volume == D("0.02")
    assert owned.entry_costs_account == D("0.885")  # Signed realized NET, including partial exits.
    out2 = replace(out1, ticket=9005, order_ticket=9006, profit=D("2"), swap=D("-0.02"))
    account = replace(account, balance=account.balance + D("1.945"), equity=account.equity + D("1.945"))
    summary = engine.logger.reconcile(account, (), (deal, out1, out2))
    assert not summary["ledger_mismatch"] and summary["closed_trades"] == 1
    with engine.database.session() as session:
        row = session.scalar(select(Trade))
        assert row.profit == D("2.83") and row.profit_usd == D("2.83")
        assert row.commission == D("-0.14") and row.swap == D("-0.03")


def test_missing_position_without_complete_exit_legs_halts_but_does_not_fake_close(sample):
    prove(sample)
    engine, _, _, account, _, deal, _ = sample
    summary = engine.logger.reconcile(account, (), (deal,))
    assert summary["ledger_mismatch"] and summary["closed_trades"] == 0
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).status == "open"


def test_late_unknown_callback_cannot_downgrade_reconciled_fill(sample):
    prove(sample)
    engine, _, command, _, _, _, ack = sample
    engine.authority.on_result(command, replace(ack, status=ResultStatus.UNKNOWN))
    engine.authority.on_uncertain(command, "TEST late timeout")
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "reconciled" and row.request["result"]["status"] == "filled"
        assert session.scalar(select(RiskState)).reserved_risk_usd == 0


def test_wrong_result_key_or_operation_is_not_a_fill_proof(sample):
    engine, _, command, _, _, _, ack = sample
    for result in (replace(ack, idempotency_key="f" * 64), replace(ack, operation=Operation.CLOSE)):
        with pytest.raises(TradingDisabled):
            engine.authority.on_result(command, result)
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "submitting"


def test_late_known_fill_can_update_unknown_halt_but_owner_review_remains_required(sample):
    engine, _, command, account, position, deal, ack = sample
    engine.authority.on_uncertain(command, "TEST timeout")
    engine.authority.on_result(command, ack)
    summary = engine.logger.reconcile(account, (position,), (deal,))
    assert summary["unsettled_intents"] == 0
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled):
        engine.control.resume(OWNER, account_key=engine.account_key)
    engine.control.acknowledge_recovery(
        OWNER, account_key=engine.account_key, broker_writes_quarantined=False
    )
    assert engine.database.status()["state"] == "paused"


def test_positive_acknowledgement_cannot_be_erased_by_a_contradictory_rejection(sample):
    engine, _, command, _, _, _, ack = sample
    engine.authority.on_result(command, ack)
    engine.authority.on_result(command, replace(ack, status=ResultStatus.REJECTED))
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        assert row.state == "acknowledged" and row.request["reserved"] and row.request["counted"]
        assert row.request["result"]["status"] == "filled"
        assert session.get(BotState, 1).last_error == "ledger_mismatch"


@pytest.mark.parametrize("unknown_result", [False, True])
def test_definitive_rejection_after_unknown_releases_once_but_does_not_auto_resume(sample, unknown_result):
    engine, _, command, account, _, _, ack = sample
    if unknown_result:
        engine.authority.on_result(
            command,
            replace(ack, status=ResultStatus.UNKNOWN, order_ticket=0, deal_ticket=0, filled_volume=D("0")),
        )
    engine.authority.on_uncertain(command, "TEST timeout before definitive unsent result")
    with engine.database.session() as session:
        assert session.scalar(select(OrderIntent)).state == "unknown"
        assert session.scalar(select(RiskState)).reserved_risk_usd > 0
    rejected = ExecutionResult(
        Operation.OPEN,
        command.idempotency_key,
        account.key,
        ResultStatus.REJECTED,
        reason="TEST definitely unsent",
    )
    for _ in range(3):
        engine.authority.on_result(command, rejected)
    with engine.database.session() as session:
        row = session.scalar(select(OrderIntent))
        risk = session.scalar(select(RiskState))
        assert row.state == "rejected" and not row.request["reserved"] and not row.request["counted"]
        assert risk.reserved_risk_usd == 0 and risk.accepted_entries_today == 0
        assert session.get(BotState, 1).last_error == "unknown_execution"
    assert engine.database.status()["state"] == "paused"
    with pytest.raises(TradingDisabled, match="recovery"):
        engine.control.resume(OWNER, account_key=engine.account_key)
````

## File: `tests/test_trading_contracts.py`

<!-- SHA256: 049edaa24e636a8a040668e060d625f3b98816083f2e9c9d40957ff7ec416ae4 -->

````python
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
````

## File: `tests/test_trading_diagnostics.py`

<!-- SHA256: bafefb1b68ece8bb9b67961ec193f18a190fb4481e10bdaf25a57f40de65ecbe -->

````python
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
````

## File: `tests/test_trailing_engine.py`

<!-- SHA256: b0280cea97d3c5c15afaeade5b077e08d55a070905d098ef1b8a49813df022e7 -->

````python
from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.models import Trade
from tests.risk_helpers import OWNER, D, make_engine, open_one, safe_context
from trading.order_calculator import OrderCalculator
from trading.position_manager import PositionManager
from trading.risk_types import PositionReview
from trading.trailing_engine import TrailingEngine
from trading.types import BrokerError, Side, SourceKind


@pytest.fixture
async def engine(tmp_path):
    result = await make_engine(tmp_path)
    await open_one(result)
    yield result
    await result.shutdown()
    result.database.close()


async def at_quote(engine, bid):
    await engine.broker.set_tick("EURUSD", D(bid), D(bid) + D("0.00012"))
    await engine.reconcile()
    return (await engine.broker.get_positions())[0], engine.logger.owned(engine.account_key)[0]


async def test_earned_30_tier_is_deferred_when_stop_distance_makes_it_unachievable(engine):
    position, owned = await at_quote(engine, "1.10115")
    current = await engine.trailing.net_at_price(position, owned, D("1.10115"), stop=False)
    assert current >= owned.target_usd * D("0.3")
    assert await engine.trailing.plan(position, owned) is None
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 0


@pytest.mark.parametrize("bid,level", [("1.10165", 30), ("1.10250", 60), ("1.10280", 90)])
async def test_highest_earned_legally_achievable_lock_is_dollar_valued_and_persisted(engine, bid, level):
    position, owned = await at_quote(engine, bid)
    plan = await engine.trailing.plan(position, owned)
    assert plan and plan.lock_level == level
    assert plan.sl > position.sl and plan.estimated_net_at_stop_usd >= owned.target_usd * D(str(level)) / 100
    await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.sl == plan.sl and trade.profit_lock_level == level
        assert (
            D(trade.features_json["execution"]["last_verified_lock_net_usd"])
            >= trade.target_profit_usd * D(str(level)) / 100
        )


async def test_progress_at_60_can_fall_back_to_achievable_30_not_falsely_claim_60(engine):
    position, owned = await at_quote(engine, "1.10190")
    current = await engine.trailing.net_at_price(position, owned, D("1.10190"), stop=False)
    assert current >= owned.target_usd * D("0.6")
    plan = await engine.trailing.plan(position, owned)
    assert plan and plan.lock_level == 30


async def test_once_confirmed_lock_never_worsens_on_retracement(engine):
    manager = PositionManager(engine)
    await at_quote(engine, "1.10165")
    await manager.cycle()
    protected = (await engine.broker.get_positions())[0]
    await engine.broker.set_tick("EURUSD", D("1.10140"), D("1.10152"))
    await manager.cycle()
    position = (await engine.broker.get_positions())[0]
    assert position.sl == protected.sl
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 30


async def test_unverified_requested_lock_level_is_never_claimed(engine):
    owned = engine.logger.owned(engine.account_key)[0]
    await engine.protect_sl(owned.ticket, owned.identifier, D("1.09900"), lock_level=90)
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.profit_lock_level == 0 and trade.sl == D("1.09900")


async def test_no_change_can_verify_a_preexisting_better_stop_without_lowering_it(engine):
    position, owned = await at_quote(engine, "1.10165")
    await engine.protect_sl(position.ticket, position.identifier, D("1.10140"))
    position = (await engine.broker.get_positions())[0]
    owned = engine.logger.owned(engine.account_key)[0]
    plan = await engine.trailing.plan(position, owned)
    assert plan.sl == position.sl and plan.lock_level == 30
    await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=30)
    with engine.database.session() as session:
        assert session.scalar(select(Trade)).profit_lock_level == 30


async def test_stop_estimate_accounts_for_paid_entry_actual_swap_closing_fee_and_slippage(engine):
    position, owned = await at_quote(engine, "1.10165")
    meta = await engine.broker.get_symbol_info(position.symbol)
    changed = replace(position, swap=D("-0.20"))
    stop = D("1.10106")
    from trading.price_rules import adverse_price

    executable = adverse_price(
        stop,
        position.side,
        meta,
        engine.settings.max_slippage_points + engine.settings.trailing_spread_buffer_points,
        entry=False,
    )
    gross = await engine.broker.calculate_profit(
        position.symbol, position.side, position.volume, position.entry_price, executable
    )
    estimated = await engine.trailing.net_at_price(changed, owned, stop)
    assert estimated == gross + owned.entry_costs_account - D("0.20") - D("0.07")
    assert owned.entry_costs_account == D("-0.07")  # Never charge opening estimate twice.


async def test_lock_does_not_guarantee_a_gap_fill(engine):
    await at_quote(engine, "1.10165")
    await PositionManager(engine).cycle()
    position = (await engine.broker.get_positions())[0]
    await engine.broker.set_tick("EURUSD", position.sl - D("0.00100"), position.sl - D("0.00088"))
    await engine.reconcile()
    with engine.database.session() as session:
        trade = session.scalar(select(Trade))
        assert trade.status == "closed" and trade.profit < trade.target_profit_usd * D("0.3")
        assert trade.profit_lock_level == 30  # Historical confirmed estimate, NOT a guaranteed payout.


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_signed_actual_net_offset_solver_handles_both_sides(engine, side):
    calculator = OrderCalculator(engine.broker, engine.settings)
    entry = D("1.10000")
    target = D("1.50")
    price = await calculator.price_for_profit_usd(
        "EURUSD", side, D("0.02"), entry, target, net_offset_account=D("-0.30"), exit_slippage_points=2
    )
    from trading.price_rules import adverse_price

    meta = await engine.broker.get_symbol_info("EURUSD")
    executable = adverse_price(price, side, meta, 2, entry=False)
    gross = await engine.broker.calculate_profit("EURUSD", side, D("0.02"), entry, executable)
    assert gross - D("0.30") >= target and price % meta.tick_size == 0
    worse = price - side.sign * meta.tick_size
    executable = adverse_price(worse, side, meta, 2, entry=False)
    assert (
        await engine.broker.calculate_profit("EURUSD", side, D("0.02"), entry, executable) - D("0.30")
        < target
    )


async def test_sell_stop_only_moves_down_while_locked_net_moves_up(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        await open_one(engine, side=Side.SELL)
        await engine.broker.set_tick("EURUSD", D("1.09835"), D("1.09847"))
        await engine.reconcile()
        owned = engine.logger.owned(engine.account_key)[0]
        position = (await engine.broker.get_positions())[0]
        plan = await engine.trailing.plan(position, owned)
        assert plan and plan.sl < position.sl and plan.lock_level == 30
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=30)
        assert (await engine.broker.get_positions())[0].sl == plan.sl
    finally:
        await engine.shutdown()
        engine.database.close()


def atr_frame(clock, *, future=False):
    instants = [clock.now() - timedelta(minutes=5 * (15 - index)) for index in range(15)]
    frame = pd.DataFrame(
        {
            "time": instants,
            "close_time": [value + timedelta(minutes=5) for value in instants],
            "open": 1.10150,
            "high": 1.10155,
            "low": 1.10145,
            "close": 1.10150,
            "tick_volume": 10,
            "spread": 12,
            "real_volume": 0,
        }
    )
    if future:
        frame.loc[len(frame) - 1, "close_time"] = clock.now() + timedelta(minutes=5)
    return frame


def test_atr_is_a_closed_chronological_true_range(engine):
    frame = atr_frame(engine.clock)
    assert TrailingEngine.closed_atr(frame) == D("0.00010")
    frame.loc[0, "high"] = float("nan")
    with pytest.raises(BrokerError):
        TrailingEngine.closed_atr(frame)


@pytest.mark.parametrize("fault", ["short", "duplicate", "inverted", "negative"])
def test_invalid_atr_is_never_an_approval(engine, fault):
    frame = atr_frame(engine.clock)
    if fault == "short":
        frame = frame.iloc[:10]
    if fault == "duplicate":
        frame.loc[1, "close_time"] = frame.loc[0, "close_time"]
    if fault == "inverted":
        frame.loc[1, "high"] = 1.0
    if fault == "negative":
        frame.loc[1, "low"] = -1
    with pytest.raises(BrokerError):
        TrailingEngine.closed_atr(frame)


async def test_future_atr_candle_is_not_used_to_tighten_protection(tmp_path):
    engine = await make_engine(tmp_path, atr_trailing_enabled=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10060")  # Too early for a lock.
        assert (
            await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock, future=True)) is None
        )
        plan = await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock))
        assert plan and plan.reason == "closed_bar_atr" and plan.sl > position.sl
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_atr_can_beat_profit_tier_but_only_if_verified_net_is_sufficient(tmp_path):
    engine = await make_engine(tmp_path, atr_trailing_enabled=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10165")
        plan = await engine.trailing.plan(position, owned, candles=atr_frame(engine.clock))
        assert plan and plan.reason == "closed_bar_atr" and plan.lock_level == 30
        assert plan.sl > D("1.10106")
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_freeze_zone_defers_modification_without_claiming_lock(engine):
    position, owned = await at_quote(engine, "1.10280")
    meta = engine.broker.market._symbols["EURUSD"]
    engine.broker.market._symbols["EURUSD"] = replace(meta, freeze_level=20)
    assert await engine.trailing.plan(position, owned) is None  # Existing TP only 11 points away.


async def test_extension_defaults_disabled_and_owner_policy_is_not_an_ai_override(engine):
    position, owned = await at_quote(engine, "1.10280")
    review = PositionReview(
        engine.clock.now(), SourceKind.SYNTHETIC, 99, True, True, safe_context(engine.clock).news
    )
    assert await engine.trailing.extension(position, owned, review) is None
    with pytest.raises(BrokerError):
        await engine.extend_tp(position.ticket, position.identifier, position.tp + D("0.00050"), review)
    assert (await engine.broker.get_positions())[0].sl == position.sl


@pytest.mark.parametrize("fault", ["ai", "momentum", "volatility", "news", "source", "stale"])
async def test_extension_review_must_be_fresh_high_quality_in_all_dimensions(tmp_path, fault):
    engine = await make_engine(tmp_path, allow_tp_extension=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10280")
        review = PositionReview(
            engine.clock.now(), SourceKind.SYNTHETIC, 90, True, True, safe_context(engine.clock).news
        )
        change = {
            "ai": {"ai_confidence": 0},
            "momentum": {"momentum_continues": False},
            "volatility": {"volatility_safe": False},
            "news": {"news": replace(review.news, known=False)},
            "source": {"source": SourceKind.MT5},
            "stale": {"observed_at": engine.clock.now() - timedelta(seconds=31)},
        }[fault]
        assert await engine.trailing.extension(position, owned, replace(review, **change)) is None
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_120_percent_extension_preserves_sl_and_immutable_original_target(tmp_path):
    engine = await make_engine(tmp_path, allow_tp_extension=True)
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10280")
        plan = await engine.trailing.plan(position, owned)
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
        position = (await engine.broker.get_positions())[0]
        review = PositionReview(
            engine.clock.now(), SourceKind.SYNTHETIC, 90, True, True, safe_context(engine.clock).news
        )
        tp = await engine.trailing.extension(position, owned, review)
        assert tp and tp > position.tp
        assert abs(tp - position.entry_price) <= abs(owned.original_tp - position.entry_price) * D("1.2")
        engine.control.kill(OWNER)  # Live-entry permission is irrelevant to protective maintenance.
        await engine.extend_tp(position.ticket, position.identifier, tp, review)
        after = (await engine.broker.get_positions())[0]
        assert after.sl == position.sl and after.tp == tp
        with engine.database.session() as session:
            row = session.scalar(select(Trade))
            assert (
                row.target_profit_usd == owned.target_usd
                and D(row.features_json["execution"]["original_tp"]) == owned.original_tp
            )
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_non_usd_locks_use_verified_signed_fx_not_usd_parity(tmp_path):
    engine = await make_engine(tmp_path, account_currency="EUR", account_to_usd_symbols={"EUR": "EURUSD"})
    try:
        await open_one(engine)
        position, owned = await at_quote(engine, "1.10165")
        plan = await engine.trailing.plan(position, owned)
        assert plan and plan.estimated_net_at_stop_usd >= owned.target_usd * D("0.3")
        await engine.protect_sl(position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level)
        with engine.database.session() as session:
            row = session.scalar(select(Trade))
            assert row.currency == "EUR" and not row.features_json["execution"]["profit_usd_verified"]
            assert row.features_json["execution"]["historical_fx_required"]
    finally:
        await engine.shutdown()
        engine.database.close()
````

## File: `trading/__init__.py`

<!-- SHA256: 8e9294dba06dad6105837011a2caa09fb0135b42cc85526bcb67921149db8766 -->

````python
"""Trading adapters. Importing this package never connects to a terminal."""
````

## File: `trading/authorization.py`

<!-- SHA256: e8435eaeae782963cb35fc52ef26ef16e05c94678cef8f0d6e940bf486a06d9e -->

````python
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
````

## File: `trading/candles.py`

<!-- SHA256: 7f88203a483a7188e8e7707686b9f765eb6f67b226431e4845f79afa45763db5 -->

````python
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
````

## File: `trading/client_helpers.py`

<!-- SHA256: 2bdefb1629d296af3986cc4797893c3d594c35958743b687746a7e017698af0f -->

````python
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
````

## File: `trading/currency.py`

<!-- SHA256: 5ef921a5d31936acb0b5e1a34b29575d03ef2b762fe4b8661cb85a99ad202e16 -->

````python
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
````

## File: `trading/mock_mt5.py`

<!-- SHA256: 07b4c8205a85198521261fede8358228ded6fe475abcdd0be35bcfc1e75a61d5 -->

````python
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
````

## File: `trading/mt5_client.py`

<!-- SHA256: a368f38549007d022eed7a8ebc95460e9f4b7a7485425ded3d8cafb6af54f3fd -->

````python
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
````

## File: `trading/order_calculator.py`

<!-- SHA256: c8f5fb27fcb2c92a8182d03d2ff093e9561d7eceb7bbf88392c7096d8b4ebe43 -->

````python
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
        net_offset_account: Decimal = ZERO,
    ) -> Decimal:
        if not isinstance(target_usd, Decimal) or not target_usd.is_finite() or target_usd < ZERO:
            raise InvalidOrder("target must be a finite nonnegative USD amount")
        if not isinstance(net_offset_account, Decimal) or not net_offset_account.is_finite():
            raise InvalidOrder("net offset must be a finite signed account-currency Decimal")
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
            return await self.currency.to_usd(value - costs + net_offset_account)

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
````

## File: `trading/paper_mt5.py`

<!-- SHA256: ae7b84887cbbe91dbb123ff6b459b2bf58eafa08d274596467676ee59569fbfb -->

````python
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
````

## File: `trading/price_rules.py`

<!-- SHA256: 5b3938365988b5297b4131bb645a66903168c936c578f5f6077b705d9f6f077a -->

````python
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
````

## File: `trading/simulation.py`

<!-- SHA256: b798efc0884dd87e068ab3db042d69fe1ce4a7ac76d6b7fae23a73ab554358bc -->

````python
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
````

## File: `trading/symbol_manager.py`

<!-- SHA256: 00e34f14839a6ac3d281cbba674cf58fc8ba9bf65e6f0de479a2306a9400bcc5 -->

````python
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
````

## File: `trading/types.py`

<!-- SHA256: 80d8da60f41dea066b327bbea6d2f3a739d14bc4be5b9ef80285c5d1dd7a5c90 -->

````python
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
````

## Validation scope

# Executed validation — release 0.3.0 (Parts 1–5)

Date: **2026-10-03**. Environment: Linux x64, **CPython 3.13.14**, isolated venv.
Windows x64 / Python 3.11 is the reference MT5 deployment and remains **untested**.

## Results

- **444 pytest tests/parameter cases passed** in one full run; no warnings.
  Previous release had 199. The cumulative suite retains foundation/broker
  contracts and adds durable risk/execution/control/ledger/trailing regressions.
- Ruff **0.16.10** global lint and format check: passed; 62 Python source files
  plus the documentation considered by the formatter.
- Python compileall: passed for core/trading/scripts/tests/CLI.
- Installed Linux dependency stack and `pip check`: no broken requirements.
  Native MetaTrader5 was skipped by its Windows platform marker.
- Environment-isolated CLI `check-config`, `init-db`, `status`: paper/mock,
  schema 2, paused, kill=false, heartbeat=null; no broker connection.
- Environment-isolated operator `migrate-db`: real file-SQLite schema-1 fixture,
  integrity-checked schema-1 backup, schema 2 result, kill/daily/drawdown latches
  and 1200 high-water preserved; JSON response is one parseable object.
- **Both** isolated synthetic CLIs passed: `smoke_mock` and `smoke_risk`.
  Neither reads host mode/credentials, loads the native SDK or qualifies as
  stage evidence. Runtime SQLite/checkpoints use temporary directories.
- Windows read-only diagnostic on Linux: structured unsupported-platform
  response, exit code 2, zero orders; no connection attempted.
- Release archive CRC/path/file-manifest integrity checked. A clean extraction
  passed all 444 tests, lint/format/compile/config/init/status and both smokes
  using the same already installed Linux venv. This is not a second-platform or
  fresh dependency installation test.

Commands executed:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q core trading scripts tests main.py config.py
.venv/bin/python -m pip check
.venv/bin/python -m scripts.smoke_mock
.venv/bin/python -m scripts.smoke_risk
# CLI validation uses isolated PROJECT_ROOT/temp SQLite, not the old release DB.
.venv/bin/python main.py --env-file .env.example check-config
.venv/bin/python main.py --env-file .env.example init-db
.venv/bin/python main.py --env-file .env.example status
# migrate-db was exercised ONLY against a separate known schema-1 test fixture.
.venv/bin/python main.py --env-file .env.example migrate-db
```

Do not blindly run `migrate-db` against an active or fresh schema-2 installation.
Follow `MIGRATIONS.md`. Diagnostics/test JSON synthetic gains are engineered
accounting assertions, not strategy results, historical returns or a forecast.

## Coverage actually exercised

### Foundation and broker/calculation contracts (Parts 1–4 retained)

- Paper/mock/paused configuration, mode conflicts and immutable nested maps;
  hard risk/trailing/embargo controls, owner/token pairing, TLS/trusted hosts,
  protected project paths and stable configuration/policy fingerprints.
- SQLite Decimal/UTC/schema/rollback, persistent kill state, append-only audits,
  sanitized known-secret/header/URL/traceback logging and strict bounded JSON.
- Native nonlinear profit/margin valuation through marked fake SDK; BUY/SELL
  lot sizing, downward lots, below-minimum skip, fees/slippage/reward-risk,
  physical/tick price bounds and explicit signed non-USD conversion.
- Fresh/old/future quotes, spread/deviation/stop/freeze, no loosened SL, approved
  bounded TP extension, stable maintenance hashes, finalized-only candle bars.
- Synthetic bid/ask gap SL/TP, opening/closing commission/swap/net P&L, alias
  resolution, unknown exposure, separate real-data paper **never** calling the
  source broker's write methods.
- Marked fake SDK thread serialization, process lease/account pinning/reconnect,
  native mode/default-deny, strict finite trusted grants, changed portfolio/quote/
  margin/permissions/pending/expiry, post-check control hook veto before send.
- Native filled/partial/accepted/rejected/unknown classification, durable callback
  failure, nonblocking timeout/cancellation/read/shutdown, late callbacks and
  retained SDK lease until worker actually completes. No guessed position IDs.
- Positive final native order history (filled/canceled/expired/rejected) versus
  non-final/foreign/absent history. Pending-order absence is never settlement proof.

### Durable risk, controls and stages (Part 5)

- Transactional entry count/risk reservation; one granted and one vetoed under
  **spawned cross-process** competing reservations. No broker submission involved.
- Persistent runtime lease/paused startup, authenticated-internal owner checks,
  pause/kill, no auto-resume, explicit recovery/kill-reset review and expiry.
- Complete daily/peak/cash/credit accounting, new-day carry-gap and fee loss,
  persistent drawdown, no loss-latch reset by restart/kill review, sampled-history
  gaps, migrated baseline review, foreign/open/unsettled exposure veto.
- Missing cash evidence and unknown deposits/withdrawals halt; no fake profits,
  peak inflation or loss latches from the unexplained balance. Late cash evidence
  adjusts anchors once and requires explicit owner review. Deposit fees remain
  costs; a midnight unresolved reservation remains protected.
- Fresh news AND calendar/AI/technical/source/bars, exposure/lot/margin/spread/
  daily limits, native persisted-Signal exact binding and single-use reservation.
- Report/schema/dataset hashes, chronological progression, code/model/config/
  owner source binding, live nonce/expiry/single-use/revocation and pre-send review.
- Independent authored TEST-only paper/demo ledger fixtures: exact intent and
  entry/exit volume, whole net P&L/PF, credit-aware drawdown and continuous account
  journal coverage. Missing native intent/exit legs, forged PF/coverage/report/
  datasets/source/code/model fail. These authored fixtures are not real stage evidence.

### Durable execution, reconciliation and protection (Part 5)

- Concurrent same-key entry sent once; conflict veto, persistent duplicate result
  after restart/close, no minimum-lot escalation, no forced trade count.
- Pause after authorization prevents sending and releases definitely unsent risk.
- Cancellation/checkpoint failure retains reservation, pauses/quarantines writes
  and never blindly replays. Snapshot precedes ACK; a failed SQL callback can be
  recovered by the matching exact durable shadow cache without re-execution.
- Positive ACK cannot be overwritten by late uncertainty/contradictory rejection.
  Definitive unsent rejection after UNKNOWN releases reservation/count exactly
  once but does not auto-resume. Post-ACK reconciliation error halts, preserves
  positive fill evidence and needs reconciliation plus separate owner recovery.
- Corrupt/missing/oversized/duplicate-key/config/account/source-mismatched snapshot
  refusal; no capital reset; atomic save/read-triggered automatic TP accounting.
- Exact order/deal IDs → stable position identifier/current ticket; comment/magic/
  time alone never adoption; changed deal evidence, foreign netting additions,
  partial/final/canceled-order proof, full exit-volume proof and fee/swap accounting.
- Owned-only management while entries paused/killed, wrong-owner/ticket denial,
  no adoption of manual-looking bot comments. Unknown non-USD historical dollars
  explicitly flagged unverified, never fabricated by current FX.
- BUY/SELL 30/60/90 tier feasibility, off-grid VWAP, partial-exit signed offsets,
  entry/exit fee/swap/slippage/buffer/FX and verified readback; lower-feasible tier
  or defer instead of falsely claiming a clamped lock.
- Fresh finalized-bar ATR and no worse SL; 120% extension review/freshness/momentum/
  news/volatility/cap/legal-price controls; no original-target mutation.
- Explicit SQLite migration backup/integrity/latch preservation, active/leased/
  uncertain/schema-drift refusal, no silent repeated/in-memory migration.

## Limits — what was NOT verified

No native MetaTrader5 module/Windows terminal was imported or executed. No broker
login/connection, demo/live order, withdrawal, password change, actual promotion,
model training/ML function, historical strategy backtest, AI/news/Telegram request,
Mini App server, scheduler/watchdog/deployment or unattended trading was tested.
No credential or owner approval for real execution was supplied.

Native tests use marked **fake SDKs and TEST-only authorities/hand-authored DTOs**.
A trusted Python plugin/DB administrator can violate internal seams; this is not
hostile-Python sandboxing. File checksums are not signatures/tamper-proof storage.
Separate account/history/position reads are not an atomic broker transaction and
may conservatively mismatch/halt. Dedicated account and broker-side protection
remain necessary. MT5 has no native exactly-once key; unknown outcomes still
require positive reconciliation/owner investigation, never absence-based replay.

PostgreSQL server/roles/migration, Windows dependency/runtime behavior, broker-
specific margin/swap/partial/netting/financing and vulnerability audit are untested.
The Linux lock is a resolved local freeze, **not** a Windows deployment lock.
ML/data package resolution is not proof their later integration has been tested.
Snapshot streaming is bounded; large intent/deal history still needs profiling.
Unclassified correction/bonus cash and unresolved native fills intentionally
require operator investigation; no bypass/classification UI is supplied yet.

MetaTrader5 5.0.6231 previously had a Windows CPython 3.11 x64 wheel verified as
available. Wheel availability is not runtime validation. Pins/tests are not a
security certification or live-readiness audit. Mandatory backtest → real-data
paper → broker demo → explicitly approved small-live progression remains in force.

## Next: Part 6 — strategy, signals and router

Part 6 will connect finalized candle/trend/volatility features, weighted strategy
candidates and fresh reviewed context to the persisted-Signal/risk authority.
It cannot bypass missing news/AI, owner controls or promotion/live gates.

Say CONTINUE to generate the next part.
