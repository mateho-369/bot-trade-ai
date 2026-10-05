# Completed replay bundle audit — operator guide

**0.12.0 / schema 2. Research-only internal consistency, NEVER trading authorization.**

## 1. New completed run workflow

```bash
python -m scripts.smoke_bundle_audit
python -m scripts.make_backtest_fixture --output data/fixtures/ARTIFICIAL_audit_001 --replay-minutes 1
python -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_audit_001/manifest.json --output data/backtests/ARTIFICIAL_audit_001 --review-mode synthetic_research --simulate-orders --close-at-end
python -B -m scripts.audit_backtest --run data/backtests/ARTIFICIAL_audit_001
```

Synthetic prices/contracts/news/confidence are engineered, not actual broker/AI capture.
The optional simulated entry/close is confined to that NEW private historical BACKTEST
ledger. Normal default reviewer=veto/no simulated resume still produces a valid zero-trade
bundle. ML-enabled research continues to require the Part13 frozen model/corpus path;
see `ML_REPLAY.md`. No production registry, original financial ledger or credentials are copied.

The runner shuts down/releases the shared engine, shuts down the historical market and
closes its private DB BEFORE completion/sealing. Ordinary SQL close may finalize its own
normal journaling; the auditor never opens/checkpoints/replays the source DB. Sealing runs
in a worker, not on the async event loop. A failure preserves evidence and reports incomplete.
Existing directory or bundle => refusal, never overwrite/reset/recover by re-sealing.

## 2. Exact closure

`bundle.json` format `reflex-replay-bundle-v1` contains:

- purpose=research_only, source=historical, completed=true;
- promotion_eligible/genuine_owner_authenticated/production_model_activated=false;
- fixed dataset/code/model/strategy/safety bindings;
- exact relative file -> raw SHA-256 and byte length, self hash excluded.

Allowed files are run/report/completion, five journals, private `data/replay.db`, private
`data/paper/state.json`, exact captured manifest-declared `inputs/`, and the ONE selected
`data/models/<SHA>.json` if ML was supplied. No unlisted files, secret .env/bearers/plugin
source or extra model schedule. A missing file, failure.json, link/FIFO/case alias or SQLite
sidecar is a refusal, not an ignored diagnostic. Complete inventory is verified, then
unchanged bytes/inventory are checked again after semantic work. Original content is captured
immutably; the original paths are never reloaded to revise a captured input mid-audit.

Bounds: <=256 total files, <=4096 walk nodes, total <=128 MiB; ordinary <=32 MiB, DB <=64 MiB;
bundle <=1 MiB, usual metadata JSON <=4 MiB; JSONL line <=32768 bytes, ordinary journal
<=200000 rows, equity <=500002. Strict duplicate/nonfinite/UTF8/depth/path/length checks.
Large research runs need a shorter NEW interval; exceeding bounds does not waive safety.
An old pre-Part14 directory without closure is NOT auto-upgraded. Preserve it as unverified
historical output; rerun only in a NEW explicit research directory if appropriate.

## 3. Auditor CLI and optional external anchor

```bash
python -B -m scripts.audit_backtest --run data/backtests/ARTIFICIAL_audit_001
# OPTIONAL: use a literal lower-case SHA of bundle.json obtained from a genuinely trusted channel.
python -B -m scripts.audit_backtest --run data/backtests/ARTIFICIAL_audit_001 --trusted-bundle-sha256 YOUR_REAL_64_LOWERCASE_SHA256
```

The illustrative token is NOT a valid/runnable hash. Never replace a bad external digest
with the convenient current local hash. Matching local bytes do not authenticate their
producer; modifying BOTH data and manifest can defeat local hashing. An external archive/
source review/private interpreter dependency chain remains an operator obligation.

Output `reflex-replay-bundle-audit-v1` is stdout only, exit **0** for consistent research
bundle, **2** for blocked/refused. Fixed bounded findings never echo SQL, private paths,
credentials or poisoned file text. No .env/ambient Settings are instantiated, no SDK/
training/native foreign model library or broker/HTTP/Telegram/child transport is used.
`-B` suppresses usual Python cache writes; OS read atimes are not application state writes.

The observation always says research_only=true, stage_evidence/trading_authorized/
owner_authenticated/production_model_activated/native_validation_complete/
historical_provenance_verified=false; no auto resume, reset or order. It is not an API
endpoint or owner confirmation, and is NEVER `reflex-stage-v1` input.

## 4. What is recomputed and cross-bound

- Captured input file hashes, full dataset digest and strict manifest constraints;
  aware bar/tick chronology/grid/session availability, source coverage/gap observations.
- All 32 cost-inclusive ordinary metric fields from exact currency-aware trade/equity
  journals, with recorded timezone, cash/credit-adjusted sampled drawdown, costs, open vs
  closed counts and finite/undefined-factor handling. Ordinary metric-engine reuse is
  NOT an independent broker/financial-code implementation or full intratick guarantee.
- In-memory-only deserialization of captured private SQLite, fixed schema2 tables,
  released/paused-or-killed control, no owner approvals/deployment evidence, bounded fixed
  read queries with extension loading denied/read authorizer/query-only/VM/row budget.
- Exact journal vs SQL signal/trade/model identity/columns and checkpoint balances,
  positions and deal legs. Independently recomputed stored trade net/fees/swap sums;
  no invented zero fees or profit from editing both report and trade journal.
- Original immutable proposal hash, pre-entry closed-frame timing, review/context/news
  hashes, archived reply original availability/model/code, synthetic-review fixture scope,
  persisted finalizations versus SQL rows, approved signal/entry/trade/intent references,
  explicit entry/end-close veto counters. No replay-provider relabel or stale time refresh.
- If ML selected: captured artifact/corpus + private snapshot + persisted binding,
  schema/source/code/policy/labels/export/create/availability/selection/embargo/horizon/
  age and fixed gate vector/proposal/threshold/acceptance/proof digests.

Logistic numeric JSON is cross-checked with stdlib math and absolute 1e-12 floating-point
probability tolerance. This is ONLY an observation—not a execution probability or changed
runtime filter. Part13 runtime still performs actual selected-artifact inference at BOTH
approval and shared execution/risk revalidation, with unchanged threshold/finite gates.

LightGBM bounded text and binding/evidence are checked, but its foreign-library inference
is **NOT reexecuted**; the audit explicitly warns/counts that limitation. Training/OOS
reconstruction is not rerun by the auditor for either algorithm. That work belongs to the
runner's Part13 pre-output verifier. Corpus/fold proof declarations are not independent
original fit/availability attestations. This inspection cannot promote a model or fix a
failed one by normalization/substitution/new weights/lowered threshold.

## 5. Snapshot/control and authenticity limits

A valid completed research snapshot can retain simulated OPEN positions (no close-at-end,
or an ordinary close veto). Audit reports retained exposure/warning and changes nothing.
Kill/loss/protection state stays latched/preserved; consistent killed snapshots are not
permission to resume. Unsettled/unknown/economic mismatches cannot be repaired here.

No original SQLite file is passed to sqlite3/SQLAlchemy. Its bounded bytes are deserialized
into :memory: after SHA/closure verification; only the memory COPY's journal-header mode is
normalized when required. No WAL/shm/journal is removed, replayed or checkpointed; all such
source sidecars refuse. Memory copies don't hold a source OS/SQL lock or prove future state.
The filesystem/code owner is trusted: reads aren't pinned-directory-FD hostile-owner defense.

Source timestamps/proof strings/JSON flags/hashes can be forged. News safe/impact decisions,
original full policy and owner selection are not independently rerun/authenticated; the
auditor binds the recorded policy subset, snapshot/reply timing and evidence hashes. It
cannot prove feed/model capture contemporaneity, vendor entitlement/completeness, genuine
Telegram owner authentication, predictive skill/calibration, unbiased all-opportunity labels,
robust out-of-sample economics, native Windows/broker/deployment or actual stage/live consent.

All original backtest → paper → demo → explicit small-live controls remain separate and
unqualified. Do not convert observations into StageGate artifacts, reset capital/history,
apply proposals, activate production models or launch a runtime because exit code is 0.
See `VALIDATION.md` for actual Linux and extracted-package results and remaining obligations.
