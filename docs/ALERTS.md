# Alert Center

Real-time owner alerts for errors, warnings and critical events. The code is in `app/alerts.py`;
the runtime wires it in `app/dependencies.compose`, `app/scheduler.py` and `app/lifecycle.py`.
Nothing in the alert path places, modifies or closes an order.

## Levels

| Level | Stored (`alerts` table, Mini App, `/alerts`) | Telegram | Extra |
|---|---|---|---|
| `INFO` | yes | no (log only) | — |
| `WARNING` | yes | yes | — |
| `ERROR` | yes | yes, on the next 5 s flush | — |
| `CRITICAL` | yes | yes, on the next flush | **auto-pause** of new entries |

A CRITICAL auto-pause calls `RuntimeControl.auto_pause("critical_alert")`. It sets the desired
state to `paused` and writes the `runtime.auto_paused` audit record. It is **not** a halt: it does
not set `last_error`, touch the kill switch or loss latches, or change open positions. Resume with
the normal, fully gated `/resume`.

## What raises an alert

| Event | Level | Source |
|---|---|---|
| MT5 connection lost (broker error in heartbeat/positions/signals/position reviews) | ERROR | scheduler hook |
| MT5 reconnected (first successful run after the failure) | WARNING | scheduler hook |
| AI provider failure: circuit open, with the active fallback mode (BLOCK = entries blocked) | ERROR | `AIFirstLayer` status listener |
| AI provider answering again | WARNING | `AIFirstLayer` status listener |
| `AI_FALLBACK: …` log lines | WARNING | log handler |
| Order rejected by the broker | ERROR | audit `broker.acknowledged` status=rejected |
| SL/TP modification rejected | ERROR | audit `broker.acknowledged` operation=protect |
| Profit-lock SL update not confirmed | WARNING | audit `trailing.lock_not_claimed` |
| Kill switch activated | CRITICAL | audit `owner.killed` |
| Daily loss limit / max drawdown reached (auto-pause latch) | CRITICAL | audit `risk.*_latched` |
| Watchdog restarted the bot | WARNING | audit `runtime.notice_pending` kind=restart |
| Watchdog restart budget exhausted | CRITICAL | audit `runtime.notice_pending` kind=budget |
| Runtime stalled | ERROR | audit `runtime.notice_pending` kind=stalled |
| Database write failure | ERROR | audit `runtime.halted` reason=persistence_failure |
| Other runtime halts / reconcile or ledger halts | ERROR | audit `runtime.halted`, `reconcile.*_halt`, `broker.*_halt` |
| Risk check blocked an AI-approved trade (margin, risk cap, position cap, …) | WARNING | audit `risk.entry_decision` approved=false |
| Any other WARNING+ record from the bot's own loggers | WARNING/ERROR/CRITICAL | log handler |
| AI fallback mode changed by the owner | INFO | audit `owner.ai_fallback_mode_changed` |

Some events are deliberately **not** alerted:
- Routine vetoes caused only by a known state (paused, killed, latched). The owner already knows.
- `runtime.halted` with reason `broker_unstable`, because the richer "Connection lost" alert covers it.
- Low-confidence `AI_TRAILING_FALLBACK`, which is normal mechanical continuation.

Bot start and stop, trades, SL locks, daily reports and news pauses are covered by the existing
owner notifications (`RuntimeNotices`, `AIOwnerNotifier`, the news notifier).

The audit scanner starts at the current maximum `audit_logs.id`, so history is never replayed as
new alerts after a restart.

## Delivery rules

- **Dedup:** the same alert (level + component + message with digits stripped) within **5 minutes**
  is grouped. Only the first one is sent, and the row's `repeat_count` goes up. The next message
  after the window shows `Repeated: xN in the last 5 min`. CRITICAL is **never** deduplicated.
- **Rate limit:** at most **5 Telegram messages per rolling 5 minutes**, with CRITICAL exempt.
  Suppressed alerts stay stored (`telegram_status = rate_limited`), and the next sent message says
  how many were suppressed.
- **Independence:** a database failure never stops Telegram delivery, and a Telegram failure never
  stops storage. Delivery is at most once: a failed send is stored as `uncertain` and never resent.
- **Sanitized:** secrets are redacted, raw broker or SDK exception text is never forwarded, and
  messages are plain text (`parse_mode=None`).
- **Thread-safe intake:** `emit()` is in-memory and O(1), never does I/O and never raises.
  The `alerts` job (every 5 s) persists and delivers. A final flush runs on shutdown.

Telegram format:

```
🚨 ERROR ALERT
Time: 2026-10-05 08:15:02 UTC
Level: ERROR
Component: MT5 Client
Message: Connection lost: broker unavailable
Action: New entries halted (broker_unstable); protective jobs keep retrying
```

## Schema (`alerts`, additive; core schema version unchanged)

```sql
CREATE TABLE alerts (
  id               INTEGER PRIMARY KEY,
  timestamp        DATETIME NOT NULL,        -- UTC, indexed
  level            VARCHAR(10) NOT NULL,     -- INFO | WARNING | ERROR | CRITICAL, indexed
  component        VARCHAR(64) NOT NULL,     -- MT5 Client, AI Provider, Risk Engine, Execution, ...
  message          VARCHAR(500) NOT NULL,
  details          JSON NOT NULL,            -- bounded, sanitized
  action           VARCHAR(200),             -- what the bot did / what the owner should do
  fingerprint      VARCHAR(64) NOT NULL,     -- dedup key, indexed
  repeat_count     INTEGER NOT NULL DEFAULT 1,
  last_seen        DATETIME NOT NULL,
  telegram_status  VARCHAR(16) NOT NULL,     -- sent | uncertain | rate_limited | no_transport | not_required
  acknowledged     BOOLEAN NOT NULL DEFAULT 0,
  acknowledged_at  DATETIME,
  acknowledged_by  BIGINT,
  resolved         BOOLEAN NOT NULL DEFAULT 0,
  resolved_at      DATETIME
);
```

`python main.py init-db` creates the table; the runtime also creates it additively.

## Owner interfaces

Telegram (owner only):

| Command | Effect |
|---|---|
| `/alerts` | last 10 alerts with unacknowledged count |
| `/alerts critical` | filter: `critical`, `error`, `warning`, `info` |
| `/ack_all` | acknowledge every alert (audited `owner.alerts_acknowledged`) |
| `/ack ID` | acknowledge one alert |

Mini App: **Alerts** page (sidebar). It shows:
- the last 100 alerts, newest first;
- filter buttons All / Critical / Error / Warning / Info;
- colour by level: INFO blue, WARNING amber, ERROR orange, CRITICAL red;
- "Repeated xN" and the Telegram status on each row;
- an Acknowledge button per alert, plus Acknowledge all;
- the unacknowledged count in the sidebar;
- auto-refresh every 30 s.

API (owner `X-Telegram-Init-Data` required):
- `GET /api/alerts?level=CRITICAL&limit=100&offset=0` → `{items, counts, unacknowledged}`
- `POST /api/alerts/ack` with `{"request_id": "<uuid>"}` acknowledges all;
  with `{"request_id": "<uuid>", "alert_id": 7}` it acknowledges one.

Acknowledging only marks alerts as seen. It never resumes trading or changes risk.

## Log files

`data/logs/` contains:
- `bot.log`: everything;
- `errors.log`: WARNING and above;
- `ai_decisions.log`: every AI entry decision, trailing decision and config adjustment, with its
  reason.

Each file rotates at `LOG_MAX_BYTES` (5 MB) with `LOG_BACKUP_COUNT` (5) backups, so up to 6 files
per log.

## Tests

`tests/test_alerts.py` covers:
- MT5 disconnect and reconnect alerts through the real scheduler;
- dedup with "repeated xN";
- the rate limit;
- CRITICAL bypass and auto-pause (resume still works);
- INFO stored only;
- Telegram failure stored as `uncertain` and never resent;
- the audit event map, and that history is not replayed;
- the log handler filters;
- `/api/alerts` with filter and ack;
- `/alerts`, `/alerts critical`, `/ack_all`;
- the three rotating log files.
