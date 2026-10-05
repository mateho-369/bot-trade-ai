# Windows PC / interactive VPS deployment — Part 10

Operator instructions only. **No deployment, connection, task registration, EXE
compilation, real trading or live authorization has been performed here.**
Historical backtester/stage reports are Part 11; do not promote early. Current
runtime can run mock/paper PAUSED for operator diagnostics. No profit guarantee.

## 1. Private installation and baseline checks

Use a patched **Windows x64** PC/VPS and a dedicated ordinary interactive user.
Restrict RDP via VPN/IP allowlist; enable account protection/2FA where available.
Do not deploy as SYSTEM, a Windows service, Session 0, S4U or a stored-password task.
RDP disconnect usually preserves the logged-on session; logoff does not. Verify
provider session policy. Locking the screen is preferable to signing out.

Extract the complete project to `C:\Trading\mt5_ai_reflex_bot` (local NTFS disk).
Do not run from a public/shared/synced drive. Grant only the dedicated owner and
necessary local administrators/SYSTEM access to `.env`, `data`, backups and logs;
remove inherited broad Users/Everyone permissions using reviewed NTFS ACLs. Unix
0600 flags are NOT sufficient proof of Windows ACL privacy. Keep source/package
write permissions private to prevent executable/DLL/source substitution.

Install Python **3.11 x64** and the broker's official MT5 terminal. This Linux
validation did not exercise Windows wheels/native MetaTrader5. Before installing,
review requirements/pins and conduct a deployment vulnerability/dependency audit.
Do not use an arbitrary remote installer or pip index with secrets in commands.

```powershell
Set-Location C:\Trading\mt5_ai_reflex_bot
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env     # FIRST installation ONLY, never overwrite secrets/history
notepad .env                   # local private review; do not paste passwords into chat/logs
.\.venv\Scripts\python.exe main.py --env-file .env check-config
# Only on a NEW, EMPTY, dedicated database:
.\.venv\Scripts\python.exe main.py --env-file .env init-db
.\.venv\Scripts\python.exe main.py --env-file .env status
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.smoke_runtime
```

All smoke diagnostics are fixtures, not broker/market/promotional validation.
Never delete an existing DB/checkpoint or initialize fresh capital to bypass a
failed gate. Preserve schema 2 and original history; runtime never migrates it.

Default `.env`: `DEMO_MODE=true`, `LIVE_TRADING=false`, `PAPER_TRADING=true`,
`MT5_BACKEND=mock`, `START_PAUSED=true`, empty broker/Telegram/provider credentials.
Keep those flags for first diagnostics. `.env` must be in the **deployed source
root**, even when launching a compiled executable from elsewhere.

## 2. Foreground diagnosis, no automatic entry resume

```powershell
# Runtime ONLY: no external supervisor. Ctrl+C requests graceful stop.
.\.venv\Scripts\python.exe -m app.bot --env-file .env
# Another terminal, READ-only local process/health evidence:
.\.venv\Scripts\python.exe -m scripts.runtime_status --env-file .env
```

The daemon restores/reconciles and starts PAUSED. Default mock has synthetic data,
not broker evidence. Empty owner credentials leave the owner interface locked;
API read routes are authenticated, no developer/browser login fallback.
`/healthz` is public basic interface health, **not broker health or permission**.
Private local `data/runtime/health.json` is diagnostic, not live authorization.

Do not run API-only launcher and the composed API on the same port. The runtime
already owns one authenticated API worker. No `--reload`, multiple workers,
application startup migration, concurrent bot copies or ad-hoc order scripts.

## 3. Genuine market PAPER, then gated DEMO; not authorized by these instructions

After native Windows/account/symbol/currency/fee/clock checks, a reviewed
`MT5_BACKEND=real` with `PAPER_TRADING=true` uses **native read-only market data +
simulated writes only**. Log into the verified intended terminal account, confirm
its server/type and exact symbol suffixes; configure aliases, tick/lot rules,
commission/swap and account-to-USD conversion. Do not use synthetic catalogue
specifications as broker contracts. Require source coverage/entitlements and a
complete economic calendar; missing news/AI/quality evidence rejects entries.

Default terminal path is `C:/Program Files/MetaTrader 5/terminal64.exe`. Keep a
simple literal quoted/unquoted dotenv value; VBS does not implement arbitrary
shell expansion or inline-comment parsing. No broker passwords are passed on
terminal command lines. Credentials, if explicitly configured later, remain `.env`.

The minimized launcher below opens MT5 only if `MT5_BACKEND=real`; mock mode does
not open a terminal. Foreground Python may initialize the terminal itself, so use
this explicit launcher first if you need a minimized native terminal:

```powershell
wscript.exe //B //Nologo .\scripts\run_mt5_background.vbs
```

Paper entries still need explicit owner resume and every signal/AI/news/risk gate.
When later using broker demo, verify Algo Trading and that external Python trading
is not disabled. Demo/live writes additionally need genuine matching backtest →
paper → demo → owner-approved small-live evidence; synthetic/test SDK reports
cannot qualify. Default live risk cap is 0.1%, not automatic live permission.
Do not change live flags or create approvals as part of first deployment.

## 4. Telegram owner transport and authenticated Mini App

Configure the real owner ID/bot token privately, exact HTTPS Mini App URL/host,
trusted proxy peer IPs and polling OR authenticated secret-header webhook. Tokens
never appear in URLs, frontend files, logs, git, CLI strings or backups. Register
owner command/menu updates only when deliberately setting `RUNTIME_REGISTER_MENU=true`.
Blank token means no Telegram network transport. Never delete an existing webhook
or drop pending updates blindly; polling refuses an existing webhook.

Use a controlled TLS proxy on the SAME host to the loopback API. No inbound access
to raw 8000 or SQL ports; no trusting client-supplied forwarded IP/host. Configure
`API_TRUSTED_HOSTS_JSON` with the exact public host, `API_TRUSTED_PROXY_IPS_JSON`
with actual exact proxy socket-peer IPs and `TELEGRAM_MINIAPP_URL` with HTTPS.
The proxy must overwrite `X-Forwarded-Proto`; uvicorn proxy_headers is FALSE.

Example Caddy site block (requires your real DNS/domain and certificate setup;
not deployed/tested here):

```text
owner.example.com {
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-Proto https
    }
}
```

Review edge request/body/concurrency limits, request logging privacy, TLS/host
policy and forwarded-header replacement on the actual proxy. Native backend port
must stay private. Do not use wildcard trusted hosts/origins/proxy peers.

Test forged/expired/repeated-key initData, non-owner/private-chat denial, token
expiry, rate limits, single-use confirmation, paused state and close scope on
fixtures BEFORE any genuine deployment/trading. Browser-only static HTML is not
an authenticated owner panel. Owner approval is never proposal application/model
selection/live permission. Details: `PART_09_NOTES.md`.

## 5. Supervised foreground, then minimized/hidden logon task

```powershell
# Reviewed explicit supervisor; default 30-second checks; always PAUSED restart.
.\.venv\Scripts\python.exe watchdog.py --env-file .env
# Hidden supervisor + minimized native terminal when REAL backend is configured.
wscript.exe //B //Nologo .\scripts\run_bot_hidden.vbs
# Review task without registration first:
.\scripts\task_scheduler_setup.ps1 -WhatIf
# Explicit registration by the logged-on owner:
.\scripts\task_scheduler_setup.ps1
Get-ScheduledTask -TaskName 'MT5 AI ReflexBot' | Select-Object TaskName, State
```

The VBS waits for the supervisor so Task Scheduler sees a running instance.
Task: current-user **AtLogOn**, **Interactive**, **Limited**, IgnoreNew instances,
no execution deadline or Task Scheduler restart loop. Default terminal path is
configurable; Python/pythonw comes from this private virtualenv. Do not select
“Run whether user is logged on or not.” No passwords are stored in the task.

Source `.env`/code changes require explicit stop, review and supervisor restart.
No auto-resume on reconnect/reboot/restart. Watchdog launches at most 3/hour
including first start and failed spawns, and never force-kills an alive child.
A lost broker acknowledgement is not evidence no order exists.

Keep AC power/internet, clock synchronization, disk health and a supported logged-
on session. Disable unattended sleep/hibernation if appropriate; do not disable
Windows lock/security. Plan updates/reboots away from exposure. Uptime, gap-safe
fills, profit locks and maximum realized losses are not guaranteed.

## 6. Stop, backups and restart — preserve all uncertainty/latches

```powershell
.\.venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env
.\.venv\Scripts\python.exe -m scripts.runtime_status --env-file .env
# After confirmed clean stop and released OS/SQL session:
.\.venv\Scripts\python.exe -m scripts.backup_db --env-file .env
# Optional ONLINE DB-only snapshot; not a paper recovery bundle:
.\.venv\Scripts\python.exe -m scripts.backup_db --env-file .env --db-only
# Clear only local stop marker, after review; NOT kill/drawdown/recovery/live state:
.\.venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env --clear-request
.\.venv\Scripts\python.exe watchdog.py --env-file .env
# Remove logon registration only, not a substitute for graceful runtime stop:
.\scripts\task_scheduler_setup.ps1 -Remove -WhatIf
.\scripts\task_scheduler_setup.ps1 -Remove
```

Local stop marker persists across logon/reboot; launchers do not silently clear it.
Uncertain or still-running native futures retain DB/OS lock until actual completion.
Do NOT force-stop a task, delete lock files, kill by a PID-file number, reconnect to
reset quarantine or resend a “missing” order. Inspect real broker open positions,
orders and deals plus durable intent records; owner-only recovery/flat-baseline
review is separate from resume. No remote reset or live-approval route was added.

Coherent recovery needs the matching stopped SQLite DB + paper checkpoint/models/
news/candles; online DB alone is insufficient. Backups include private trading
history, not `.env`/logs/keys, and are NOT signed/evidence/live permission. Use
private encrypted/off-host storage under an explicit retention/recovery plan.
No automatic restore helper; verify manifests/CRC, preserve originals, reconcile
actual broker outcomes and start PAUSED. PostgreSQL is optional/unvalidated here:
use reviewed pg_dump + stopped artifacts; disable SQLite scheduler backup and
validate a PostgreSQL plan independently. Never pass DB passwords in CLI URLs.

## 7. Optional EXE packaging — source remains deployed

Only on a validated Windows environment; optional dependency installation is a
separate reviewed action. Packaging itself does not start/deploy/enable trading.

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3
.\scripts\compile_exe.ps1
# After fresh mock/paper validation, explicit manual supervisor start:
.\package\ReflexBot\ReflexBot.exe --env-file C:\Trading\mt5_ai_reflex_bot\.env
```

Onedir package includes runtime/static assets and reviewed dependencies. Keep the
full ordinary source project at the `.env` parent; EXE is not a self-contained
credential store or permission artifact. Preserve output SHA256 and private
package write permissions. Validate runtime, Mini App, SDK, CPU models and native
broker gates on your target Windows installation. This environment did NOT run
PyInstaller, native Windows wheel, packaging or EXE tests.

Frozen code identity includes the executable digest: **source-only stage evidence
cannot be reused as compiled evidence**. Recompilation/dependency/source changes
need fresh scope validation; never rebind old history to the new fingerprint.
Compiled entry launches its same executable with an explicit child flag; no
shell-based Python command or untrusted PID adoption. VBS/task defaults remain
source/pythonw mode unless you deliberately review a compiled deployment variant.

## Troubleshooting

- Locked UI / no bot: check real configured owner/token and TLS/private Telegram
  launch; never substitute a developer password or bypass initData validation.
- Resume challenge stale: heartbeat/reconciliation changed revision; prepare and
  promptly confirm a new challenge. Do not weaken the locked revision fence.
- Unknown news/low confidence/spread/limit: correct source/quality conditions,
  never force the 6-trade target or lift risk caps to produce activity.
- Missing checkpoint with old DB: STOP, preserve evidence and reconcile matching
  backup; do not create new paper capital or delete prior risk/trade rows.
- Stale/overdue/alive child: inspect terminal, disk, broker outcomes and private
  logs. Replacement is deliberately withheld; SDK timeout may still mean execution.
- Native Session 0/logoff: restore a valid interactive owner session; never change
  Task Scheduler to SYSTEM/service operation to work around the failure.
- Last_error/kill/loss/unsettled gate: explicit authenticated recovery/progression
  is required. Restart/stop-marker clearing/proposal approval are not reset tools.

## Part 12 — read-only preparation, not native/deployment authorization

Before any explicit native initialization/daemon/task command, review READINESS.md
and NATIVE_VALIDATION_CHECKLIST.md. From the extracted current source root:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.verify_release
.\.venv\Scripts\python.exe -B -m scripts.readiness --env-file .env.example
```

These new diagnostics do NOT initialize MT5/SQL original state, send Telegram/provider
requests, register tasks, start watchdog or grant resume/live. They are separate from
the existing explicitly invoked `scripts.check_mt5_readonly`, which DOES connect native
MT5 on Windows after actual operator authorization. A source manifest match is not a
signature; a green development profile is not real Windows/SDK/ACL/TLS/feed qualification.
Windows-native profile keeps required external/session/ACL/source facts UNVERIFIED/BLOCKED.
A pending SQLite WAL/journal is refused and preserved, not deleted/checkpointed/reset.


## Part 13 offline research ML (not installation or native permission)

From the extracted root, `.\.venv\Scripts\python.exe -m scripts.smoke_replay_model`
creates only disposable ARTIFICIAL model/corpus/history/private-ledger fixtures. It is
not actual Windows/NTFS/SDK/broker/owner/strategy validation. `ML_REPLAY.md` gives new
explicit research paths and env; never use production credentials or reuse an output
ledger. Original logon-only/limited-user/30-second-watchdog requirements and all native
validation obligations remain unchanged. No task/service/terminal is installed or
started, no production model activated and no actual deployment authorized by Part 13.


## Part 14 research inspection is not deployment permission

From the extracted root, `.\.venv\Scripts\python.exe -m scripts.smoke_bundle_audit`
creates only disposable ARTIFICIAL research fixtures. `.\.venv\Scripts\python.exe -B
-m scripts.audit_backtest --run <completed-private-research-directory>` performs bounded
read-only file/memory-SQL consistency inspection, no original SQLite connection or
terminal/task/runtime start. Full command is on one line in REPLAY_AUDIT.md. This Linux-
validated source is NOT actual NTFS/session/SDK/owner/broker/deployment validation.
Original limited-user/logon-only/30-second-watchdog requirements and all native obligations
remain. No model/trading authorization or task/service deployment is supplied here.
