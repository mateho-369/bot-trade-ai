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


## Part 6 / 0.4.0 — schema 2 retained, policy/code hashes change

There is no Part 6 schema DDL: a valid schema-2 DB keeps its existing tables,
financial ledger, risk counters, kill/daily/drawdown latches and audit history.
Do not run `migrate-db` a second time or recreate the DB to bypass those controls.

New strategy/volatility settings and runnable strategy code change configuration,
strategy/model-baseline and code hashes. Prior promotion/live approvals therefore
cannot authorize new entries. Config-bound managed shadow checkpoints may reject
the new policy; that refusal is intentional, not permission to reset paper capital.

Keep matched release-0.3.0 code/config/DB/checkpoint together. Use the retained
Parts 1–5 archive to review/reconcile its simulated exposure/history under that
matching release before planning an owner-reviewed policy transition. No automatic
checkpoint/policy rebinding tool is supplied in Part 6; do not edit hashes or
silently discard state to manufacture fresh stage results. Separate temporary
smoke fixtures are diagnostic ledgers, never a substitute for that history.

Native-generated signals now additionally need `reflex-signal-v1` immutable
proposal/review proof. Legacy manually inserted approved Signal rows cannot
bypass the new publisher contract. Historical fills still require positive exact
broker/deal evidence and protective management, not replaying their old signal.
Keep entry startup paused and investigate unresolved native state with the owner.


## Part 7 / 0.5.0 — schema 2 retained; AI/learning policy hashes change

No DDL or financial-state reset: existing 15 schema-2 tables support Signals,
Trades/fill-proof labels, AISuggestions, ModelVersions and stage/owner records.
Learning exports bounded immutable content-addressed JSON, not a new schema table.

New AI/learning fields and runnable source change policy/code/baseline model hashes.
Old approvals, signals/models/evidence are not permission for the new runtime.
Current learning exports exclude incompatible old-code/policy records; they are
never silently relabelled or rewritten. A matching old-release code/config/DB/
checkpoint must be preserved for recovery. Do not manually edit checksums/reset
capital or copy old stage approval fields to manufacture eligibility.

A stopped owner-approved settings application records a projection and returns
new frozen Settings, but does not edit .env, refresh live consent, start a runtime
or rebind existing paper checkpoints. get_settings() does not silently load DB
projections. Explicit operator review/persistence/recomposition is necessary;
changed checkpoints may refuse and no automatic policy-transition tool is supplied.
Risk latches/counters/high-water/capital/history remain authoritative and preserved.
Model selection is similarly stopped/flat, not a hot swap or a broker permission.
Native TP-extension reviews additionally need position and code/model binding;
legacy generic native PositionReview can no longer authorize target extension.


## Part 8 / 0.6.0 — schema 2 retained; news evidence policy/code changes

No DDL, capital/counter reset or financial-state projection. The existing News
and append-only AuditLog tables support story dedup, causal risk observations,
pending notifications and current scoped publication epochs. Immutable canonical
snapshots are stored separately at data/news/snapshots/<sha256>.json. Include them
in reviewed backups with DB/checkpoint for investigation and future causality.

New news/source/entitlement/calendar controls and runnable source invalidate old
policy/code/model-baseline hashes, signals, stage/live approvals and managed
checkpoint compatibility. Preserve the matching old code/config/DB/checkpoint;
never rewrite checksums or erase positions/capital to bypass startup refusal.
Native-data new entries and TP extensions now require current symbol-bound
managed non-fixture publication proof; legacy known/safe timestamp DTOs are not
native permission. Old documents/manifests remain historical release snapshots.

No calendar is seeded/migrated as current genuine coverage. Startup/shutdown and
refresh claims publish unknown until complete new collection succeeds. Schema
upgrade does not attest feed entitlement, true timestamps, complete coverage,
source authenticity or market performance, and never resumes the owner state.
Review real licensed feeds/calendar JSON/currencies/timezone/quotas and validate
native integration before recomposition. Missing old snapshot/history is unknown,
not permission to synthesize a historical safe window. Do not prune referenced
snapshots or silently relabel synthetic fixtures as genuine news/native evidence.
