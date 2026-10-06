# Runtime architecture and safety contract

## Current runtime shape

```text
Windows user session / start_demo.bat or setup_demo.ps1
  └─ watchdog.py (single managed child, graceful stop, bounded restart policy)
      └─ app.bot → app.lifecycle.RuntimeLifecycle
          ├─ app.process_guard (OS lock, runtime lock, persistent stop marker)
          ├─ app.dependencies.compose (explicit service composition; no listener)
          ├─ trading.execution.ExecutionEngine / RuntimeControl
          ├─ trading.risk_engine / StageGate / durable OrderIntent reconciliation
          ├─ strategy.SignalEngine + ai.AISupervisor + bounded AI-first layer
          ├─ news.NewsManager / calendar and spread gates
          ├─ app.scheduler.RuntimeScheduler (one worker, coalesced jobs)
          └─ app.alerts.AlertCenter → app.reporter.Reporter
               ├─ local console + data/reports/actions.log + daily JSONL
               └─ optional HTTPS Telegram sendMessage only
```

`main.py` is the safe administrative CLI for `check-config` and explicit DB initialization; it is not
the daemon entry point. `python -m scripts.ops` performs current-local-operator typed transitions without
a listener. `python -m scripts.live_view` is a read-only local health/report viewer. The Telegram bot,
Mini App, inbound webhook/polling, API server, owner routes and control keyboards have been removed.

## Start and resume state machine

1. Startup refuses `LIVE_TRADING=true`, a missing reviewed environment, a live/unsafe mode or a persistent
   local stop marker. An existing database is verified; the daemon does not silently initialize/migrate
   it or reset financial state.
2. The runtime takes a single-process lease and sets durable desired state to PAUSED (or keeps KILLED if
   latched). It restores state and reconciles orders/deals/positions/intents before entry permission.
3. Execution, signal, AI and news components initialize while entry permission remains paused.
4. `AUTONOMOUS_DEMO=true` may attempt resume after components and AI health are ready, and rechecks every
   60 seconds. Local resume and auto-resume share `evaluate_resume_gates()`.
5. A replacement watchdog child uses this same flow. It cannot adopt/kill a still-running child, reset a
   latch or resend an uncertain broker write.

The shared gates refuse kill/loss/drawdown latches, designated halt reason, missing/unverified/stale risk
baseline, risk continuity gaps, unsettled intents and, in autonomous native mode, any account not reported
as DEMO by MT5. Recovery after a CRITICAL alert additionally needs 15 minutes, all normal gates, no newer
local pause/review action and fewer than three automatic resumes in the current UTC day. Cap exhaustion
leaves entries paused. Local manual pause is not overridden by retry.

## Execution invariants

- Risk sizing uses conservative account capital and the deterministic risk engine; an AI may veto or
  reduce risk only. AI approval is required for new entries by default.
- Required SL/TP, news/calendar, spread, tick freshness, stage, broker/account identity and kill/loss
  gates remain authoritative. Any fail-closed signal blocks a new entry.
- Each broker operation is represented by a durable idempotent intent and risk reservation before write.
  Unknown acknowledgements are reconciled, never blindly resent.
- Restart reconciliation binds trades to broker order/deal/position identifiers and blocks on ambiguity.
- Broker-side SL/TP remains on open positions through pause/stop. Local trailing and monitoring require
  a running process; no stop operation can recall an accepted broker write.

## Local operator and reporting boundaries

`core.local_operator.LocalOperator` binds a typed control action to the current OS user/process. The local
CLI calls reviewed `RuntimeControl` transitions; no browser/client-supplied identity or HTTP request is
accepted. `stop` persists `data/runtime/operator-stop.json`; clear-stop only clears the marker and never
starts/resumes or clears risk/kill latches.

`Reporter` bounds text/details/queue and stores local reports before optional delivery. Valid Telegram
configuration permits only HTTPS `sendMessage`; every inbound Telegram feature was removed. Reporter
failure does not affect trading. Khmer catalog parity is tested and the exact strings are in
`KHMER_REVIEW.md` with native review still required.

## Persistence compatibility

SQLite schema version 2 retains legacy columns/tables for existing-database compatibility. Old approval,
alert acknowledgement and Telegram-status storage fields have no current inbound-control path. Existing
financial rows, stop markers, intents, kill/risk latches, account baselines, checkpoints and broker SL/TP
must not be dropped/reset to make a validation pass.

## Verification boundaries

Automated tests use mocked transports and synthetic/mock/paper brokers. They prove code-level safety
contracts only; they do not prove Windows ACLs/session behavior, actual MT5 DEMO identity, broker writes,
provider/news entitlement, trading profitability, a live-stage evaluation or live authorization.
