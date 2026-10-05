# Read-only verification and offline readiness — exact usage

**0.12.0 / schema 2. No trading/deployment permission or genuine native qualification.**

## 1. First establish the delivery source

Obtain the project ZIP and its SHA through a trusted channel. SHA-256 detects changed
bytes only if your expected digest is independently trusted; a checksum next to a
substituted archive is not authentication. There is no signed release certificate here.

```powershell
Get-FileHash .\MT5_AI_ReflexBot_Parts_01_12.zip -Algorithm SHA256
# Compare the ENTIRE result with the trusted delivery digest before extraction.
Set-Location C:\Trading\mt5_ai_reflex_bot
.\.venv\Scripts\python.exe -B -m scripts.verify_release
```

The verifier is standard-library-only. It does not load configuration, `.env`, SQLAlchemy,
Pydantic, MetaTrader5, a provider, bot, API, runtime or daemon. It reads the **CURRENT**
`docs/RELEASE_14_MANIFEST.json`, not a stale historical manifest. Old guides/manifests are
historical snapshots and do not bind today's modified cumulative source.

For an independently recorded manifest digest:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.verify_release --trusted-manifest-sha256 'YOUR_INDEPENDENTLY_TRUSTED_64_LOWERCASE_HEX_DIGEST'
```

The placeholder is operator input, not supplied evidence. Get the actual manifest
from an independently validated ZIP, record its SHA outside the installation, and use
that literal anchor. Computing a digest from the same untrusted installation does NOT
establish its authenticity. Compromised verifier/interpreter/installed dependencies
can also lie; least privilege, private sources, trusted installation and review matter.

## 2. What verification actually checks

- Manifest ≤2 MiB, ≤1500 entries, every declared raw file ≤16 MiB, total ≤96 MiB.
- Aware release identity/counters: supported schema 2; exact declared/packaged/Python
  counts; manifest self-hash exclusion; complete relative portable file-name records.
- Exact SHA-256 lowercase hex and actual integer lengths (booleans not integers).
- No duplicate JSON keys, nonfinite numbers/overflow, BOM, invalid UTF-8, deep JSON,
  absolute/drive/backslashed/traversing/control/Windows-reserved/colliding paths.
- No symlinks, hardlinks or NTFS reparse/junction components. Stable regular-file
  identity/size/mtime/ctime before/after bounded reads and a final manifest re-read.
- Bounded scan ≤20,000 directory/file nodes. Complete runnable/config closure, including
  Python .py/.pyw/.pyi, PS1/VBS and JS/HTML/CSS; unknown root sitecustomize.py is not
  allowed to hide behind preserved original SHA values.
- POSIX source files and source directories cannot be group/world writable. NTFS ACL
  privacy/write provenance requires a separate actual-Windows review; POSIX bits are
  never claimed as Windows authorization.
- Runtime data and dependency/generated/cache trees are excluded from source closure.
  Their exclusion is not a package/plugin/supply-chain audit. Legitimate auth source
  `tests/test_telegram_initdata.py` is included even though its name contains initdata.
- No secret .env, DB/log/backup/PEM/key/bearer may appear as a declared release input.

No file is created, deleted, chmodded, repaired or uploaded. Output uses fixed bounded
messages and aggregate counts, not arbitrary original error/config/SQL/private paths.
The verifier does not install/audit/import distributions or contact an entitlement server.
Reads can cause OS-managed access-time updates; the `-B` flag avoids normal Python
bytecode cache creation. Zero application-state writes is not a zero-storage-I/O claim.

Exit **0** = content comparison/closure passed (possibly unanchored warning); exit **2**
= refused/changed/incomplete. No configuration or trade authorization follows from 0.
If checks fail, preserve financial state, stop normally if applicable, investigate and
re-extract trusted source into a separate installation. Do not delete losses/latches,
intents, model/checkpoint, uncertain orders or a WAL to make a check green.

## 3. Development profile — no inherited credentials

From THIS inspected installation (root must equal the inspector's own source root):

```powershell
.\.venv\Scripts\python.exe -B -m scripts.readiness
.\.venv\Scripts\python.exe -B -m scripts.readiness --env-file .env.example
.\.venv\Scripts\python.exe -B -m scripts.readiness --env-file .env
```

Without --env-file: immutable defaults only, not the repository .env. With the flag:
only literal source-root `.env` or `.env.example` is accepted. Existing Settings aliases,
JSON field decoding and cross-field risk/owner/timeout/mode validators are reused.
The diagnostic ignores inherited process values and secret-directory sources; it NEVER
passes secrets to a transport or prints their values. An explicitly selected .env may
be read locally to validate secret pair/triplet shape; that is not owner authentication.

Actual daemon Settings retain normal environment precedence. Recognized inherited
variables trigger `runtime_environment_overrides_present` and block file-only diagnostic
results. Review a clean shell; do not assume a private dotenv overrides an old LIVE_TRADING
or DATABASE_URL process variable. Only an aggregate count is printed, not private names.

Dotenv grammar is real python-dotenv parsing, not shell execution. Duplicate case-folded
keys, invalid syntax, value-less keys, non-UTF-8/BOM/NUL, >64 KiB, >300 keys and `${...}`
interpolation are refused. Literal dollar text is not expanded; use reviewed literal
source-root configuration, not environment/shell interpolation. Unknown settings fail
normal Settings validation. Root redirects and linked/out-of-root state paths fail.

Direct `requirements.txt` pins are compared with distribution metadata only (MetaTrader5
required for windows_native). Missing/different metadata blocks. Exact pins are not a
transitive vulnerability/consistency audit or proof a native wheel imports. No pip or
subprocess is invoked. Use the existing actual-platform lock/pip/security procedures.

Development observations include OS/interpreter architecture and a read-only 512 MiB
free-space floor. No write-permission/disk-health/clock-network/session probe occurs.
No DB is opened/created by default. Missing credentials or genuine sources are not
invented; the default mock/paper/paused target remains development-only.

Exit 0 is `offline_checks_passed`, NOT production/native readiness. Exit 2 is `blocked`.
Every `reflex-readiness-v1` output says native_validation_complete=false,
owner_authenticated=false, stage_evidence=false, trading_authorized=false and actual
broker/provider/Telegram/process/order operations 0. No JSON diagnostic can qualify a stage.

## 4. Optional stopped SQLite memory snapshot

Only if explicitly requested, using the configured persistent local SQLite path:

```powershell
# First perform ordinary graceful STOP/drain/reconciliation on the actual deployment.
# Then diagnostic observes a bounded COPY, NOT an authoritative lock or live account.
.\.venv\Scripts\python.exe -B -m scripts.readiness --env-file .env --inspect-sqlite-snapshot
```

The original DB is NEVER opened with sqlite3/SQLAlchemy; no original PRAGMA, sidecar
creation, checkpoint, migration, transaction, risk/baseline/owner mutation or reset.
Nonempty -wal or -journal ⇒ `sqlite_pending_journal_or_wal`. Keep them with their original
DB, use the normal runtime/backup procedure and investigate; do NOT delete/copy around
uncertain financial state. Empty sidecars/SHM are not touched. PostgreSQL, :memory: URL,
SQLite query/URI overrides, linked/out-of-root files are refused, not silently replaced.

A stable main file ≤64 MiB is deserialized into `:memory:`. Only the COPY's journal mode
header is adjusted for SQLite's WAL-image-deserialization limitation; original bytes
are checked unchanged. SQL query_only, extension loading disabled, fixed read authorizer,
≤2 million VDBE-operation budget; no user-supplied SQL. It observes required table names,
schema_version singleton 2, stored paused/running/kill/session **flags only**, counts of
open/unknown trades and unsettled original intents. It does not print account/session
IDs, financial amounts, owner bearers, audit details or arbitrary database contents.

The test is not a full migration/schema/ledger/ownership/model proof. Runtime session,
running state or unresolved exposure/intents block. A kill latch remains latched and
is explicitly reported, never cleared. A stopped/flat snapshot is not a live OS/SQL
lease: it can become stale immediately. Actual runtime still verifies/reconciles all
financial/model/owner/source gates. Memory deserialize/extension API support varies by
actual Python/SQLite build; unavailable API fails closed, never opens a fallback DB.

## 5. Windows-native target — intentionally retained unknown gates

```powershell
.\.venv\Scripts\python.exe -B -m scripts.readiness --profile windows_native --env-file .env --inspect-sqlite-snapshot
```

The profile requires actual Windows x64, private explicit .env, native-source config,
owner controls and direct distribution metadata. Where supported, it observes the
configured unlinked regular .exe terminal FILE only, default
`C:/Program Files/MetaTrader 5/terminal64.exe`. It does not hash/authenticate/execute it,
load a Windows DLL/SDK, log into MT5, call account_info, retrieve ticks, open a port,
register tasks, change credentials/ACLs or start watchdog.

**Required unknown checks remain blocked:** actual logged-on limited user/non-Session-0,
NTFS privacy/source write provenance, executable/vendor identity, account/server/kind,
symbol suffixes/contracts/currency/fees, broker behavior/soak/reconnect/drain, entitled
AI/news/complete calendar, genuine Telegram/Mini App/HMAC/TLS, vulnerability audit,
PostgreSQL/restore/EXE platform testing. Environment SESSIONNAME or fake SQL native tags
cannot make those facts true. Read DEPLOYMENT_WINDOWS.md + NATIVE_VALIDATION_CHECKLIST.md.

Even perfectly passing offline observations do not establish these facts. Actual owner
credentials, genuine data and explicit deployment/trading authorization were not supplied
in this build. Do not change backend/live flags to obtain green diagnostics. Backtest →
authentic paper → actual broker demo → separately explicitly approved small live remains
mandatory; every research report and fixture is ineligible.

## 6. Engineering smoke

```powershell
.\.venv\Scripts\python.exe -B -m scripts.smoke_readiness
```

Unlike the read-only diagnostic, the smoke explicitly creates and later discards its
TEMPORARY invented source/empty SQL/control/WAL fixtures. Fourteen checks cover current
hash/closure, exact source file inclusion, ignored credentials, memory schema/kill latch,
unchanged state, WAL refusal, bad digest, different-inspector-root refusal, changed/unlisted
source and no SDK import. It does not touch production financial state, connect a broker,
request providers/Telegram, spawn a child or qualify a genuine release/owner/stage.
