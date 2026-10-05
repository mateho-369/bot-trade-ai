# Part 10 — runtime, scheduler, watchdog and deployment

**0.8.0 · cumulative Parts 1–10 · schema 2 retained · 2026-10-03.**

Real implementation, not timer-loop pseudocode. The source-only release has been
built here; no Windows deployment, genuine broker/Telegram/provider connection,
compiled EXE, live permission, profitability or independent security audit is claimed.
Validation commands/results are in `VALIDATION.md`. Part 11 supplies historical
replay/backtester, reporting and final usage. Do not promote before that validation.

## Safe composition

`main.py` remains the non-trading operator CLI. **`python -m app.bot` is a separate,
explicit runtime startup**, not an import effect. It requires an existing reviewed
source-root `.env` and initialized persistent DB. Missing SQLite DB is refused
before a connection can create an empty financial store. Schema is verified, never
initialized/migrated by the daemon/supervisor. BACKTEST mode cannot be a daemon.

`app/dependencies.py` constructs the actual guarded broker, ExecutionEngine,
SignalEngine, AISupervisor/SuggestionStore/ModelRegistry, NewsManager,
PositionManager and shared OwnerServices. Constructors do not login, fetch feeds,
start Telegram, select a new model, activate/apply proposals, resume or trade.
Enabled model filtering requires one explicit valid active model of the correct
mode/age/artifact/evaluation; no silent baseline fallback. Without it, the explicit
rule-baseline hash is not a trained-model/evidence claim.

- MOCK: deterministic synthetic market/book, never native market or promotion proof.
- PAPER + REAL: native read-only MT5 market wrapped in simulated execution; actual
  **market provenance**, not the paper execution label, binds all service profiles.
  Underlying native write authority stays deny-all. No native order is sent.
- DEMO/LIVE + REAL: guarded MT5 broker; actual account/owner/risk/protection/source/
  intent/news/AI/stage/expiry gates remain unchanged. Configuration is not permission.
- Native daemon/supervisor requires Windows and a nonzero interactive session;
  Session 0/service operation is refused. This is not a logout-proof service.

Every claim/restart begins **PAUSED** (or retains KILLED), preserving original
capital, checkpoints, daily/drawdown/kill/recovery latches, ownership, history and
pending/uncertain intents. Fresh reconciliation is required; no unknown write is
resubmitted. No remote reset/live/apply/model-activate/open-order endpoint was added.

```text
explicit local launcher → OS runtime lock → existing schema verification
     → actual-source/code/selected-model profile → durable paused claim
     → restore/reconcile broker/book → readonly signals + AI + news initialization
     → authenticated owner API + polling OR secret-header webhook
     → bounded APScheduler jobs + independent local health monitor
     → stop: downward pause/fence → drain requests/jobs/native futures
            → close transports/resources → persist paused release → unlock
```

Factories are local trusted Python seams, not remote authentication. All imports
remain inert. Nothing in this installment contacts your broker/bot/provider or
deploys a Windows task merely by extracting the archive or importing modules.

## Scheduler contracts

One process, one API worker, APScheduler 3 AsyncIOScheduler in **UTC**. Every job
has `max_instances=1`, coalescing and a short misfire grace; internal locks also
prevent overlap from explicit direct invocations. Missed runs do not catch up
orders. OS lock plus the persistent SQL lease exclude competing runtimes.

| Job | Default cadence | Effect and guard |
|---|---|---|
| heartbeat | 10 seconds | Session-bound SQL renewal and private local health; no resume |
| positions | 5 seconds | Reconcile owned exposure, profit locks/ATR protection; continues PAUSED/KILLED |
| signals | 30 seconds | Only RUNNING, no kill/error/overdue/local-stop/code mismatch; finalized bars → stored news window → actual AI review → existing execute_signal bridge |
| news | 180 seconds | Explicit bounded collector; unknown/stale/incomplete coverage vetoes entries |
| news advisory | >=300 seconds | Typed advisory sentiment only; cannot green deterministic news gates |
| position reviews | 300 seconds | Optional TP-extension reviews; protection cycle does not wait for AI; stale/mismatched reviews are vetoed |
| notifications | 30 seconds | Owner-only durable runtime/news outboxes, no blind resend |
| daily report | 00:00 UTC | Last 24 hours of attributable closed-trade review; advisory, not stage evidence |
| learning | 02:00 UTC, OFF by default | PAUSED + owned-flat start; purged training → INACTIVE candidate only; no activation/application/resume |
| online DB snapshot | 03:00 UTC | SQLite backup API; explicitly NOT a coherent paper recovery bundle |

A zero-trade day remains correct. The 6/day target is never used to force an order;
12/day is the default hard cap. No job bypasses risk/news/confidence/spread/margin/
ownership/SL/stage gates or applies owner-approved suggestions automatically.

Heartbeat cadence must not exceed one third of the lease; position cadence must
precede risk-observation expiry. Shutdown budget must cover a native API timeout.
Both limits and cron hours are validated; `.env.example` includes all new fields.

Owner resume remains revision-fenced in the SAME locked SQL transaction. Heartbeat
and reconciliation can invalidate a prepared resume before its 45-second TTL.
Confirm promptly; otherwise prepare a fresh challenge. Never remove that fence.

## Shutdown and native uncertainty

Normal shutdown fences new resume/close/proposal actions immediately, preserves
fast downward pause/kill, pauses entries and stops accepting scheduled jobs. In-
flight accepted actions drain before their broker/DB disappears. No forced close-
all or automatic liquidation occurs. Broker-side protections are not removed;
local trailing cannot function while the runtime is stopped/disconnected.

The API has no forced request-cancellation deadline. Telegram initial polling
checks the shutdown fence after its webhook read, so a stop during startup cannot
begin a new poll. Existing aiogram handler tasks and owner sagas are drained.

APScheduler executor shutdown is invoked only AFTER active jobs drain; otherwise
its cancellation behavior could abandon a broker write. An overdue task is
recorded/degraded, entries halt, and shutdown/replacement is withheld until the
actual work finishes. The configured shutdown budget is a **drain diagnostic
window**, not a license to cancel/kill uncertain work.

MT5Client now tracks **concurrent native Futures**, not just asyncio wrappers.
A timeout/cancelled wrapper does not mean the SDK thread exited. Further SDK
queueing is refused while prior unresolved work exists; one shutdown may queue
behind it. DB/resources and OS lock remain held until native futures actually
finish, including late durable acknowledgement callbacks. Quarantine is not reset.

## Watchdog — 30 seconds by default

`watchdog.py` launches an explicitly configured **direct child** with shell=False,
known working directory and an opaque generation UUID. It never adopts a PID file,
terminates an arbitrary process, kills a hung child, resumes entries, clears a
latch or resends an order. Local health is diagnostic, not trading permission.

Health is bounded atomic private JSON under `DATA_DIR/runtime`, containing PID/
creation time/generation/session/code/model/source/config hash, job availability,
control state and native-pending count. No account number, positions or credentials.
Its freshness, exact child identity and fingerprints must match. A previously
ready child receives no new startup grace when health later becomes stale.
Default startup grace is 240 seconds; default staleness is 90 seconds.

- Confirmed child exit: wait for old SQL lease expiry/release AND free OS lock,
  reserve a durable SQL launch attempt, then launch PAUSED. Alert is queued.
- Alive but stale/overdue/quarantined: write a generation-bound graceful stop,
  queue a warning, **withhold replacement while that child remains alive**.
- Restart budget: default **3 launches per rolling hour, including initial start
  and failed spawns**. Reservations survive a new supervisor object/process.
  Capacity recovers with the rolling window, never through deleting audit history.
- Code or `.env` changes: request stop and end this supervisor. Explicit operator
  review/restart is required; no silent policy/credential rebinding.
- Another runtime/lock owner: wait, never adopt/kill it. SQL expiry alone is not
  proof that a stuck native thread has ended.

Runtime/restart/failure/report/model notices and news alerts are owner-only.
Delivery attempt is claimed BEFORE sending. Success is recorded after Telegram
succeeds; timeout/cancel/lost ack is uncertain and never blindly retried. Missed
notices are possible. This is not exactly-once delivery. No configured token means
no Telegram transport; pending local notices do not contact Telegram.

## Explicit local stop and restart

```powershell
# Requests both supervisor and child to stop; persists across logon/reboot.
.\.venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env
# Diagnostic only: never broker/provider/polling/control writes.
.\.venv\Scripts\python.exe -m scripts.runtime_status --env-file .env
# After confirming clean stop, clear only the local stop request; NOT risk/kill.
.\.venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env --clear-request
# Explicit supervisor start; all new runtime sessions remain PAUSED.
.\.venv\Scripts\python.exe watchdog.py --env-file .env
```

Foreground runtime Ctrl+C and supervisor signals request graceful stop, not force
cancellation. `--clear-stop-request` on an explicit watchdog launch clears ONLY the
local operator marker after acquiring the supervisor OS lock. It grants no resume,
kill reset or live permission. Logon launchers never silently delete this marker.
Do not use Task Scheduler's forced stop or arbitrary PID killing as broker recovery.

## Backups and manual recovery

`scripts/backup_db.py` defaults to a **STOPPED coherent SQLite recovery bundle**.
It acquires the same runtime OS lock, requires no persisted runtime session and
no RUNNING state, verifies schema, uses SQLite's online backup API (includes WAL),
and bundles regular bounded paper/model/news/candle/calendar artifacts. No `.env`,
logs, private keys, executable/pickled models or runtime credentials files are
included. Trading DB/artifacts remain private financial data: protect/encrypt them.
Do not put secrets inside artifact JSON; exclusion is not content-anonymization.

CRC/hash manifest, fsynced complete ZIP, unique atomic publication, <=1024 members,
<=16MiB/artifact, <=64MiB total and bounded SQLite copy work. The source DB and
settings/file identity must match. Unexpected/symlink/changing artifacts fail closed.
Daily retention deletes only verified scoped DB-ONLY snapshots (7 by default),
never coherent bundles, arbitrary files, audit history or checkpoints.

```powershell
# First stop supervisor/runtime cleanly; keep original DB/checkpoint untouched.
.\.venv\Scripts\python.exe -m scripts.backup_db --env-file .env
# Explicit ONLINE DB snapshot only; cannot restore paper capital coherently alone.
.\.venv\Scripts\python.exe -m scripts.backup_db --env-file .env --db-only
```

No automated restore/reset helper is provided. Retain originals, verify archive
CRC/manifest, restore a matching stopped DB + checkpoint/artifact set into a separate
private directory, verify schema/code/config/model, inspect actual broker positions/
orders/deals, resolve uncertain intent attribution, then start PAUSED and review
owner/risk/stage gates. Never clear history/latches or regenerate capital to make
recovery green. A restored online DB alone is NOT safe paper-ledger recovery.
PostgreSQL uses reviewed `pg_dump` + a separately stopped coherent artifact bundle;
SQLite helper refuses PostgreSQL, and this optional driver/database is unvalidated.

## Windows/VPS/EXE deployment

See `DEPLOYMENT_WINDOWS.md` for full operator steps, current-user interactive logon
Task Scheduler, NTFS/TLS/firewall controls, VBS launchers, optional onedir packaging,
backup/stop and troubleshooting. Real scripts are included, not executed here.
Default terminal path remains `C:/Program Files/MetaTrader 5/terminal64.exe`.
Minimized/background is not a service or guarantee of uptime after logoff.

Frozen EXE runtime code identity includes the executable digest, so compiled
builds cannot reuse source-only stage approvals/evidence. Keep the full source
project beside the reviewed source-root `.env`; no compiled or Windows validation
is claimed. Optional packaging never deploys, starts or enables trading.

## Offline diagnostics and release record

```bash
python -m scripts.smoke_runtime
python -m pytest -q tests/test_runtime*.py
python -m pytest -q
ruff check .
ruff format --check .
```

Runtime smoke ignores dotenv/process environment, uses an isolated explicit TEST
DB/bootstrap, synthetic broker, disabled provider/API/Telegram and scripted child
handles: 13 assertions, no real/simulated order operation, actual child spawn,
native SDK or genuine provider/Telegram request; no stage eligibility.

Part 9 archive remains immutable and its final CRC, file hashes, all 226 literal
source sections and 220 runnable/config bytes against its independently validated
clean candidate were checked. **Correction:** its historical manifest accidentally
retained Parts 1–8 / 1085-test metadata; the actual Part 9 source/validation records
are Parts 1–9 / 1504 working and clean tests. Historical hashes/archive were NOT
rewritten to conceal this. `RELEASE_10_MANIFEST.json` is authoritative for this
current release, including the corrected predecessor metadata. Integrity hashes
are not signatures, market evidence, profitability proof or live authorization.

The next installment is Part 11 — chronological historical backtesting, costs,
news/risk/trailing simulation, metrics/stage reporting and full usage playbooks.
