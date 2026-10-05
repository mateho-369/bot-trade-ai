# Part 9 — Telegram owner bot, Mini App API and mobile frontend

**0.7.0 · cumulative Parts 1–9 · schema 2 retained · 2026-10-03.**

This installment supplies real aiogram 3 handlers/transport, a FastAPI factory and
routes, a dark responsive vanilla-JS owner console, durable action confirmations,
SQL-only projections, notification delivery and executable offline diagnostics.
It is **not** the assembled autonomous trading daemon: lifecycle, APScheduler,
30-second watchdog and Windows/VPS startup/backup scripts are Part 10; historical
chronological backtesting/usage is Part 11. No real trading/deployment is authorized.

`PART_09.md` contains every complete current Python module/test/config plus the full
HTML/CSS/JavaScript frontend. Ordinary files are executable; the guide is a literal
source snapshot. `CURRENT_TREE.txt` is the actual packaged tree; `TARGET_TREE.txt`
separates remaining future files. Historical Parts 1–8 snapshots are unchanged.

## 1. Authentication, not an integer owner parameter

### Mini App

The browser sends the **original** `Telegram.WebApp.initData` in exactly one
`X-Telegram-Init-Data` header. The server validates before reading owner data or
preparing/consuming actions. No query/body/cookie/Bearer/localStorage/developer
login fallback exists. `initDataUnsafe.user` is never used to authorize anything.

The bot-token algorithm follows the official Telegram contract: sort received
decoded fields by key, omit **hash only**, join `key=value` with newline, derive
`HMAC-SHA256(key=WebAppData, message=bot token)`, then sign the data-check-string
with those secret bytes and compare the received lowercase hex digest in constant
time. An optional `signature` stays in this bot-token HMAC. Third-party Ed25519
validation is a **different** algorithm which omits hash and signature; this
project does not interchange the two. Official reference: [1](https://core.telegram.org/bots/webapps).
Installed aiogram's signature helper was also inspected and tested for agreement.

The custom parser additionally rejects malformed percent/UTF-8, duplicate decoded
query keys or nested JSON keys, nonfinite JSON, control characters, excessive
fields/bytes/depth, non-integer/bool/string/float user IDs, wrong owner, bot users,
group/nonmatching private chat and malformed/future/stale auth_date. It does not
reserialize signed JSON or drop optional signed fields before HMAC verification.

Defaults: **8,192 initData bytes; 300s lifetime; 5s future skew**. At expiry the app
wipes the displayed owner projections and asks the owner to close/reopen from the
private bot. There is no client clock renewal, refresh token or issued app cookie.
`initData` is replayable bearer material within its TTL, **not** a nonce or proof
that every browser gesture is fresh. Keep it in memory/header only; TLS, short TTL,
no credential logs and separate single-use action challenges are all necessary.

`OwnerIdentity` constructed locally is a **trusted Python composition seam**, not
remote authentication. API identities come exclusively from the verifier. IDs in
request JSON never construct an actor.

### Telegram updates

`OwnerOnlyMiddleware` checks actual `message.from_user` or
`callback_query.from_user`, private chat ID == configured owner, current bot token,
fresh message time (default **60s**), no forwarded/via-bot/business/inline command,
and bounded text/callback. Callback messages must be accessible messages from the
current bot in the owner's private chat; the bot's sender ID is NOT mistaken for
the owner. Other users/groups/stale commands are silently ignored without replies
or control writes. Only message/callback updates are enabled for polling/webhooks.

Default polling is explicit. It first checks for an existing webhook and refuses
to silently delete it or drop pending updates. Optional webhook mode must be
explicitly configured AND injected into the API factory. It uses a fixed
`/telegram/webhook` path, HTTPS and constant-time verification of the random
`X-Telegram-Bot-Api-Secret-Token` header. Secret length is **32–256** allowed ASCII
characters; generate high entropy, do not use the example TEST_ONLY strings.
Unauthenticated webhook routes are never mounted. Polling and webhook modes cannot
run together. No method here starts polling/registers menus/webhooks on import.

## 2. Exact API surface

All `/api/*` routes below require fresh verified owner identity. List reads have
`limit` 1–100 (default 50), `offset` 0–10,000 and bounded allowlisted JSON output.
Unknown/duplicate/sensitive query parameters are rejected.

| Method / route | Meaning |
|---|---|
| GET `/api/dashboard` | Stored runtime lease/control, current/stale account observations, daily KPI, equity observations, owned candidates |
| GET `/api/positions` | Stored open/unknown ledger, NOT a broker poll or real-time floating P&L |
| GET `/api/trades` | Bounded account-currency ledger and explicitly verified USD values only |
| GET `/api/signals` | Stored scores/decisions; no review/provider/execution |
| GET `/api/news` | Stored headlines and optional initialized managed snapshot/calendar; never refresh |
| GET `/api/ai_suggestions` | Stored proposal projection; expiry displayed without updating SQL |
| GET `/api/settings` | Secret-free allowlist, read-only |
| GET `/api/logs` | Append-only audit metadata, no raw details/model payloads/log-file reads |
| POST `/api/pause` | Direct downward NEW-entry pause; protective monitoring continues |
| POST `/api/kill` | Direct downward NEW-entry latch; not flatten/kill-reset |
| POST `/api/resume` | Prepare then confirm same runtime/revision; no live approval |
| POST `/api/close_position` | Prepare/confirm exact captured bot-owned ticket + position_identifier |
| POST `/api/close_all` | Prepare/confirm captured owned positions only; pause entries before closes |
| POST `/api/approve_suggestion` | Prepare/confirm proposal decision only, NEVER apply/trade |
| POST `/api/reject_suggestion` | Direct one-way proposal rejection |
| POST `/api/cancel_confirmation` | Cancel pending token; never execute its action |

Public `/`, fixed `/static/*` assets and `/healthz` reveal no owner/broker/secrets.
`healthz` checks interface/schema readiness only and declares NOT trading health.
No open/order/live-enable/risk-editor/withdrawal/model-start/apply/kill-reset route
exists. Docs/OpenAPI downloads are disabled. No factory/import/startup resumes,
initializes a broker/provider/bot, creates/migrates/resets the production database,
or spawns a trading task. Lifespan verifies existing schema only.

### Strict bodies and two-phase request

Use a canonical lowercase UUID `request_id`; never encode credentials into it.
All action bodies reject extra fields, coerced bool/string/float IDs, oversized
integers and invalid nonce syntax. Close/proposal IDs are positive 64-bit integers.

First phase (prepares, does NOT resume/close/approve):

```json
{"request_id":"ca0c9f1b-3124-4911-8bdc-7dc4123a5ad3"}
```

`/api/close_position` additionally needs `ticket` and `position_identifier`;
`/api/approve_suggestion` needs `suggestion_id`. The response is
`confirmation_required`, a fixed captured scope summary, short `expires_at`, the
same request ID and one opaque 43-character `confirmation_token`. Second phase
submits exactly the same payload/ID with that returned token. The user must
explicitly confirm; front-end checkbox/countdown are UX only, server enforces it.
Never copy any genuine token/initData into examples, curl CLI arguments or logs.

## 3. Durable confirmation and effect journal

`OwnerActionStore` reuses schema 2 `OwnerApproval` and append-only `AuditLog`:

- `owner_action` is a challenge; `owner_action_run` is a deterministic owner+UUID
  effect reservation. Neither is a StageGate `live_session` approval.
- Only **nonce hashes** persist. Challenge binds owner/transport/credential hash,
  request ID, exact action/parameters and captured config/account/code/model/source/
  mode plus runtime/interface session. TTL **45s**, maximum **64** pending; expired
  challenges are terminal, cancellation/consumption one-way.
- A serialized SQL transaction consumes the challenge and commits a unique run
  reservation/audit **before** the remote effect. SQLite uses BEGIN IMMEDIATE;
  PostgreSQL uses the existing row-lock contract (server unvalidated here).
- Completion/rejection/uncertainty are append-only terminal outcomes. Completed
  same-payload keys return cached outcomes without another SDK call. Safety/account/
  transport/payload changes conflict. Historical terminal replay can span interface/
  runtime restart with unchanged safety identity, and is labelled
  `historical_runtime`; it never resumes the newly paused runtime or mints new proof.
- Pending/uncertain reservation cannot be resubmitted. Remote effect + SQL completion
  are not an atomic transaction. Cancellation waits unkillable SQL threads and
  records uncertainty; lost final persistence leaves an untouchable pending run.
  Never promise exactly once or blindly retry an unknown close.
- Upward/close/proposal actions serialize in the facade. **Pause/kill deliberately
  bypass that async action lock** so they cannot queue behind a slow native close;
  their durable SQL keys still prevent duplicated effects.

Resume carries the prepared `BotState.revision` into `RuntimeControl.resume` and
compares it **inside** the locked transaction. A later pause, kill, halt or heartbeat
cannot be erased by a stale confirmation. Reprepare on revision changes; do not
weaken this check just because a heartbeat made a confirmation stale. Existing
lease/risk/day-loss/drawdown/recovery/baseline/uncertainty gates still apply. Resume
never supplies staged/live approval, resets a latch, changes capital or opens a trade.

Owned capture reconciles in the explicit action-preparation path (not a GET), proves
actual account/magic/ticket/identifier/symbol/direction/volume against execution
ledger, and hashes exposure/TP/open time. An improved SL or floating P&L does not
invalidate a close; ticket/volume/TP/identity changes do. The execution authority
rechecks the capture hash at authorization AND final pre-send. Existing internal
callers passing no expected hash retain their legacy close semantics.

Close-all is **not atomic flatten**. It captures only currently proved bot-owned
positions; later/manual/other-account positions are never silently added. Confirm
pauses NEW entries, uses deterministic per-position close intent keys, stops at
rejection/partial/unknown, preserves already observed close outcomes, halts on
uncertainty and reports bounded remaining owned ledger/unsettled intents. Always
`broker_account_flat_claimed=false`. A pause/kill cannot recall an already submitted
SDK write; external account/market races still require reconciliation/server SL.

Proposal approve/reject calls `SuggestionStore.decide` only. Approval is NOT settings
application, live permission, model activation, restart, risk escalation or a close.
Its result explicitly says `settings_applied=false`, `trade_executed=false`. Applying
an approved change remains a separate stopped/flat trusted owner workflow; no such
route/command is exposed here.

## 4. Telegram commands and notifications

Actual commands: `/start`, `/help`, `/status`, `/dashboard`, `/positions`, `/trades`,
`/signals`, `/news`, `/suggestions`, `/settings`, `/logs`, `/pause`, `/resume`, `/kill`,
`/close TICKET POSITION_IDENTIFIER`, `/close_all`, `/approve SUGGESTION_ID`,
`/reject SUGGESTION_ID`. Private keyboards provide status/read controls, safe stop,
confirmed resume/close-all and a configured HTTPS WebApp button. Confirm/cancel
callback payloads fit Telegram's 64-byte bound. Menus register to the configured
owner's private chat only. Replies/headlines/model reasons use plain text, no HTML
or Markdown parse mode, bounded size, and never echo request/update/SDK exceptions.

`OwnerNewsNotifier` drains the Part 8 scoped expiring outbox in batches <=10. A SQL
`news.alert_delivery_attempted` claim commits **before** send; acknowledgement only
follows Telegram API success. A lost/timeout/canceled send is uncertain and **never
automatically resent**, including after restart or concurrent drainers. This trades
possible missed alerts for no blind duplicates; delivery is not exactly-once. Review
uncertain attempts manually. No scheduler invokes the notifier until Part 10.

## 5. HTTP/TLS/privacy controls

- One worker/process-local bounded minute limiter; separate ordinary and safe-stop
  actor and pre-auth IP budgets. Key churn cannot evict current-window limits.
  Default actor ordinary **60/min**, stop **10/min**, **2,048** keys. Edge limiting
  and a single deployment worker are still required; no distributed/DDoS claim.
- Request body default **16KiB**, headers **16KiB**, strict UTF-8 JSON, no duplicate
  security headers/JSON keys/NaN/compressed bodies, bounded chunked bodies. Fixed
  errors omit Pydantic inputs, raw auth, model/provider bodies and tracebacks.
- Explicit trusted hosts, no unrestricted `*`; exact configured HTTPS origins and
  same configured app origin, not open CORS/cookie credentials. Public HTTPS origin
  works behind an ASGI HTTP proxy without pretending forwarded host/client IP is
  trustworthy.
- HTTPS is required on protected API/webhook requests, except private loopback
  diagnostics with no public URL or the deliberate synthetic preview. A controlled
  proxy may attest `X-Forwarded-Proto: https` ONLY when its actual ASGI peer IP is in
  `API_TRUSTED_PROXY_IPS_JSON` (default loopback, <=8 exact IPs; no CIDR/wildcard).
  Run uvicorn with `proxy_headers=False`; forwarded client IP/host are never trusted.
  Bind backend to loopback and have the proxy overwrite the protocol header.
- CSP with same-origin code/styles/connect, official Telegram SDK only, restricted
  Telegram frame ancestors; no unsafe-inline/eval. No-store, no-referrer, nosniff,
  disabled device permissions, no cookies/auth logs. Only the isolated synthetic
  preview permits arbitrary framing for the workspace viewer.

A proxy/CDN can still log headers, URLs or reject large bodies before this app.
Disable request/auth body/header logging; do not use `$request_uri`, `$args` or
credential query URLs. TLS terminator/firewall/NTP/access-control/backup hardening
and independent audit are deployment work, not established by offline unit tests.

## 6. Run owner API without starting any trading

Keep defaults `DEMO_MODE=true`, `LIVE_TRADING=false`, `PAPER_TRADING=true`, startup
paused. Review `.env.example`, make `.env` private and configure actual Telegram
owner ID/token only yourself. No credential has been supplied in this session.

```powershell
Copy-Item .env.example .env
# Privately edit .env; add your exact HTTPS hostname, WebApp URL and owner/token.
.\.venv\Scripts\python.exe main.py check-config
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe -m scripts.run_owner_interface --env-file .env
```

Do not initialize/reset an existing runtime DB/history: use documented migration
and backups. API-only launcher verifies schema and attaches **no broker, bot polling,
news provider, AI or trading loop**. Reads and downward controls can be used by a
real verified owner; resume/close/proposal decisions require explicit initialized
matching runtime/store injection, and remain disabled without it.

Example `.env` public configuration (NOT credentials):

```dotenv
API_HOST=127.0.0.1
API_PORT=8000
API_TRUSTED_HOSTS_JSON=["reflex.your-domain.example"]
API_TRUSTED_PROXY_IPS_JSON=["127.0.0.1","::1"]
API_CORS_ORIGINS_JSON=[]
TELEGRAM_MINIAPP_URL=https://reflex.your-domain.example
TELEGRAM_USE_WEBHOOK=false
```

An illustrative controlled nginx TLS server location (review/install certificates,
edge limits and firewall separately; do not expose backend 8000):

```nginx
# Inside your HTTPS server{} for the one trusted app hostname:
location / {
    client_max_body_size 16k;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For "";
    proxy_buffering off;
    access_log off;
}
```

Configure BotFather's HTTPS menu/domain for this app and open it from the owner's
private bot. A normal browser/static HTML cannot invent owner identity. The
frontend's same-origin relative URLs never call browser `localhost` to reach MT5.
Actual Telegram delivery, owner menu launch, TLS proxy and native runtime have NOT
been exercised here. Part 10 will provide explicit composition/lifecycle/startup.

The implemented composition seam, not a broker-starting factory:

```python
# Only after a trusted composer has initialized these matching resources explicitly:
services = OwnerServices(database, settings, execution=engine, news=news_manager)
transport = TelegramOwnerTransport(services)
api = create_app(settings, services)  # polling mode: never auto-polls
# Webhook mode: create_app(settings, services, telegram_transport=transport)
# Composer separately starts polling OR explicitly registers the authenticated hook.
```

## 7. Deliberate UI preview and offline smoke

```bash
python -m scripts.preview_owner_ui --host 0.0.0.0 --port 8000
python -m scripts.smoke_owner_interface
pytest -q
```

Preview launcher **ignores dotenv AND inherited environment variables**, creates an
isolated temporary database, attaches no owner/broker/provider/Telegram, labels
all numbers/headlines/positions/proposals artificial, and denies **all mutations**.
Only this explicit mode mounts public `/preview/data`; it never supplies fake auth
for the production API. Preview host wildcard `*.e2b.app` is for this fixture only.
No frontend token/secret/local identity exists. UI has desktop sidebar, mobile
navigation, observed-equity chart, ledger, signals, calendar, proposals, read-only
settings/audit, and locked/expired/uncertain/proposal-only states. Untrusted text
uses `textContent`, never HTML interpolation. No build/CDN/npm runtime is required.

Owner smoke uses TEST_ONLY local HMAC, real ASGI routes/aiogram dispatcher and a
scripted Telegram session; nine assertions, one scripted Telegram reply and two
synthetic order operations (one open/one close), **zero actual Telegram/provider HTTP,
zero real orders/native SDK/stage eligibility**. It is NOT actual Telegram-issued
auth, a genuine feed, historical profitability or live permission.

## 8. Regression correction and release limits

The first full-suite run surfaced a pre-existing concurrent model artifact race:
exclusive creation of the final digest filename exposed zero/partial bytes before
fsync. `ai/artifacts.py` now flushes a private same-volume temporary and atomically
hard-links the final immutable name without overwrite. Concurrent writers read
fully published bytes; corrupt existing files are never replaced. Unsupported
filesystems fail closed. Linux/NTFS semantics are targeted; Windows is unvalidated.
This is a real regression correction, not a skipped/flaky-test workaround.

No schema change, capital/history reset, evidence rebinding, real Telegram/provider/
broker call, training promotion or live approval was performed. Source hash includes
owner backend/frontend and artifact changes; old code-bound evidence remains old.
See `VALIDATION.md` for actually executed working/clean/CRC/hash/source-guide and
browser results. PostgreSQL server/native Windows/real credentials/Telegram/tunnel/
webhook entitlements/true historical stages and independent security/vulnerability
review remain unvalidated. Pins/hashes/tests are not signatures, certification or
profit guarantees. Scheduling/deployment next; backtest→paper→demo→approved small
live remains mandatory.
