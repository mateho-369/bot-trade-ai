# Part 16 — Autonomous DEMO and local-only operations (0.14.0 · schema 2)

This release replaces remote operator controls with a local process-bound operator and makes the optional Telegram integration outbound-reporting only. It does not relax any entry, risk, broker, reconciliation, or recovery gate.

## Safety and startup

- `LIVE_TRADING=true` is refused at startup. `AUTONOMOUS_DEMO` is false in code defaults and true only in `.env.demo.example`; it is incompatible with live trading and requires the connected MT5 account to report DEMO. Mock/PAPER configurations remain available for offline testing.
- Startup remains paused. Reconciliation, initialized services, fresh risk baseline, news/spread checks, AI health and approval, idempotent intent state, and every existing resume gate remain authoritative. The same shared resume function is used by local resume and automatic resume.
- Automatic resume retries after startup readiness and successful AI health, then at a bounded interval. Critical-alert recovery requires its cooldown and all gates, is capped per runtime day, and never overrides a kill, halt, latch, unsettled intent, unverified baseline, or non-DEMO account. Watchdog replacement follows the same paused reconciliation path.
- Broker-side SL/TP, risk limits, drawdown/loss latches, circuit breakers, and automatic halt triggers remain in place. A pause does not remove protective broker orders from open positions.

## Local operations and reporting

`python -m scripts.ops` is the only production operator-control interface. It binds to the current local OS user/process, uses typed reviewed transitions, opens no listener, and never initializes or migrates runtime storage. Pending AI proposals can be listed, inspected, and approved/rejected only with a hash-bound confirmation; a decision never applies settings or executes a trade. Local stop requests remain in `operator-stop.json`; clearing one only permits a later startup, which is still paused and must reconcile.

Reports are stored locally first (`data/reports/actions.log` and bounded daily JSONL), mirrored to the console, and exposed through `python -m scripts.live_view`. Optional Telegram delivery uses HTTPS `sendMessage` only; missing or malformed report credentials disable delivery without disabling local reports. Telegram polling, webhook, commands, keyboards, Mini App, and owner-authenticated API controls are removed.

## Windows DEMO setup

Use the no-prompt path documented in `docs/DEMO_QUICKSTART.md`: review and copy `.env.demo.example`, fill private MT5/AI values in `.env`, run `setup_demo.ps1`, then launch `start_demo.bat`. The template remains paused at startup; no start script asks for confirmation or silently enables live trading. Use `python -m scripts.ops stop` for a persistent stop request and `python -m scripts.ops clear-stop` only after review; clearing does not resume entries.

## Verification limits

Validation was executed on Linux with Python 3.11.2: the complete suite passed (2,451 tests); the dedicated offline DEMO-cycle E2E passed (8 tests); runtime and readiness smokes passed (13/13 and 14 checks); `compileall`, `ruff check .`, and `ruff format --check .` passed (350 files already formatted). The byte manifest records the full-suite pass count. It is not a signature, source provenance proof, stage qualification, or trading permission. Windows/MT5 behavior, account and data-source entitlements, real broker execution, provider availability, and profitability require separate review and are not asserted by this release.
