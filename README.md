# MT5 AI ReflexBot

Selective, owner-controlled MT5 trading automation. **No profit guarantee.**

## Current release: Parts 1–15 (0.13.0 · schema 2)

Implemented: settings/database/logging/operator CLI, serialized Windows MT5 and
mock/paper adapters, cost-aware sizing, durable risk/owner/stage/ownership/trailing,
causal signals, strict async AI supervision/proposals, CPU logistic/LightGBM and
purged evaluation/immutable registry, bounded news/RSS/complete-range calendar,
owner-only aiogram 3 commands, authenticated FastAPI Mini App and mobile frontend,
**actual runtime composition/lifecycle, bounded APScheduler jobs, 30-second watchdog,
private health, durable notifications, SQLite backups and Windows deployment scripts**,
plus **strict causal historical tick/OHLC replay, archived news/AI timing, shared core execution,
cost-inclusive metrics, signed-owner stage review and operator playbooks**,
and **read-only current-release integrity, file-only configuration, distribution metadata
and optional in-memory SQLite readiness diagnostics**, plus **one frozen pre-replay
ML artifact/corpus, exact purged reconstruction, private research-only registry import
and actual ML approval/execution revalidation**.
Completed research runs additionally publish an exact input/output/private-ledger
bundle closure for bounded read-only metric/decision/SQLite-memory audits.
**Part 15** adds working deployment preflight checks, **Groq (OpenAI-compatible) strict
JSON-schema mode**, a **rule-based fallback when the AI provider is unavailable**, **Exness
cent accounts (USC/EUC)**, **dynamic multi-symbol discovery** (broker suffixes, contract specs,
per-instrument spread caps via `python -m scripts.resolve_symbols`), plus a reproducible
`docs/RELEASE_15_MANIFEST.json` (see `docs/PART_15_NOTES.md`). 3-day paper test runbook:
`docs/THREE_DAY_PAPER_TEST.md`.
**AI-FIRST upgrade**: the Groq AI (`qwen/qwen3.8-27b` for fast decisions,
`openai/gpt-oss-120b` for nightly deep review) is consulted before every entry and after every
profit lock. The pieces:
- a market awareness engine
- strict JSON decisions with a confidence threshold
- a technical-score fallback and a 3-failure circuit breaker
- a decision journal
- a bounded config adjuster: minor changes auto-apply, majors need `/approve`, `/ai_reset` reverts
- a learning loop
- Telegram AI notifications and `/ai`, plus the Mini App **AI** tab
- **lock-first AI-adaptive trailing**: the mechanical 30/60/90 lock always comes first, then the AI
  may hold, close, tighten or extend, never loosen

Details: `docs/AI_FIRST_ARCHITECTURE.md` and `docs/TRAILING_AI_DESIGN.md`.
**2,762 tests pass with no file skipped** (Part 15 recorded 2,683; Part 14 2,528).
Lint/compile and the offline smokes pass.
Actual Linux scope and remaining native/provenance limits are in `docs/VALIDATION.md`.
See `docs/VALIDATION.md` for executed checks and limitations.
Production-oriented code is not profitability proof or audited/native-deployment readiness.

`main.py` stays a non-trading operator CLI. `python -m app.bot --env-file .env`
is an **explicit daemon start**, requiring an existing DB and reviewed source-root
.env. It restores/reconciles and starts PAUSED; protection continues while paused.
`python watchdog.py --env-file .env` explicitly supervises that guarded child.
An alive/stale native child is never force-killed/replaced; no order replay,
capital/latch reset, automatic owner resume or live approval occurs.
`python -m scripts.run_owner_interface` remains API ONLY, without broker/daemon startup.
Approval is a decision ONLY, never settings application/model selection/trading permission.
Historical replay/metrics/usage are implemented in **Part 11**. Replay outputs remain
research-only; enabled-ML approvals require a verified causal frozen model/corpus in a
new private BACKTEST ledger, not a production registry activation or genuine owner approval.

Native adapters default **DenyAllWrites**. Every actual write requires durable
owner/risk/stage/account/source/expiry/reconciliation gates. Nothing here supplies
genuine feed coverage, native demo/live evidence or live approval. Diagnostics used
TEST_ONLY synthetic markets/transports, not genuine owner credentials or orders.
No actual deployment, genuine Telegram/provider/broker request or real order was made.

`docs/PART_10_NOTES.md` covers lifecycle/scheduler/watchdog/backup contracts;
`DEPLOYMENT_WINDOWS.md` full operator steps; **`PART_15_NOTES.md`** covers Part 15; **`PART_14.md`** includes complete literal
current code/configuration/tests plus HTML/CSS/JS/VBS/PowerShell. `BACKTESTING.md` and
`OPERATOR_PLAYBOOK.md` give exact replay and stage-review workflows. `CURRENT_TREE.txt`
is the actual packaged tree. Earlier guides/manifests are historical snapshots, not
current-source manifests. Part 9's historical manifest had stale part/test metadata,
not damaged source hashes; the historical Part 10 manifest recorded the correction, retained in Part 11 metadata.
`PART_09_NOTES.md` covers auth/API/TLS/owner actions, `PART_08_NOTES.md` news,
`PART_07_NOTES.md` AI/learning, `PART_06_NOTES.md` causal signals and `MIGRATIONS.md`
preserved state. Current `.env.example` and ordinary files are authoritative.

## AI-FIRST quick test (mock broker / paper engine, offline)

```
python -m pytest -q tests/test_ai_first.py tests/test_ai_first_runtime.py tests/test_ai_adaptive_trailing.py
python -m scripts.smoke_ai_first                  # scripted AI: entry, config policy, 30→hold→60→close_now
python -m scripts.smoke_ai_first --provider rule  # AI outage: rule mode, circuit, mechanical trailing
```

No broker connection and no real order. The optional `--provider groq` makes a real Groq request
using `OPENAI_API_KEY` from the environment, still on the mock broker.

## Read-only offline readiness (no connection/deployment/trading permission)

```bash
python -B -m scripts.verify_release
python -B -m scripts.readiness --env-file .env.example
python -B -m scripts.smoke_readiness  # writes disposable TEST_ONLY fixtures only
```

Integrity is checked before any explicit configuration/state inspection. Every declared
SHA/length and the complete runnable/config file set are verified; optional trusted
manifest SHA is an external anchor, not a signature. File-only Settings ignores inherited
credentials and reports runtime override mismatch. No package/SDK/provider import or
pip/subprocess/network is used by distribution metadata checks. Optional SQLite inspection
queries only a MEMORY COPY and refuses pending WAL/journal, without opening/mutating the
original ledger. Windows/session/ACL/feed/owner unknowns remain blocked, not invented.
`offline_checks_passed` is NEVER native qualification, stage evidence or live approval.
See `docs/READINESS.md` and `docs/NATIVE_VALIDATION_CHECKLIST.md`.

## Historical replay quick start (no credentials/native startup)

```bash
python -m scripts.smoke_backtest
python -m scripts.make_backtest_fixture --output data/fixtures/replay_001 --replay-minutes 2
python -m scripts.backtest --manifest data/fixtures/replay_001/manifest.json --output data/backtests/analysis_001
# OPTIONAL artificial PRIVATE simulated entry/close; no real provider/broker/owner call:
python -m scripts.backtest --manifest data/fixtures/replay_001/manifest.json --output data/backtests/simulation_001 --review-mode synthetic_research --simulate-orders --close-at-end
```

Default reviewer=veto, zero-trade results valid; no forced six/day or favorable path
invented. Tick freshness/closed M1→higher frames, daily/aggregate risk, owner/idempotency/
ownership, fees/gaps/trailing all use the ordinary core. Historical != MT5; OHLC has
stop-first and intrabar uncertainty. A NEW output directory is required. Real history
needs licensed input/contract/provenance and genuine causal AI/news archives; none supplied.
Reports are **not promotion evidence**. `scripts.stage_report` uses native ledger proofs,
fresh signed owner identity and literal artifact confirmation, never raw owner IDs.
See `docs/BACKTESTING.md`, `docs/OPERATOR_PLAYBOOK.md` and `docs/PART_11_NOTES.md`.

## Frozen ML replay (strictly offline / artificial example)

```bash
python -m scripts.smoke_replay_model
python -m scripts.make_backtest_model_fixture --output data/fixtures/ARTIFICIAL_ml_001
python -m scripts.verify_replay_model --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env
python -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env --output data/backtests/ARTIFICIAL_ml_analysis_001
# OPTIONAL private artificial approval/entry/close, NEVER a production activation:
python -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env --output data/backtests/ARTIFICIAL_ml_sim_001 --review-mode synthetic_research --simulate-orders --close-at-end
```

Enabled ML is not disabled to make replay trade: exact frozen model/corpus/source/code/
policy/schema/label/selection/embargo bindings and reconstructed real purged evaluation
are mandatory. Actual inference is rechecked at both finalization and pre-send risk.
One fixed model only; no midrun refit, selection, guessed probability or fallback. Missing
ML input still permits veto-only analysis, not approvals. The generator's deliberately
engineered labels/probabilities are **not economic skill or actual historical availability**.
All outputs remain promotion-ineligible. See `docs/ML_REPLAY.md` and `PART_13_NOTES.md`.

## Completed replay bundle audit (read-only / research only)

```bash
python -m scripts.smoke_bundle_audit
# Explicit completed NEW private output from scripts.backtest, NOT your production ledger:
python -B -m scripts.audit_backtest --run data/backtests/ARTIFICIAL_ml_sim_001
```

Every current completed replay seals exact raw input/output/checkpoint/private-SQLite
hashes AFTER successful shutdown. Auditor recomputes research metrics/bindings and queries
only a captured private MEMORY SQL image; no original DB SQL connection, Settings/credentials,
repair, re-seal, owner/model/stage activation or trading permission. Old unsealed outputs
refuse; zero trades remains valid. Logistic observations are numerically cross-checked;
LightGBM foreign inference and training are NOT rerun. See `docs/REPLAY_AUDIT.md`.

## Explicit runtime quick start (review first; always PAUSED)

```powershell
# Existing reviewed source-root .env + initialized persistent DB required.
.\.venv\Scripts\python.exe -m scripts.smoke_runtime  # isolated offline TEST_ONLY
.\.venv\Scripts\python.exe -m app.bot --env-file .env
# Or supervisor, NOT a second concurrent runtime:
.\.venv\Scripts\python.exe watchdog.py --env-file .env
# Separate shell: diagnostic then persistent downward stop, no process kill.
.\.venv\Scripts\python.exe -m scripts.runtime_status --env-file .env
.\.venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env
```

Default mock/paper, empty credentials, unknown news, no owner resume. No trading or
Stage progression is authorized by extracting/running diagnostics. Local stop
requests persist across logon; clearing one does NOT clear kill/loss/recovery or
resume. Watchdog default budget is 3 launches/rolling hour including first/failed
starts. No blind retry of broker effects or uncertain notifications.

## Owner interface quick start (no trading auto-start)

```powershell
# Review .env privately; configure your actual owner ID/token and HTTPS app hostname.
Copy-Item .env.example .env
.\.venv\Scripts\python.exe main.py check-config
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe -m scripts.run_owner_interface --env-file .env
```

Never initialize/reset existing history to bypass failed gates. The API launcher
verifies an existing schema and binds loopback by default, one worker, no access
logging/forwarded-IP trust. Use a controlled HTTPS proxy, exact trusted hostname,
`TELEGRAM_MINIAPP_URL` and `API_TRUSTED_PROXY_IPS_JSON`; have the proxy overwrite
`X-Forwarded-Proto`. No public raw backend port. Owner API needs signed initData
from a private Telegram launch, never a developer/browser login fallback.
See Part 9 notes for the exact protected GET/POST route table and setup limits.
Actual composed lifecycle and deployment are in Part 10; see its notes and Windows guide.

Deliberate **synthetic read-only** preview, independent of your .env/inherited flags:

```bash
python -m scripts.preview_owner_ui --host 0.0.0.0 --port 8000
python -m scripts.smoke_owner_interface
```

Every figure/headline/position/proposal is artificial; preview never authenticates
an owner or trades and denies every mutation. Production never mounts its public
fixture route. Dashboard GETs never poll broker/providers or apply proposals.
Pause/kill stop NEW entries (not flatten/recall); resume and owned closes require
fresh single-use confirmation. Close-all captures owned scope and reports residue,
never promises atomic account flatten. Proposal approval != application.

## Safety defaults

- `DEMO_MODE=true`, `LIVE_TRADING=false`, `PAPER_TRADING=true`.
- `MT5_BACKEND=mock`, `START_PAUSED=true`; no broker credentials required.
- 0.5% maximum nominal stop risk/entry; initial live cap 0.1%.
- 1.5% total nominal open risk; 3% daily equity-loss cap; 10% peak drawdown cap.
- 12 maximum entries/day, 3 simultaneous positions, one position per symbol.
- Six entries/day is a target only. No forced entries, martingale or averaging.
- SL and promotion gates cannot be disabled. Every restart is paused.
- Default $5 profit objective must pass net reward/risk >=1.1 and costs.
- No configured RSS/calendar coverage means unknown news risk, not safe.
- Kill switch/SL cannot guarantee a fill during gaps or disconnections.

## Windows local-PC setup (working foundation commands)

1. Install **Python 3.11 x64**. Enable the PATH option. Verify `py -3.11 --version`.
   Use x64 Python; the native MT5 wheel is Windows x64. Python 3.12+ is possible
   only after validating the broker package/dependencies on that version.
2. Install your broker's official MT5 terminal (Exness or another broker), not
   MT4. Launch it interactively and log into a **demo account**. Verify its server,
   actual account type and the available symbol names/suffixes.
3. Enable terminal Algo Trading. Under Expert Advisors options, ensure external
   Python API trading is **not disabled**. This is needed for later demo orders,
   not for read-only inspection. Check broker symbol trading hours/permissions.
4. Extract this repository to a local directory, for example
   `C:\Trading\mt5_ai_reflex_bot`, or clone your own private copy. No remote
   repository URL is invented or provided.
5. In PowerShell:

```powershell
Set-Location C:\Trading\mt5_ai_reflex_bot
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

Activation is optional; using the explicit venv executable avoids PowerShell
activation-policy changes. Run under your normal Windows account, not admin.

6. Edit `.env` locally. Keep paper/mock flags unchanged for this installment.
   Set `MT5_TERMINAL_PATH` to the real `terminal64.exe` path. Leave login,
   password and server ALL blank to attach to an already-authenticated terminal,
   or configure ALL three for explicit login. Never paste credentials into chat.
7. Create a bot using Telegram **@BotFather**. Put its token in
   `TELEGRAM_BOT_TOKEN` and your numeric user ID in `TELEGRAM_OWNER_ID`; configure
   both together. Configure the HTTPS Mini App URL/hosts only after reviewing Part 9 notes. Telegram
   controls are not installed in this release.
8. Validate and initialize:

```powershell
.\.venv\Scripts\python.exe main.py check-config
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe -m pytest -q
```

Expected status: `mode: paper`, `state: paused`, `heartbeat: null`. Null is
intentional: there is no running trading loop. `init-db` is idempotent but never
resets existing kill state or silently migrates an incompatible schema.
For a prior schema-1 installation, stop all runtimes and follow
`docs/MIGRATIONS.md`; only then run the explicit `main.py migrate-db` command.
A fresh install does not need that command. The CLI never connects a broker.

9. Keep `.env`, data, backups and logs readable only by your Windows user. Example
   ACL tightening after creating `.env` (inspect the resulting ACLs):

```powershell
$Principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .env /inheritance:r /grant:r "${Principal}:(R,W)"
icacls data /inheritance:r /grant:r "${Principal}:(OI)(CI)F" /T
```

ACL rules depend on your organization; do not lock yourself out. Use full-disk
protection where available. Add exclusions for sensitive paths in backups/shares.

## Synthetic diagnostics and read-only inspection

```powershell
# Pure synthetic execution: no terminal, credentials, providers or network.
.\.venv\Scripts\python.exe -m scripts.smoke_mock
# Isolated durable risk / pause / trailing / restart / kill / close test.
.\.venv\Scripts\python.exe -m scripts.smoke_risk
# Full synthetic signals/reviews/risk/fill/duplicate/TP/fee-ledger regression.
.\.venv\Scripts\python.exe -m scripts.smoke_signals
# OFFLINE scripted HTTP → signal/risk/label → actual toy ML/registry/owner projection.
.\.venv\Scripts\python.exe -m scripts.smoke_ai

# Explicit local WINDOWS read-only broker-data inspection; may launch/login MT5.
# Configure .env locally first; this forces paper mode and never sends orders.
.\.venv\Scripts\python.exe -m scripts.check_mt5_readonly --env-file .env
```

The mock smoke ignores host mode/credentials, makes one deliberately engineered
artificial trade, verifies duplicate handling and complete deal/balance accounting,
and labels its results **ineligible for promotion**. It is not a backtest or a
profitability demonstration. The read-only diagnostic is not live authorization.

Never install a test write authority into runtime code. Mock contract tables are
not broker specifications; native sizing uses the actual broker's valuation.
Timeouts/cancellation/partial/accepted/unknown acknowledgements quarantine writes;
reconnect does not clear the latch or justify resubmission. Python cannot safely
kill a hung native worker. Keep server-side protection and reconcile durable
intents before owner-controlled recovery.

Managed paper execution now checkpoints every mutation automatically before SQL
acknowledgement, including read-triggered exits. Missing/corrupt state with a prior
DB footprint cannot reset capital. Owner/stage/risk services, trailing and the strategy/signal publisher are
implemented. News/calendar adapters are now implemented with strict review, source,
UTC and latest-epoch proof, but real coverage/market evaluation remains unvalidated.
Scheduler and authenticated owner transport arrive later. Tests use only scripted
offline transports. Do not operate this library unattended/native yet.
See `docs/PART_05_NOTES.md` for exact ownership, uncertainty and achievable-lock rules.

## Part 6 strategy defaults and provider boundary

`STRATEGY_WEIGHTS_JSON` defaults to trend .30, mean-reversion .25, breakout .20,
momentum .25. All four keys are required, total exactly 1; zero disables a rule.
At least two qualifying votes, .50 coverage, .80 agreement and 70 technical score
are required. Scores are heuristics, not probabilities or performance guarantees.
ATR/spread/gap/quote/price-extension filters veto independently; six entries/day
remains a target only. Indicator histories need 200 real finalized bars per frame;
M15/H1 align to M5 **close**, never their opening/future data.

`SignalEngine.evaluate()` without actual bound AI/news reviews rejects. A quality
approved Signal still cannot auto-resume or execute; `execute_signal(id)` explicitly
invokes the durable risk/owner/stage checks. The diagnostic's scripted confidence
and independently engineered timeframe histories are NEVER stage evidence.

Schema remains 2, but new strategy policy/code changes hashes: existing approvals
are invalid and an old config-bound paper checkpoint may refuse restoration.
Keep matched code/config/DB/checkpoint; do not erase capital or edit a hash to
bypass validation. See the Part 6 compatibility section in `docs/MIGRATIONS.md`.

## Part 7 AI/learning safety boundary

Ollama and OpenAI-compatible adapters (including **Groq**: `OPENAI_BASE_URL=https://api.groq.com/openai/v1`,
`OPENAI_MODEL=qwen/qwen3.8-27b` or `openai/gpt-oss-20b|120b`, `OPENAI_RESPONSE_FORMAT=json_schema_strict`)
are async, fixed-origin and bounded. Provider fallback is for availability only, never to shop
past a valid reject/WAIT/low confidence. Strict schema/hash/freshness checks reject untrusted replies.

**Rule-based fallback (Part 15, `AI_RULE_FALLBACK_ENABLED=true`):** when the AI times out, is
rate-limited (429), down (5xx), unconfigured or returns invalid JSON, the persisted technical
signal score decides instead of blocking every entry. It approves only at
`AI_RULE_FALLBACK_MIN_SCORE` (default 80, validated ≥ AI/signal thresholds); its confidence is
bound to that exact score and rechecked at finalization and pre-send. A valid AI veto, an
unbound reply that signals a veto, `AI_PROVIDER=disabled`, unsafe news, an ML-filter veto and
BACKTEST never use it; LIVE needs `AI_RULE_FALLBACK_ALLOW_LIVE=true`. Risk/stage/owner/SL gates
are unchanged and the bot still starts PAUSED.
Prompt injection cannot be guaranteed away; deterministic risk/news/owner controls
remain independent. No model output is an order or a live permission.

Learning uses only immutable original features and positively reconciled closed
USD fee-inclusive outcomes. Five expanding timestamp-grouped folds purge unavailable/
overlapping labels and embargo by time; neither shuffled CV nor future preprocessing
is used. Logistic and LightGBM are actual CPU training options. Reports describe
selected-trade classification, **not market backtests or promotion evidence**.
Artifacts never use pickle/joblib loading and register INACTIVE. Owner selection/
rollback requires stopped flat state; synthetic models cannot promote to native-data
paper/demo/live. `MODEL_FILTER_ENABLED=false` by default. An enabled matching model
can only veto, never boost risk/confidence. Remote LLM weights are not attested by
the local classifier/baseline SHA.

AI changes become immutable pending owner proposals. Stopped owner `apply` returns
new validated frozen Settings and records a projection; it never edits `.env`,
resumes, resets capital/checkpoints or clears latches. `AUTO_ADAPT_STRATEGY_WEIGHTS`
enables bounded proposals only, not unapproved active changes. Prior config/code/
model approvals are invalid after changes. See `PART_07_NOTES.md` and `MIGRATIONS.md`.

## Part 8 news/calendar safety boundary

RSS/Atom, NewsAPI, Finnhub and CryptoPanic v2 clients are real bounded async GET
adapters, not hard-coded safe flags. Keys alone enable no API. Empty scopes,
delayed/development access, empty/malformed feeds, missing publication timestamps,
stale/quiet sources or incomplete calendars withhold proof. RSS is NOT a calendar.
Production entitlement/realtime/scope must be honestly reviewed by the owner;
provider/plan/source labels are not independent authenticity or complete world-news proof.

A per-symbol window binds current policy/code/market source, immutable snapshot and
committed publication epoch. Its expiry covers fetch/cadence/calendar issue/horizon
and the next high-impact pre-event boundary. Signal review/finalization and durable
authorization/pre-send recheck the latest SQL epoch; GBP proof cannot authorize EUR,
old green cannot survive a new publication, and fixtures cannot authorize MT5-data
paper/demo/live. Source file SHA is rechecked before reuse. Calendar rereads/304 and
syndication never refresh issued/publication/first-seen times. New severe content
gets separate causal risk-observation time; unchanged content does not reset it.

Sentiment is advisory, never a trade side/risk authority. Deterministic blocks
survive positive/AI sentiment. Pending sanitized alerts are **not delivered** yet;
Part 9 supplies a staged owner sender with no blind retries/exactly-once promise. No automatic resume/order.
Unknown/high news blocks new entries/TP extension, never improved SL or closure.

```powershell
# Isolated SCRIPTED HTTP/HEADLINES/CALENDAR; zero real/simulated orders/network/SDK.
.\.venv\Scripts\python.exe -m scripts.smoke_news
# Configured identifiers/review scopes only; zero HTTP/credentials/broker connection.
.\.venv\Scripts\python.exe -m scripts.inspect_news_sources
```

Read `docs/PART_08_NOTES.md` before enabling a source. There is no entitled calendar
seed or undocumented website scraper. File/calendar origins must be reviewed;
missing/partial/timezone/completeness/interval evidence stays unknown. DNS/egress,
license/quotas/genuine latency, data completeness and deployment need verification.

## Mode selection (configuration validation, not trading authorization)

| Mode | BACKTEST_MODE | DEMO_MODE | PAPER_TRADING | LIVE_TRADING | MT5_BACKEND |
|---|---|---|---|---|---|
| Backtest | true | true | false | false | mock |
| Paper with mock data | false | true | true | false | mock |
| Paper with broker data | false | true | true | false | real |
| Broker demo | false | true | false | false | real |
| Broker live | false | false | false | true | real |

Broker execution is NOT enabled by this table alone. The durable authority verifies
actual account type/identity, staged evidence, protection, risk and an owner
approval. No Mini App setting will directly toggle live order permission.

## Operational progression (runtime delivered; historical backtester Part 11)

Parts 1–10 are delivered; Part 11 historical backtester/stage-report usage is still future-only:

1. **Part 4–6**: configure real symbol aliases, points/tick sizes, lot steps,
   commission, swap and currency conversion. Test the MT5 adapter read-only.
   Keep `PAPER_TRADING=true`; a real data backend still uses simulated execution.
2. **Part 7–8**: run Ollama with the configured model or an explicitly configured
   cloud provider (Groq via the OpenAI-compatible adapter). Provider failure uses the
   bounded rule-based fallback (technical score only, see Part 7 section); no fabricated AI confidence.
   Install reliable, entitled RSS/API sources and economic-calendar coverage.
   Check feed timestamps, UTC coverage horizon and high-impact windows.
3. **Part 9**: host the Mini App with HTTPS through a reverse proxy or tunnel.
   Do not forward raw port 8000 publicly. Add the exact public host to
   `API_TRUSTED_HOSTS_JSON`, put the HTTPS app URL in `TELEGRAM_MINIAPP_URL`,
   configure BotFather's menu button and open it INSIDE Telegram. Static HTML
   opened alone is not an authenticated trading panel. Test owner access and
   confirm every other user and forged/expired initData receives a denial.
   No bot token or initData may appear in the app URL; the frontend sends
   authenticated headers to same-origin relative API routes.
4. **Part 10**: run `python -m app.bot --env-file .env`, inspect local `scripts.runtime_status` and `/healthz`, then
   test `/status`, `/positions`, `/pause`, `/resume`, confirmations and kill switch.
   Test protective monitoring while entries are paused. Use polling OR webhooks.
5. **Part 11**: chronological cost-aware backtest, then >=14-day paper stage,
   then >=14-day broker-demo stage, each with sufficient trades and reviewed
   metrics. Persist hashed evidence for the same strategy/code/model. Mock-only
   reports cannot qualify as real demo/live evidence. Failed gates mean no
   promotion; do not edit the database to manufacture success.
6. `scripts/run_mt5_background.vbs` explicitly opens MT5 minimized only in real
   backend mode. `scripts/run_bot_hidden.vbs` launches that helper and the guarded
   supervisor hidden. Install the logon task with `scripts/task_scheduler_setup.ps1`;
   its logon type must
   be **Interactive**, and 'run whether user is logged on or not' must be OFF.
   Hidden/minimized is not a logout-proof Windows service.
7. Disable sleep while on AC power, not Windows security lock:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

   Locking the screen preserves the session; signing out does not. Schedule
   Windows maintenance/patching outside open-trade windows; reconnect and review
   state after every restart. Maintain time synchronization, power and internet.
8. Small live only after explicit owner confirmation. Set live flags manually,
   restart paused, verify the REAL account/server, inspect evidence, and approve
   the exact account/config/session with an expiry. Start at the 0.1% cap. Risk
   increases/new models require review and renewed validation. Expiring approval
   stops entries but must not prevent safe protective modification/closure.

Deployment scripts and the separate `app.bot` runtime are implemented. `main.py`
intentionally has no trading `run` command. See Part 10 for clean shutdown/backups,
interactive logon-only task setup, uncertainty and native/compiled validation limits.

## Optional Windows VPS

Use a **Windows** VPS with an interactive user session for native MT5. Install
x64 Python and the official broker terminal as above. Restrict RDP through a VPN
or IP allowlist, enable updates/2FA where available, and do not share credentials.
Use logon-triggered interactive tasks, a TLS proxy for the Mini App, and firewall
rules denying inbound API/database ports. RDP disconnect normally leaves the
session running; user logoff ends this supported environment. Session policy
must be verified on your provider. Never auto-resume live trading after reboot.
A Linux VPS can host a separately secured UI/backend, but cannot run this native
MT5 Python integration; a remote broker bridge is outside this implementation.

## Linux/macOS development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python main.py check-config
.venv/bin/python main.py init-db
.venv/bin/python -m pytest -q
```

The platform marker skips MetaTrader5. The native adapter imports it lazily only
on Windows. MockMT5Client is implemented for synthetic simulation, not a bridge.
Run `.venv/bin/python -m scripts.smoke_mock`, `-m scripts.smoke_risk` and
`-m scripts.smoke_signals` and `-m scripts.smoke_ai` for isolated deterministic checks. None consumes
host credentials or qualifies
as a strategy evaluation, real-market paper stage or trading authorization.

## Database, logs and dependency reproducibility

SQLite uses WAL, FULL synchronous writes, foreign keys, busy timeout, exact
Decimal-text financial columns and UTC conversion. Aggregate money in Python
Decimal, not SQL SUM(text). Sessions are per operation/thread. Audit changes
roll back with their business transaction. SQLite triggers block audit deletion
and updates; administrators can still modify database files.

PostgreSQL is optional: install `psycopg[binary]==3.3.6`, configure a dedicated
`postgresql+psycopg://...` database, initialize with a migration/admin role, then
use a restricted runtime role. Grant only SELECT/INSERT on audit_logs; grant
needed DML on the other tables and needed sequence access. Do not use a broker
machine's administrator or database superuser credentials in `.env`.
PostgreSQL integration still requires a real-server test before deployment.

Fresh installations create schema 2. An existing schema 1 fails closed until an
explicit stopped-runtime `main.py migrate-db` upgrade. That command verifies old
columns, checks leases/unresolved intents, creates a consistent SQLite backup,
checks integrity, preserves latches and marks old risk baselines unverified.
Read `docs/MIGRATIONS.md` first; it also gives a reviewed PostgreSQL procedure.
Keep DB and matching paper checkpoint together. Do not copy a live `.db` while
omitting its WAL or reset a ledger to manufacture results. Backups/restore and
runtime-role permissions must be verified on the deployment platform.

Direct dependencies are pinned. Freeze the successfully tested deployment
venv on each platform separately, review for private index URLs, and audit it:

```powershell
.\.venv\Scripts\python.exe -m pip freeze > requirements.windows.lock.txt
.\.venv\Scripts\python.exe -m pip install pip-audit==2.10.1
.\.venv\Scripts\python.exe -m pip_audit -r requirements.windows.lock.txt
```

Resolve advisories and rerun tests before network exposure. Pins are not a
security certification; transitive versions must be locked per deployment.
Only known-secret and pattern redaction is guaranteed by tests. Never add code
that logs raw auth data, passwords, provider bodies or complete settings.

## Validation scope

See `docs/VALIDATION.md` for actual full working/clean test counts, Ruff,
compileall, dependency consistency, explicit SQLite migration, CLI and synthetic
risk/trailing/restart and complete signal mock flow. Native tests use
marked fake SDKs only. No Windows terminal, broker demo/live orders, genuine-history strategy
qualification, genuine AI/news providers or real Telegram integration were exercised.
Part 11 replay tests and smoke use expressly artificial prices/archives only. No live
permission has been granted and no real broker orders have been sent.
