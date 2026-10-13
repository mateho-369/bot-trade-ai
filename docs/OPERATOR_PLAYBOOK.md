# Local operator playbook

There is no owner API, Telegram control, Mini App, webhook, polling loop or operator HTTP listener.
Control is local-only, typed, audited and process/OS-user bound. Commands below require an existing
reviewed `.env` and existing database; `scripts.ops` never initializes or migrates a database and never
calls a broker.

## Inspect and entry controls

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --help
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env status
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env pause
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env resume
```

`status` reports durable control/latch/intent state and bounded health age. `pause` blocks new entries;
protective management remains active while the runtime is running. `resume` requires current local health,
completed reconciliation/readiness, a current runtime lease and the shared account/risk/baseline/intent/
latch resume gates. Under `AUTONOMOUS_DEMO=true`, it also requires successful AI health. A command failure
prints only a fixed error type, not a raw exception or secret.

## Kill switch and designated halt review

`kill` durably latches entry permission off. It is not a graceful process stop. Only after a fresh runtime
has reconciled and every risk gate is clear may the current operator explicitly reset the kill latch;
the runtime remains PAUSED:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env reset-kill --confirm RESET_KILL_AND_KEEP_PAUSED
```

`recover --confirm ACKNOWLEDGE_RECOVERY_KEEP_PAUSED` reviews a designated halt only after the cause is
resolved, the fresh runtime is reconciled, broker writes are no longer quarantined and no intent remains
unsettled. It leaves entries paused. `review-baseline --confirm REVIEW_SAMPLED_BASELINE` is only for a
fresh, reconciled, flat account with complete known history/cash flows; it cannot clear loss/drawdown
latches or invent a historical high-water mark.

Do not use recovery commands to bypass a failed gate. Open trades retain broker-side SL/TP; never assume
that pausing or stopping flattened a position.

## Graceful stop and persistent stop marker

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env stop
```

This writes `data\runtime\operator-stop.json`. It requests graceful exit; it does not kill a process or
recall an in-flight broker write. Wait for the runtime/watchdog child to exit, inspect local status and
broker state, then explicitly clear the marker only when reviewed:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env clear-stop
```

The marker is preserved as evidence with a false stop value; do not delete it manually. Clearing it does
not start a runtime, resume entries, clear the kill switch or clear risk latches. The next start is still
PAUSED and must reconcile.

## AI-only local settings

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env ai-fallback --mode BLOCK_ON_AI_FAILURE
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env ai-fallback --mode TECHNICAL_ONLY
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env reset-ai --confirm RESET_AI_TO_REVIEWED_DEFAULTS
```

These are audited local policy transitions, not trading authorization. `TECHNICAL_ONLY` does not bypass
`AI_REQUIRE_APPROVAL`, risk, news, spread, stage, pause/kill or broker-write gates. `reset-ai` reverts AI
overlays only; it does not clear capital/risk latches, pause, kill or process-stop markers.

## AI proposal review

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env proposals --status pending
```

Use the numeric ID returned by `proposals` with `proposal-show` to inspect the bounded parameters,
reason, expiry and exact decision confirmations. `proposal-decide` accepts only the matching current
confirmation string, rechecks the proposal hash inside the decision transaction, and records an
integrity-checked local decision. Approval does not apply settings, start a model, resume entries or
execute a trade. There is no interactive prompt; stale/expired proposals are refused. Proposal
projection is not exposed by this CLI.

## Local reports

```powershell
.\.venv\Scripts\python.exe -m scripts.live_view
```

Reports are in `data\reports\actions.log` and daily JSONL. The viewer is read-only and opens no socket.
Optional Telegram reporting is outbound HTTPS `sendMessage` only; missing/invalid config leaves local
reports enabled. No Telegram interaction can change bot state.
