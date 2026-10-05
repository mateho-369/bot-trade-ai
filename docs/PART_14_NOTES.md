# Part 14 — completed research bundle closure and read-only audit

**0.12.0 · schema 2 · cumulative Parts 1–14 · 2026-10-04.**

Safest continuation after frozen historical ML replay: **bounded internal-consistency
inspection**, not production activation, genuine owner authentication, platform validation,
strategy qualification or trading permission. No authentic corpus/model/history/credentials
were supplied. All engineering examples are explicitly artificial.

## Actual implementation

- `backtesting/bundle.py`: the NEW private runner publishes `bundle.json` only after
  successful execution-engine shutdown, market shutdown and private DB close. Exact
  complete allowlisted file closure, input/output/checkpoint/model/SQLite raw SHA/lengths;
  immutable same-volume fsynced publication, no replacing an existing bundle.
- `backtesting/audit/contracts.py`: exact redacted formats, decimal/UTC/hash validation,
  strict bounded JSON/JSONL and fixed research-only observations.
- `backtesting/audit/files.py`: complete portable-case-aware file scan; link/reparse/
  hardlink/nonregular/FIFO refusal, no missing-path creation or permission changes.
- `backtesting/audit/integrity.py`: strict completed research manifest, optional externally
  trusted literal digest, every raw SHA/length, no unlisted/missing/sidecar/failure files;
  immutable captured bytes, detached observations and second unchanged check after audit.
- `backtesting/audit/inputs.py`: original manifest/schema/raw input/dataset identity,
  declared bar/tick availability/grid/session/order constraints, coverage and gap recompute.
  No downloader, original input-path reload or native feed call.
- `backtesting/audit/ledger.py`: SQLite byte image deserialized in PRIVATE MEMORY only,
  extension loading disabled, query-only/read authorizer and bounded VM/row queries;
  fixed schema/control/signal/trade/intent/deal/model observations. No original SQL connection.
- `backtesting/audit/semantics.py`: recomputes all 32 ordinary metrics from exact trade/equity
  journals; checks copied SQL rows and checkpoint positions/deals/balance; independently
  aggregates exact stored trade-leg P&L, fees and swap. No fills, order replays or resets.
- `backtesting/audit/signals.py`: recomputes proposal/review/context digests; temporal
  closed-frame/archive-review/news-hash binding, published decisions versus private rows,
  approved signal/entry/trade/intent references and exact veto-count journal consistency.
- `backtesting/audit/model.py`: corpus/source/code/policy/schema/selection/label/horizon/
  private model proof consistency; fixed snapshot/gate vector and threshold binding;
  numeric logistic probabilities cross-checked with stdlib math (absolute tolerance 1e-12).
  No fitting/optimizer/registry activation or numpy/sklearn/LightGBM/SDK import by auditor.
- `backtesting/audit/runner.py`, `scripts.audit_backtest`, `scripts.smoke_bundle_audit`,
  five new pytest modules and explicit artificial bundle helpers.
- `backtesting/backtester.py`: adds a nonsecret audit-policy subset, seal-worker lifecycle
  and records end-close failures explicitly. Original signal/ML/owner/risk/protection/
  uncertainty/idempotency/stage checks are retained, not bypassed to make audits pass.

Active read-only release/readiness defaults now point to `RELEASE_14_MANIFEST.json`.
Old guides/manifests/archives stay historical byte snapshots. Schema 2 is retained;
production capital/model/stops/DB are not opened/migrated/checkpointed/reset by the auditor.

## Executable commands

```powershell
# New disposable ARTIFICIAL research input/output only; no credential/native startup.
.\.venv\Scripts\python.exe -m scripts.smoke_bundle_audit
.\.venv\Scripts\python.exe -m scripts.make_backtest_fixture --output data/fixtures/ARTIFICIAL_audit_001 --replay-minutes 1
.\.venv\Scripts\python.exe -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_audit_001/manifest.json --output data/backtests/ARTIFICIAL_audit_001 --review-mode synthetic_research --simulate-orders --close-at-end
# Auditor reads the explicit completed PRIVATE output; -B suppresses normal .pyc cache writes.
.\.venv\Scripts\python.exe -B -m scripts.audit_backtest --run data/backtests/ARTIFICIAL_audit_001
```

Use active-environment `python` on Linux. Optional `--trusted-bundle-sha256` accepts an
external lowercase SHA-256 of bundle.json. Copying the current local hash is NOT an
independent authentic producer signature. No `.env`/inherited Settings are loaded by
the auditor; it never starts runtime/Telegram/providers/broker/children. Default output
is stdout only; no automatic repair, re-seal, resumable signal or StageGate converter.

See `REPLAY_AUDIT.md` for contracts/limits and operator interpretation; `BACKTESTING.md`
and `ML_REPLAY.md` for unchanged replay/ML causal limitations. Full literal current
architecture/tree/config/source/frontend/Windows scripts/tests is `PART_14.md`.

## Bounds and deliberate limits

Closure: <=256 files / 4,096 scan nodes / 128 MiB total; ordinary file <=32 MiB,
private SQLite <=64 MiB; manifest <=1 MiB, metadata JSON <=4 MiB (model/corpus/checkpoint
explicit role bounds), JSONL line <=32 KiB, general journal <=200,000 rows and equity
<=500,002. Existing market input <=500,000 rows; original model corpus <=5,000 rows/
16 MiB. Fixed UTC/decimal strings, finite numeric scores, bounded SQL and strict JSON.
Use smaller NEW date ranges if a run exceeds bundle bounds; never reuse/reset history.

Old pre-Part14 outputs without closure are refused, not retroactively blessed/resealed.
A sealing/shutdown failure leaves original research artifacts + `failure.json` and no
valid audit completion. No WAL/journal/shm is ignored, deleted or checkpointed by inspection;
normal graceful owner runtime/backup handling remains separate. Snapshot reads do not
hold an original OS/SQL lock or prove the source remains unchanged after inspection.

For LightGBM, corpus/model/selection/vector/evidence/threshold and bounded native text
are inspected, but foreign-library inference is explicitly **NOT reexecuted** by this
auditor. Training/OOS reconstruction is NOT reexecuted for either algorithm; it belongs
to the Part13 runner's pre-output verification. Logistic audit is numeric consistency,
not a new probability passed to execution. No model/probability/report edit is permission.

Ordinary metric engine reuse is recomputation from separate journals, not an independent
financial/audit-code implementation or broker fee/fill attestation. Core trade-leg sums
are independently checked against captured SQL deals. News impact/filter decisions and
original full settings are not independently replayed/authenticated: the auditor binds
captured snapshot/review/times/hash and a declared audit-policy subset. No calibration,
actual historical availability, source/entitlement, independent strategy skill or unbiased
opportunity coverage is claimed. Local admins changing code/data/manifest together remain
outside the trusted filesystem/code model; file checks are not pinned-FD race-free defense.

## Executed state

Full current working source: **2528 passed in 674.86s**, **201 new tests** since Part13;
lint/format/compile/pip/exact freeze/eleven smokes/JS/news/isolated CLI passed. Complete
independent extraction also passed **2528 tests in 679.51s** and the full same pipeline.
Results are recorded in `VALIDATION.md` / `RELEASE_14_VALIDATION.json`. Extraction reused
tested Linux dependencies, not a native Windows installation/qualification; final docs
rebuild preserves the tested runnable bytes.
The archived-news check uses the actual historical unmanaged evidence contract, never
relabels it native managed provider evidence to bypass a guard.

The new smoke has **14 fixture-only checks** including actual portable logistic audit,
metrics/memory SQL, unchanged bytes/mtime and rehashed-profit refusal. It creates ONLY
new disposable fixtures. Audit operations make zero application/production state writes,
broker/provider/Telegram requests, children or real orders. No actual owner/provenance/
stage/native/live qualification. Original defaults remain paper/mock/paused/demo=true/
live=false; target six/day never forces entries, max twelve and all loss/DD/protection
limits remain. No martingale, averaging, doubling or automatic risk escalation.
