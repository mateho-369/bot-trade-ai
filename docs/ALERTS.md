# Alerts and outbound reporting

## Safety behavior

`app.alerts.AlertCenter` converts bounded log/audit events into sanitized durable alert rows. Levels are
`INFO`, `WARNING`, `ERROR` and `CRITICAL`. Repeated non-critical fingerprints are deduplicated for five
minutes; optional outbound reporting is rate-limited to five messages per five minutes. Critical reports
bypass that rate limit and are never suppressed by normal deduplication.

A `CRITICAL` alert persists an auto-pause request when entries are running. It does not close positions,
remove SL/TP, clear latches or call the broker. AUTONOMOUS_DEMO recovery is handled by the same shared
resume gates as startup, after a 15-minute cooldown, with all risk/account/readiness gates satisfied and
at most three automatic resumes per UTC day. Kill switches, designated halts, loss/drawdown latches,
unsettled intents, an unverified risk baseline or a non-DEMO account cannot auto-recover. A local pause
is never undone by autonomous retry.

Fixed halt reports explain that entries are stopped, preserve broker-side SL/TP, and name the local
remedy path. Raw SDK/provider exception bodies and secrets are not included in the report. Report failure
is isolated from execution and never changes a control transition.

## Local-first reporter

`app.reporter.Reporter` writes bounded records to:

- `data/reports/actions.log` — human-readable local mirror;
- `data/reports/YYYY-MM-DD.jsonl` — structured daily records;
- the console mirror.

Files are private, bounded and append-only. Missing or invalid Telegram credentials disable only remote
delivery; local reports continue. When valid, the only remote request is HTTPS `sendMessage`; there are
no inbound commands, polling, webhooks, keyboards, acknowledgements or Telegram control routes. Network
failure is recorded locally and is not retried blindly.

Alert storage keeps some old schema-v2 columns (`telegram_status`, acknowledgement and resolution
fields) for existing database compatibility. Current code writes report status only; no API or operator
path acknowledges/resolves alerts remotely.

## Viewer and tests

```powershell
.\.venv\Scripts\python.exe -m scripts.live_view
.\.venv\Scripts\python.exe -m scripts.ops status
```

The viewer is read-only and starts no listener. See `KHMER_REVIEW.md` for the full `km` catalog and
required native-speaker review status. Automated tests enforce EN/KH catalog-key parity, bounds,
local-first records, sendMessage-only requests, failure isolation and critical-pause safety.
