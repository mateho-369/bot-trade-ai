# Part 12 — offline readiness and release integrity

**0.10.0 · schema 2 · cumulative Parts 1–12 · 2026-10-04.**

Parts 1–11 completed the original implementation sequence. This continuation uses
the safest professional default: additional **read-only diagnostics**, not broker
connection, deployment or trading permission. Ordinary source is runnable; the full
literal source/configuration/frontend/deployment/tests guide is `PART_12.md`.

## New modules and entry points

- `readiness/contracts.py`: bounded fixed-message findings and observation documents.
- `readiness/files.py`: portable paths, link/reparse/hardlink rejection, bounded stable
  raw reads and strict duplicate/nonfinite/depth-bounded JSON. No fetch/chmod/creation.
- `readiness/integrity.py`: current manifest structure/counts/portable collision checks,
  every raw SHA/length, source closure including .pyw/.pyi and frontend/Windows scripts,
  optional externally trusted manifest digest, POSIX executable write-bit warnings/gates.
- `readiness/dependencies.py`: exact direct pins vs DISTRIBUTION metadata, without
  importing MetaTrader5/providers or invoking pip/subprocess/network.
- `readiness/settings_inspection.py`: explicit file/defaults-only existing Settings
  validation and alias/JSON decoding; no inherited secret values or implicit .env;
  duplicate/error/interpolation refusal; actual runtime override detection without
  name/value echo; private POSIX .env mode and state path scope checks.
- `readiness/host.py`: local OS/interpreter/free-space and optional Windows terminal
  FILE metadata. No native DLL/SDK load, executable launch, account/session/TLS probe.
- `readiness/sqlite_snapshot.py`: bounded stable main-file read, reject pending WAL/
  journal, query a private IN-MEMORY COPY with no original SQLite connection. Fixed
  query-only/authorizer/budget; schema version/table/control/exposure observations.
- `readiness/runner.py`: integrity FIRST, exact inspector source-root binding, then
  sequential observations; stdout only and never a green native qualification claim.
- `scripts.verify_release`, `scripts.readiness`, `scripts.smoke_readiness`.
- `tests/test_readiness_*.py` + explicit miniature-source/SQL fixture helpers.

`source_code_hash` now binds the new readiness source like the other ordinary modules.
No production risk/owner/AI/news/StageGate/financial-state behavior was weakened.
Changing this code changes old code-bound approvals/evidence; schema 2 did not change.

## Executed results

Full working suite: **2143 passed in 306.20 s**, **173 new tests** since Part 11.
Ruff lint/format (311 inputs at working validation), compile, pip consistency/exact
Linux freeze, nine offline smokes, JS syntax, source inspection and isolated operator
CLI passed. The new engineering smoke passed **14 checks** against disposable invented
source/SQL. It creates its temporary fixtures; the verifier/readiness calls do not
create/change production/application financial state. No SDK/provider/Telegram/process
transport, real order, live authorization, genuine stage or native deployment occurred.

The authoritative complete extraction passed **2143 tests in 292.17 s** and all
lint/format/compile/lock/smoke/JS/news/CLI checks. Full literal-source/manifest parity
is documented in VALIDATION.md and RELEASE_12_VALIDATION.json. The reused Linux dependencies are not Windows/native
installation or actual platform validation.

## Safe command preview

```powershell
# From the private extracted source root. -B suppresses normal Python .pyc cache writes.
.\.venv\Scripts\python.exe -B -m scripts.verify_release
.\.venv\Scripts\python.exe -B -m scripts.readiness --env-file .env.example
# An explicit native-target diagnostic WILL retain unverified native/session/ACL gates.
.\.venv\Scripts\python.exe -B -m scripts.readiness --profile windows_native --env-file .env
```

`offline_checks_passed` is not "safe to trade". No owner is authenticated, no native
feed/account/contract/news/calendar/AI/TLS/session/ACL is qualified, no model/stage is
selected/imported and no control is resumed. The native profile intentionally remains
blocked while required external/native facts are unverified. See READINESS.md.

## Important distinctions

- Hash integrity is not a signature. An attacker can change BOTH source and manifest;
  use an independently trusted archive/hash, private interpreter/dependencies and source
  review. Optional --trusted-manifest-sha256 supplies a literal external anchor, not
  a self-authenticating local checksum. Diagnostic documents are NEVER StageGate input.
- File-only config does not apply settings or suppress production risk/model policy.
  The actual daemon retains ordinary environment precedence. Recognized inherited
  overrides block this diagnostic to prevent a misleading file-only comparison.
- Memory SQL does not hold original OS/SQL locks or prove a runtime remains stopped.
  A pending WAL/journal is **not deleted, checkpointed, replayed or ignored**. Coherent
  stopped backup/recovery and ordinary live reconciliation remain separate obligations.
- Original .env/DB/data/log/model/checkpoint/credentials remain excluded from releases.
  Source filenames containing initdata (e.g. the real auth test) are NOT bearer files.
- Windows EXE existence is not signature/entitlement/account/interactive session proof.
  No task is registered, process launched, terminal logged in, ACL changed or watchdog
  started by these tools. Existing logon-only Task Scheduler/VBS instructions remain
  actual-operator steps, unexecuted here.
