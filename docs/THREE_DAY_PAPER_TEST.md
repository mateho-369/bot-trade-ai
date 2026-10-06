# Three-day PAPER runtime soak (optional; not trading evidence)

This protocol checks local runtime/process/report stability only. It is not evidence of strategy profitability,
AI quality, native MT5 behavior, demo-stage eligibility or permission to trade LIVE. No Telegram setup or
remote controls are required.

## Choose a safe source

- **Mock/PAPER** (`MT5_BACKEND=mock`, `PAPER_TRADING=true`) uses synthetic prices and never reaches MT5.
- **Native-data PAPER** (`MT5_BACKEND=real`, `PAPER_TRADING=true`) reads an actual terminal but wraps it
  with simulated execution. Use only an MT5 DEMO login for this optional observation, and verify that no
  broker order-write path is enabled.

Keep `LIVE_TRADING=false`, `START_PAUSED=true`, `AUTONOMOUS_DEMO=false` and
`AI_REQUIRE_APPROVAL=true`. Do not turn off stage/risk/news/SL gates or use `DEMO_FAST_TRACK` to make the
soak pass. If readiness or stage evidence is missing, keep entries paused and treat the test as a process
soak only.

## Run

1. Install 64-bit CPython 3.11–3.14 and the project dependencies. Copy `.env.example` to `.env` and review the
   settings/paths. The generic `install.bat` creates the DB if missing but never starts a runtime.
2. Run `start.bat` once from the repository folder. Startup remains PAUSED through reconciliation.
3. Inspect local state periodically:
   ```powershell
   .\.venv\Scripts\python.exe -m scripts.ops --env-file .env status
   .\.venv\Scripts\python.exe -m scripts.live_view --env-file .env
   ```
4. Record watchdog restarts, scheduler job failures, stale health, alerts, database/backup failures and
   any halt in the local report files. Do not delete a stop marker, reset capital/latches, or retry an
   uncertain broker write to make the soak continue.
5. Stop gracefully with `python -m scripts.ops --env-file .env stop`; wait for the child to exit and
   review broker/local state. Clear a reviewed marker only afterward with `clear-stop`.

The reporter writes `data\reports\actions.log` and daily JSONL regardless of whether optional Telegram
outbound reporting is configured. No listener is started. Pause is not flattening: open positions retain
broker-side SL/TP. A stopped runtime cannot run local trailing.

## What a completed soak would and would not show

A complete recorded 72-hour run may help identify process, file-permission, health, scheduling and local
reporting failures on that particular host. It does not prove fill quality, future behavior, cost-inclusive
profitability, native broker write safety, news completeness or a stage gate. Current repository state
contains no claim that such a Windows soak has been performed.
