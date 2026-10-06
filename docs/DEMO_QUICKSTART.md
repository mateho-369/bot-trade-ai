# Autonomous DEMO quickstart (Windows)

This release is DEMO-only. `LIVE_TRADING=true` is refused, and AUTONOMOUS_DEMO requires the MT5
terminal to report a DEMO account. Startup is paused through reconciliation; entries resume only when
the AI health check and every existing risk, SL, stage, news, spread, intent and broker gate passes.
No setup or start step asks interactive questions.

## First setup and start

1. Install Python 3.11 x64 and MetaTrader 5. Log MT5 in to the intended **DEMO** account and keep it open.
2. Open PowerShell in the repository folder and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_demo.ps1
   ```
   On the first run the script creates `.venv`, installs the pinned requirements, copies
   `.env.demo.example` to `.env`, and exits without starting the bot. Edit `.env` with a text editor;
   set `OPENAI_API_KEY` to your Groq-compatible key and confirm `MT5_TERMINAL_PATH`. Telegram reporting
   is optional: set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_REPORT_CHAT_ID`, or leave both blank.
   Keep the safety settings unchanged: `LIVE_TRADING=false`, `START_PAUSED=true`,
   `AUTONOMOUS_DEMO=true`, `AI_REQUIRE_APPROVAL=true`, `DEMO_FAST_TRACK=false`.
3. Run the same PowerShell command again. It checks config, preserves any existing database, resolves
   broker symbol suffixes, checks the MT5 account read-only, probes news, runs preflight, then starts
   the watchdog minimized. It does not prompt. It refuses to start if the terminal reports REAL,
   CONTEST or an unknown account type. An unavailable required news feed blocks new entries.
4. To start later from a normal command window, run `start_demo.bat` in the project folder. It requires
   the existing `.venv` and reviewed `.env`; it does not install packages or ask questions.

AUTONOMOUS_DEMO never overrides the startup pause, reconciliation, existing DEMO stage-evidence gates,
AI approval, risk sizing, required stop-loss, loss/drawdown latches, circuit breakers, news/spread checks,
order idempotency or automatic halt conditions. A critical alert pauses new entries; recovery waits
15 minutes, must pass every shared resume gate, and is capped at three automatic resumes per runtime
UTC day. Open positions retain broker-side SL/TP; local trailing is unavailable while the runtime is
stopped.

## Local controls and reports

Use these commands from the project folder; none starts a network listener:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops status
.\.venv\Scripts\python.exe -m scripts.ops pause
.\.venv\Scripts\python.exe -m scripts.ops resume
.\.venv\Scripts\python.exe -m scripts.ops stop
.\.venv\Scripts\python.exe -m scripts.ops proposals --status pending
.\.venv\Scripts\python.exe -m scripts.ops --help
```

`pause` stops new entries while protective management continues. `proposals --status pending` lists
bounded AI candidates; `proposal-show` displays the exact review-bound confirmation, and
`proposal-decide` records only an integrity-checked local approval/rejection. It does not change active
settings or execute a trade. `stop` writes the persistent `data\runtime\operator-stop.json` request;
wait for the child to exit cleanly before restarting. Do not delete that file by hand. To clear a
reviewed stop only after the runtime has exited:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops clear-stop
```

A clear does not start the bot or clear a kill/loss/drawdown latch; the next startup remains paused.
The kill switch is separate: `kill` latches it; after the cause is resolved, a fresh/reconciled runtime
can reset only the kill latch while remaining paused with:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops reset-kill --confirm RESET_KILL_AND_KEEP_PAUSED
```

Other designated halts and sampled-baseline review have their own explicit local commands; see
`python -m scripts.ops --help`. Never use a reset to bypass a failed gate or clear a risk latch.

Local reports are always available, even when Telegram is missing or invalid:

```powershell
.\.venv\Scripts\python.exe -m scripts.live_view
```

Read `data\reports\actions.log` and the daily `data\reports\YYYY-MM-DD.jsonl`. If configured,
Telegram is outbound HTTPS `sendMessage` reporting only: there are no inbound commands, keyboard,
Mini App, webhook, or Telegram-based resume controls.

## Optional second AI provider

The single configured provider is sufficient. For an additional provider, declare the secret with an
environment-variable name and add the corresponding provider entry to `AI_PROVIDERS`; never put a key
value in the JSON. Keep `AI_REQUIRE_APPROVAL=true`. A valid wait/reject is final, and an unavailable AI
means no approved new entry.
