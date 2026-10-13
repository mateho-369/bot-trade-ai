# Read-only preflight and readiness

`scripts.preflight` reports bounded observations; it is not runtime startup, broker login, account identity,
trading authorization or stage evidence. The default run reads the local source/manifest and immutable
settings defaults only. It does not read an ambient private `.env`, initialize a database, contact MT5,
open a provider connection, call Telegram or start a listener.

```powershell
.\.venv\Scripts\python.exe -m scripts.preflight --help
.\.venv\Scripts\python.exe -m scripts.preflight
.\.venv\Scripts\python.exe -m scripts.preflight --env-file .env.demo.example
```

Only `.env`, `.env.example` and `.env.demo.example` in the inspected source root may be explicitly
selected. Secret values are not printed. Recognized inherited process overrides are counted and block a
file-only result; use a reviewed clean shell. The `reporter_enabled` observation means optional outbound
report credentials pass bounded format checks; preflight never probes Telegram delivery, and local
reports do not depend on it. Preflight does not rewrite settings, clear stop markers or migrate financial
state.

Optional operations require explicit flags:

- `--probe-writes` creates/removes small temporary permission probes under the configured data/log/backup
  directories; it never opens/initializes the database.
- `--check-ai-provider` makes one bounded read-only provider listing GET; it is not an AI decision call.
- `--check-power-settings` is an informational Windows power query; it never changes settings.
- `--allow-native-import` imports the Windows MT5 SDK only; it does not initialize/connect/login.
- `--inspect-sqlite-snapshot` inspects a bounded in-memory copy of the main SQLite file only. It refuses
  a nonempty WAL/journal and never checkpoints, deletes or rewrites the original. This is not a live lock.

Preflight checks manifest consistency, explicit settings validity, safe mode flags, source/terminal path,
state-path boundaries, imports, dependencies and selected host observations. A green report is not proof
of Windows ACLs, native terminal/account, licensed feeds, provider entitlement, broker order acceptance,
profitability or stage eligibility. `LIVE_TRADING=true` is refused by runtime startup independently.

For the actual prompt-free Windows DEMO start sequence, see `DEMO_QUICKSTART.md` and
`DEPLOYMENT_WINDOWS.md`. Use `python -m scripts.ops` for local controls; no preflight command can resume,
clear a latch or grant trading permission.
