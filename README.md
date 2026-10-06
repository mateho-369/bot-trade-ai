# MT5 AI ReflexBot — autonomous DEMO release

A Windows-first MetaTrader 5 **DEMO-only** trading runtime. `LIVE_TRADING=true` is refused at startup.
It starts PAUSED, reconciles broker state before entry permission, and cannot resume unless the
existing risk, stop-loss, AI-approval, stage, news, spread, idempotency, broker and restart gates pass.
This software is not financial advice and does not guarantee fills, returns, or protection from gaps.

## Safety defaults

| Setting | `.env.example` | `.env.demo.example` |
|---|---:|---:|
| `LIVE_TRADING` | `false` | `false` |
| `START_PAUSED` | `true` | `true` |
| `AUTONOMOUS_DEMO` | `false` | `true` |
| `PAPER_TRADING` | `true` | `false` |
| `AI_REQUIRE_APPROVAL` | `true` | `true` |
| `DEMO_FAST_TRACK` | `false` | `false` |
| Remote Telegram reporting | optional | optional |

`AUTONOMOUS_DEMO=true` is incompatible with live trading and, on native MT5, requires the terminal to
report a DEMO account. It does not bypass restart reconciliation, stage evidence, AI approval, risk
limits/latches, required broker SL/TP, circuit breakers, news/spread gates, unsettled-order checks or
automatic halts. A critical alert pauses entries; recovery waits 15 minutes, rechecks the shared gates,
and is limited to three automatic resumes per UTC day. A cap or failed gate leaves the runtime paused.
Open positions retain broker-side SL/TP; local trailing cannot run while the bot is stopped.

## Windows setup and exact start steps

1. Install Python 3.11 x64 and MetaTrader 5; log in to the intended **DEMO** account.
2. From the repository folder run in PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_demo.ps1
   ```
   The first run installs the virtual environment and requirements, copies `.env.demo.example` to
   `.env`, and exits. Edit `.env` without changing the safety flags; set your AI key and verify the MT5
   terminal path. Reporting credentials are optional.
3. Run the same PowerShell command again. It performs configuration, database-preservation, read-only
   MT5, symbol, news and preflight checks, then starts the watchdog minimized with no prompts. A real,
   contest or unknown MT5 account is refused.
4. To start a later run from a normal command window, use `start_demo.bat`. It requires the reviewed
   `.env` and `.venv`; it does not install packages or prompt. Do not start a second copy while the
   watchdog/runtime is already active.

See [docs/DEMO_QUICKSTART.md](docs/DEMO_QUICKSTART.md) for local stop, pause, resume, clear-stop,
kill-latch and report-viewer instructions. See [docs/DEPLOYMENT_WINDOWS.md](docs/DEPLOYMENT_WINDOWS.md)
for deployment constraints.

## Local operator and reports

There is no inbound Telegram control, Telegram polling/webhook, keyboard, Mini App, owner API or
operator network listener. `python -m scripts.ops` provides current-process local typed transitions;
`python -m scripts.live_view` is a read-only console viewer. Examples (from the project folder):

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --help
.\.venv\Scripts\python.exe -m scripts.ops status
.\.venv\Scripts\python.exe -m scripts.ops pause
.\.venv\Scripts\python.exe -m scripts.ops resume
.\.venv\Scripts\python.exe -m scripts.ops stop
.\.venv\Scripts\python.exe -m scripts.ops proposals --status pending
.\.venv\Scripts\python.exe -m scripts.live_view
```

`stop` writes the persistent `data\runtime\operator-stop.json` marker; wait for graceful child exit.
Only clear a reviewed marker with `python -m scripts.ops clear-stop`. A clear does not start the bot,
reset a kill switch, or clear a daily-loss/drawdown latch. Open the CLI help for confirmation strings
for separately reviewed kill/halt/baseline actions. Proposal decisions use a hash-bound confirmation and
never apply settings or execute trades.

Reports always work locally, even if Telegram configuration is blank/invalid:
`data\reports\actions.log` and daily `data\reports\YYYY-MM-DD.jsonl`. Optional Telegram delivery is
outbound HTTPS `sendMessage` only; failures are isolated and do not affect trading controls.

## Included safety systems

- Persistent `start_paused` runtime; restart reconciliation and durable order-intent idempotency.
- Conservative risk sizing, daily-loss/drawdown latches, stale-observation checks and circuit breakers.
- AI approval required for new entries by default; unavailable/invalid AI blocks entries.
- News, calendar, spread, quote freshness and broker-side SL/TP gates.
- Protective position management continues while entries are paused/killed; automatic kill/halt triggers
  are never cleared by auto-resume or restart.
- A local stop marker survives process restarts; watchdog replacement follows the same paused/reconcile
  path and never kills a still-running process or resubmits uncertain broker writes.
- Offline synthetic tests and demo-cycle checks are not strategy-performance or promotion evidence.

## Developer verification

```bash
python -m compileall -q ai app backtesting core news readiness scripts strategy trading watchdog.py
ruff check .
PYTHONPATH=. python -m pytest -q
```

Tests use synthetic/mock brokers and mocked provider transports. They do not prove a Windows terminal,
provider entitlement, broker execution, profitable strategy, or future live-stage eligibility.
Rebuild the release manifest only after the full suite passes, and supply the numeric passed-test count from that exact run; never reuse a historical count.

## Documentation

- [Autonomous DEMO quickstart](docs/DEMO_QUICKSTART.md)
- [Windows deployment](docs/DEPLOYMENT_WINDOWS.md)
- [Local operator playbook](docs/OPERATOR_PLAYBOOK.md)
- [Runtime architecture](docs/ARCHITECTURE.md)
- [Alerts and reporting](docs/ALERTS.md)
- [AI safety and fallback](docs/AI_FIRST_ARCHITECTURE.md)
- [Preflight/readiness](docs/READINESS.md)
- [Khmer report catalog review](KHMER_REVIEW.md)

`docs/PART_*.md`, historical release manifests and validation captures are development-history
artifacts; they are not current run instructions. Follow this README and the documents above.
