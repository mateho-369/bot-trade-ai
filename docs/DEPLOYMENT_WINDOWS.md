# Windows DEMO deployment

## Scope and host model

The supported native target is an interactive Windows 10/11 x64 user session running 64-bit CPython
3.11–3.14 and the official MetaTrader 5 terminal. `install.bat` and `setup_demo.ps1` select the newest
installed version in that range. A Windows service/session-0 install is not supported. This source
refuses `LIVE_TRADING=true`. `.env.demo.example` opts into `AUTONOMOUS_DEMO=true`, requires
`AI_REQUIRE_APPROVAL=true`, leaves `START_PAUSED=true` and `DEMO_FAST_TRACK=false`, and selects native
MT5 with `PAPER_TRADING=false`. The MT5 terminal's reported account kind must be DEMO.

## First setup

1. Install 64-bit CPython 3.11–3.14 and MT5. In the terminal, sign in to the intended DEMO account and
   keep the terminal open. Verify the account and server yourself.
2. Open PowerShell in the repository root and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_demo.ps1
   ```
3. The first pass creates `.venv`, installs `requirements.txt`, copies `.env.demo.example` to `.env`, and
   exits without prompting or starting the runtime. Edit `.env`; set the AI provider key and check the
   terminal executable path. Keep all safety flags unchanged. Telegram report token/chat ID are optional.
4. Run the same setup command a second time. It checks settings, preserves an existing database, resolves
   symbols, performs the read-only MT5 account check, probes news, runs native-profile preflight and starts
   the watchdog minimized. It refuses REAL, CONTEST or unknown MT5 account kinds.
5. For a later foreground start, use `start_demo.bat` in the repository root. It requires the existing
   `.venv` and reviewed `.env`, refuses a persistent stop marker, and starts the supervised watchdog
   without prompting or reinstalling dependencies.

`setup_demo.ps1` and both `.bat` launchers do not ask questions. Configuration errors stop before runtime
start. Do not run setup or start in a second window when a watchdog/runtime already owns the lease.

### CPython 3.14 Windows x64 package artifacts

PyPI was checked on 2026-10-06 for the exact pinned artifacts: [pandas 3.0.6](https://pypi.org/project/pandas/3.0.6/), [NumPy 2.4.6](https://pypi.org/project/numpy/2.4.6/), and [scikit-learn 1.9.1](https://pypi.org/project/scikit-learn/1.9.1/) publish `cp314-cp314-win_amd64` wheels; [LightGBM 4.7.0](https://pypi.org/project/lightgbm/4.7.0/) publishes `py3-none-win_amd64`; and [MetaTrader5 5.0.6231](https://pypi.org/project/metatrader5/5.0.6231/) publishes `cp314-cp314-win_amd64`. No pins were changed. `ta==0.11.0` publishes an sdist rather than a wheel; its package is Python source and pip builds it during installation. This artifact check is not a Windows install or runtime qualification.

## Start and recovery invariants

The runtime claims a single-process lease and enters PAUSED state before initialization. It reconciles the
broker ledger and durable order intents before exposing readiness. AUTONOMOUS_DEMO only attempts resume
after execution, signal, AI and news components are ready and an AI health check succeeds; retries occur
on the 60-second scheduler. A watchdog replacement starts through the same paused initialization path.
Every resume uses the same durable risk/baseline/latch/continuity/unsettled-intent/account gate. Native
AUTONOMOUS_DEMO accepts only an MT5-reported DEMO account. Startup never resets a loss/drawdown latch,
kill switch, risk peak, capital ledger, intent or broker-side SL/TP.

Critical-alert recovery waits 15 minutes, rechecks all gates and is limited to three automatic resumes
per runtime UTC day. A manual local pause, designated halt, latch, unresolved write, unverified baseline,
failed AI health or non-DEMO account remains paused until a permitted reviewed action resolves it. Open
positions keep broker-side SL/TP; the local trailing loop is unavailable while the process is stopped.

## Local operations and shutdown

See [OPERATOR_PLAYBOOK.md](OPERATOR_PLAYBOOK.md) for typed command details. The common commands are:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env status
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env pause
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env resume
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env stop
.\.venv\Scripts\python.exe -m scripts.live_view
```

`stop` writes the persistent `data\runtime\operator-stop.json` marker and requests graceful shutdown.
Wait for child exit; do not force-kill a broker worker or assume an in-flight write was recalled. Inspect
positions, intents and local reports before any restart. Clear a reviewed marker only after child exit:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env clear-stop
```

A clear does not start the runtime or reset a latch. The next startup is PAUSED. Do not delete the marker
by hand. The local viewer opens no server/listener. Reports are in `data\reports\actions.log` and daily
`data\reports\YYYY-MM-DD.jsonl`.

## Secrets and optional reporting

Keep `.env` private and outside source control. Do not paste keys into chat, logs, command arguments or
reports. Telegram settings are outbound-only. Valid token/chat fields enable HTTPS `sendMessage`; missing
or invalid config disables remote delivery but leaves local reports active. There is no Telegram command
handler, webhook, polling loop, Mini App, keyboard, public API or operator network endpoint.

## What still needs actual Windows validation

This repository task has not run on a Windows host or connected to a real MT5 terminal. Before any
operational reliance, independently verify Windows ACLs and user-session policy, actual broker account
identity, contract/tick/fee/margin/stop rules, news/provider entitlement, terminal restart/reconnect,
order-check/write paths in DEMO, broker-side SL/TP, clock/sleep/RDP behavior, backup/restore, and
watchdog graceful shutdown under fault. Synthetic/mock tests do not prove native execution, profitability,
production readiness or stage eligibility. No LIVE operation is authorized.
