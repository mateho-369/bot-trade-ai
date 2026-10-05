# MT5 AI ReflexBot — architecture and safety contract

## Implementation status

Current release: **Parts 1–10 (0.8.0 / schema 2)**. Settings/database/logging,
operator CLI, broker/paper adapters, execution/risk/owner/stage/ownership/trailing,
causal signals, async AI/proposals/CPU learning/evaluation/registry, bounded
news/calendar/expiry proof, owner aiogram commands and authenticated FastAPI Mini
App routes/SQL projections/mobile frontend/durable action confirmations are implemented.
Part 10 adds real shared composition, safe lifecycle, APScheduler, private health,
watchdog, durable notices, SQLite backup and Windows/VPS scripts.
See VALIDATION.md for actual working/clean suite results and browser checks.
Native adapters default DenyAllWrites; composition/owner resume is not live permission.
Assembled lifecycle/scheduler/watchdog/deployment are implemented; Part 11 supplies backtester.
No actual Windows/broker/provider/Telegram/TLS-proxy/PostgreSQL integration, real
orders, deployment, independent audit or live approval. All diagnostics are fixtures.
See PART_09_NOTES.md, PART_09.md, PART_08_NOTES.md and MIGRATIONS.md.

## Goal

Evaluate multiple configured symbols on closed M5 bars with M15/H1 context.
Prefer selective, cost-aware opportunities, server-side protection and small
configurable targets. Six entries per day is an informational target; twelve
is the default hard entry cap. Zero trades is a valid day. AI is a veto/advisory
layer, not an execution authority. No profit, win rate, fill price or maximum
realized loss is guaranteed.

## Explicit professional assumptions

1. **Windows x64 and Python 3.11 x64** are the reference broker deployment.
   Linux/macOS development uses a deterministic mock. MT5 runs in an interactive
   logged-on Windows session, not a session-0 Windows service.
2. Default flags select **paper + mock**, with **startup paused**. Choosing a
   real data backend does not authorize real orders. Demo execution additionally
   requires MT5's actual account trade mode to be DEMO; contest/live accounts
   cannot masquerade as demo. Live requires actual REAL account mode, explicit
   live flags, staged evidence and an expiring owner approval.
3. Risk per trade defaults to 0.5% of conservative capital
   (`max(0, min(balance, equity-credit))`), **0.1% maximum for initial live
   operation**, 1.5% aggregate open risk, 3% daily equity loss and 10% peak-equity
   drawdown. These are entry/exit policy thresholds, not guarantees against gaps.
   The code-level per-entry ceiling is 1%; raising it requires code review and
   revalidation, not an AI suggestion.
4. One owned position per resolved broker symbol; no averaging, martingale,
   grids or recovery sizing. Unprotected or unaccounted-for foreign positions
   block new risk. Emergency closure defaults to bot-owned positions only.
5. All cash amounts distinguish **account currency** from **USD**. MT5
   `order_calc_profit` / `order_calc_margin` are the preferred broker valuation
   primitives. Validated conversion quotes are required for non-USD accounts;
   never silently relabel EUR/JPY/USC profit as dollars.
6. Profit targets are preferences. Default net reward/risk floor is 1.1. A
   requested $5 target must still cover costs and the stop risk; lot size is
   rounded DOWN, or the setup is skipped. 0.1R–0.5R targets are configurable only
   by explicitly lowering the owner's reward/risk floor; that knowingly permits
   individual losses larger than wins and requires higher net win rates. It
   cannot simultaneously guarantee 'losses smaller than wins'.
7. News and calendar data are separate. Missing/stale coverage is **unknown**,
   not low risk. RSS headlines alone cannot prove a safe economic-event window.
   The default empty feed/calendar configuration will block new entries when
   the trading engine is installed. No scraper is assumed to be reliable or
   entitled to access ForexFactory/Investing.com feeds.
8. AI unavailability, invalid JSON, an expired tick, missing features, unknown
   risk exposure, database failure or an uncertain order acknowledgement blocks
   new entries. AI confidence is a score, not a calibrated success probability.
9. Every restart starts paused. A persisted kill/drawdown latch is never cleared
   by a watchdog. Pausing entries does NOT disable protective position management.
10. Backtest -> paper -> demo -> small live is mandatory. Reports are bound to
    code/model/data hashes and a mode-independent strategy policy hash. Live
    approvals additionally bind the full configuration, actual broker account,
    process session, owner and expiry. No environment flag counts as approval.
    Default paper/demo gates each require at least 14 days and 100 closed trades,
    profit factor at least the configured floor and drawdown within the stage cap.
    Those are necessary checks, not proof of future profitability.
11. Stop loss, kill switch and flattening cannot guarantee a fill during a closed
    market, disconnection, gap or broker rejection. Keep native SL/TP on the server.
12. The bot never withdraws/transfers funds, changes broker passwords, executes
    AI-generated code, or grants the model shell/filesystem/trading capabilities.

## Mode matrix

| Mode | BACKTEST_MODE | DEMO_MODE | PAPER_TRADING | LIVE_TRADING | MT5_BACKEND |
|---|---|---|---|---|---|
| Backtest | true | true | false | false | mock |
| Paper, synthetic development data | false | true | true | false | mock |
| Paper, real MT5 market data | false | true | true | false | real |
| Broker demo | false | true | false | false | real |
| Broker live | false | false | false | true | real |

Invalid/ambiguous combinations raise a configuration error. Configuring a mode
is distinct from having permission to submit an order in that mode.

## Component/data flow

```text
Owner Telegram chat / Telegram WebApp
        | strict owner identity, WebApp HMAC + auth_date, request limits
        v
aiogram handlers + FastAPI API (one shared application runtime)
        | services, transactional approvals, no direct order_send route
        v
Persistent BotState / RiskState / audit ledger / stage evidence
        |
APScheduler -> finalized bars + fresh bid/ask + as-of calendar/news
        |
FeatureEngineering -> strategies -> StrategyRouter -> SignalEngine
        |                        technical score 0..100
        v
AI Supervisor (Ollama -> configured cloud fallback, strict JSON)
        |                        veto / bounded risk reduction / proposal
        v
RiskEngine (account identity, caps, exposure, spread, margin, news, mode)
        |
ExecutionEngine -> commit unique OrderIntent + reserve risk
        |
one serialized MT5 worker / RLock -> order_check -> order_send
        |
reconcile broker orders/deals/position identifiers -> trades + audit
        |
PositionManager -> legal, monotonic SL improvement -> TrailingEngine
        |
closed-trade features -> purged walk-forward training -> model registry
        |
proposal/evaluation -> owner review -> staged promotion or rollback
```

No MT5 call runs on the asyncio event-loop thread. A single broker worker owns
all terminal calls; the Telegram/FastAPI loops remain responsive. Database
sessions are not shared across threads. Execution and risk reservation are
serialized; implemented APScheduler jobs use max_instances=1 and coalescing.
A persisted single-instance lease protects the entire DB runtime, and locked
transactions serialize risk reservations across processes. Inflight broker/manual
races still require dedicated-account/server protection and reconciliation.

### Idempotency and uncertain acknowledgement

```text
prepared -> submitting -> acknowledged -> reconciled
                  |             |
                  |             +-> persist actual fill / remaining exposure
                  +-> explicit rejection -> rejected (no automatic retry)
                  +-> timeout/disconnect -> unknown -> halt new entries
```

MT5 does **not** provide a native exactly-once idempotency key. The database
prevents duplicate local intents, but an accepted order whose reply is lost
must be reconciled before any retry. Broker comments may be truncated/changed;
comments alone are not definitive evidence. Match account, order/deal tickets,
position identifier, magic, time, direction and volume; an unresolved ambiguity
requires owner intervention. Never retry an unknown `order_send` blindly.

### Profit-lock feasibility

At 30%, 60% and 90% of the original target, request the corresponding lock.
At exactly $3 floating profit it is normally **impossible** to legally place an
SL estimating $3 net profit: minimum stop/freeze distances, fees and slippage
leave no room. These levels are desired protection levels, not guaranteed fills.

The requested BUY/SELL formulas are retained as price candidates:

```text
BUY:  entry + desired_profit_distance - spread_buffer
SELL: entry - desired_profit_distance + spread_buffer
```

However, subtracting a buffer alone REDUCES the lock. The distance solver must
include fees, expected exit costs and buffer compensation, then verify net PnL
at the tick-rounded candidate with broker valuation. Do not double-count the
spread when entry/exits already use executable ask/bid. A candidate inside the
broker's stop/freeze zone is deferred (or a lower feasible tier is applied);
never claim the desired tier was locked. Gaps can still produce a worse fill.
Only a strictly more protective, legal SL is submitted. ATR cannot loosen a
better profit lock. TP extension is disabled by default and at most 1.2x when
enabled with safe news/volatility. Original target is immutable per trade.

### Part 8 news proof and causal availability

```text
Initialize / shutdown -> commit unknown scoped news epoch
Poll begin (SQL locked) -> revoke green / claim -> async bounded GET/file reads
  headlines + independently issued complete currency/interval calendar
  strict UTC/JSON/defused XML -> sanitized content -> impact / exposure / dedup
  immutable snapshot SHA + append-only publication commit, compare-and-swap claim
      | original publication / first-seen never freshened by reread/304
      | new severe content gets separate deduplicated risk-observation time
      v
Per-logical-symbol NewsWindow -> config/code/market/snapshot/epoch bound expiry
      | expires at age/cadence/calendar horizon/next future pre-event start
      v
AI review -> Signal finalization -> risk authorize -> same-transaction pre-send
      | latest committed epoch + exact symbol + non-fixture native provenance
      v
No safe proof => no new entry/TP extension; protective SL/close remain independent
```

Coverage means honest reviewed source/calendar claims within bounded freshness,
not cryptographically authenticated/exhaustive world-news truth. API license/realtime
and source scopes require operator review; rolling headlines cannot certify a calendar.
Local file content SHA is rechecked. Network bodies/keys/page URLs are not logged;
configured HTTPS origins are trusted, DNS/egress hardening remains deployment work.
Cancellation/late polls never restore cached green or replace a newer claim. SQL/file
threads cannot be killed; committed results are revoked if current after cancellation.
A veto cannot recall already submitted SDK orders or see upstream news before receipt.

Schema 2's News and append-only AuditLog are reused; risk/capital/latches unchanged.
Unknown defaults are mandatory for native-data paper/demo/live, independent of old
unbound diagnostic boolean DTOs. Alerts use an owner pending outbox; Part 9 adds claim-before-send/ack-after-success
Telegram delivery without blind retry. Part 10 implements lifecycle/scheduler.

### Learning/backtesting rules

- Only features available by the decision timestamp; only closed candles.
- Higher timeframe bars join as-of their CLOSE timestamp, not their open time.
- Labels mature after their complete forward horizon; open/unmatured trades
  never enter training. Purge label overlap and embargo the full horizon.
- Fit scalers/selectors/calibrators on training folds only. No shuffled CV.
- Replay news by publication/availability time and calendar data as known then;
  later revisions/actual release values cannot leak into earlier decisions.
- Synthetic mock data cannot qualify a model for broker/live promotion.
- Historical LLM analyses cannot be fabricated as if contemporaneous. Archived
  AI decisions or explicitly labeled deterministic baseline runs are required;
  paper/demo validate the actual AI veto layer.
- OHLC-only ambiguity uses adverse SL-before-TP ordering; realistic gaps, bid/ask,
  fees, swap and slippage are included. Fine-grained tick replay is preferred.
- Model artifacts are local, hashed and owner-approved. Never load downloaded
  untrusted pickle/joblib artifacts. Automatic risk increases are forbidden.

### Persistence/security boundaries

The seven requested tables are included, plus a deduplicated broker-deal ledger,
order intents, schema version, persistent state, account snapshots, risk baselines,
deployment evidence and owner approvals. Financial columns use exact Decimal strings in SQLite and
NUMERIC in PostgreSQL. Never perform SQL SUM on SQLite decimal text columns.
Timestamps are UTC-aware at the Python boundary. Audit is append-only through
ORM hooks and SQLite triggers. PostgreSQL runtime credentials must separately
lack UPDATE/DELETE on audit_logs; a database administrator can still alter data.
Backups and external audit copies are needed; this is not tamper-proof storage.

Configuration values are validated and read-only, including nested maps. Secrets
are SecretStr fields; public settings use an explicit allowlist. Rotating file
and console formatters redact known credentials, headers, token URLs and
tracebacks. Redaction is defense in depth, not permission to log raw requests,
initData, provider bodies or secrets in new code. No secrets in Mini App URLs.

## Complete target folder tree

`[now]` means implemented in Parts 1–7. Unmarked code files are supplied in
Parts 8–11. This is the **target tree**, not a claim all modules already exist.

```text
mt5_ai_reflex_bot/
├── main.py                              [now: non-trading operator CLI / migration]
├── config.py                            [now]
├── .env.example                         [now]
├── .gitignore                           [now]
├── requirements.txt                     [now]
├── requirements.linux.lock.txt          [now: Linux validation only]
├── pyproject.toml                       [now]
├── README.md                            [now]
├── watchdog.py
├── app/
│   ├── __init__.py
│   ├── bot.py
│   ├── scheduler.py
│   ├── lifecycle.py
│   └── dependencies.py
├── core/
│   ├── __init__.py                      [now]
│   ├── settings.py                      [now]
│   ├── logging_setup.py                 [now]
│   ├── database.py                      [now]
│   ├── migrations.py                    [now: explicit schema 1→2]
│   ├── models.py                        [now]
│   └── security.py                      [now: JSON/redaction helpers]
├── trading/
│   ├── __init__.py                      [now: Part 4]
│   ├── types.py                         [now: Part 4]
│   ├── price_rules.py                   [now: Part 4]
│   ├── currency.py                      [now: Part 4]
│   ├── authorization.py                 [now: deny-all default / contract]
│   ├── candles.py                       [now: Part 4]
│   ├── client_helpers.py                [now: Part 4]
│   ├── simulation.py                    [now: Part 4+5 automatic persistence]
│   ├── mt5_client.py                    [now: Part 4+5 final gate recheck]
│   ├── mock_mt5.py                      [now: Part 4]
│   ├── paper_mt5.py                     [now: Part 4]
│   ├── risk_types.py                    [now: trusted DTOs/provenance]
│   ├── runtime_state.py                 [now: durable lease/owner controls]
│   ├── stage_gate.py                    [now: artifact/ledger/live approval]
│   ├── execution_authority.py           [now: committed permit/revalidation]
│   ├── snapshots.py                     [now: portfolio/signed FX valuation]
│   ├── state_store.py                   [now: atomic shadow checkpoint]
│   ├── execution.py                    [now: Part 5]
│   ├── risk_engine.py                  [now: Part 5]
│   ├── position_manager.py             [now: Part 5]
│   ├── trailing_engine.py              [now: Part 5]
│   ├── symbol_manager.py                [now: Part 4]
│   ├── order_calculator.py              [now: Part 4]
│   └── trade_logger.py                  [now: Part 5]
├── strategy/
│   ├── __init__.py                      [now]
│   ├── base_strategy.py                 [now: contracts, not write permission]
│   ├── candle_patterns.py               [now: closed geometry]
│   ├── indicators.py                    [now: causal pandas/ta]
│   ├── feature_engine.py                [now: closed/as-of/hash validation]
│   ├── trend_strategy.py                [now]
│   ├── mean_reversion_strategy.py        [now]
│   ├── breakout_strategy.py             [now]
│   ├── momentum_strategy.py             [now]
│   ├── volatility_filter.py             [now: independent veto]
│   ├── news_filter.py                   [now: symbol-bound latest-epoch proof gate]
│   ├── signal_engine.py                 [now: async read/review service]
│   ├── signal_store.py                  [now: immutable bar/review proof]
│   ├── signal_execution.py              [now: exact duplicate decode]
│   └── strategy_router.py               [now: owner weighted consensus]
├── ai/
│   ├── __init__.py                      [now: import-only]
│   ├── json_validation.py               [now: bounded strict JSON]
│   ├── schemas.py                       [now: exact bound reply contracts]
│   ├── http_transport.py                [now: fixed-origin async POST]
│   ├── ai_router.py                     [now: availability-only fallback]
│   ├── ollama_client.py                 [now: /api/chat]
│   ├── openai_client.py                 [now: compatible chat completions]
│   ├── prompt_templates.py              [now: secret-free bound untrusted context]
│   ├── ai_supervisor.py                 [now: read-only EntryReviewer/offline job]
│   ├── suggestion_store.py              [now: owner proposal/projection lifecycle]
│   ├── owner_guard.py                   [now: not auth transport]
│   ├── trade_analyzer.py                [now: complete-leg deterministic report]
│   ├── learning_engine.py               [now: positive-proof USD label export]
│   ├── dataset.py                       [now: versioned immutable event samples]
│   ├── feature_engineering.py           [now: 32 pre-entry features]
│   ├── walk_forward.py                  [now: event purge/time embargo]
│   ├── model_trainer.py                 [now: CPU logistic/LightGBM]
│   ├── portable_model.py                [now: no pickle deserialization]
│   ├── artifacts.py                     [now: confined immutable JSON]
│   ├── model_registry.py                [now: owner stages/pointer/rollback]
│   └── strategy_optimizer.py            [now: small OOS association proposals]
├── news/
│   ├── __init__.py                      [now: no import side effects]
│   ├── types.py                         [now: bounded DTOs/UTC/JSON/sanitization]
│   ├── http_client.py                   [now: bounded async GET/no redirect]
│   ├── providers.py                     [now: actual licensed API contracts]
│   ├── rss_parser.py                    [now: defused RSS/Atom + 304]
│   ├── economic_calendar.py             [now: independent complete-range JSON/premium adapter]
│   ├── sentiment_analyzer.py            [now: advisory lexicon/deterministic impact]
│   ├── exposure.py                      [now: logical currency/asset mapping]
│   ├── evidence.py                      [now: scoped expiry/latest-epoch proof]
│   ├── alerts.py                        [now: pending owner outbox, not sender]
│   ├── news_manager.py                  [now: explicit async read-only collection]
│   └── news_cache.py                    [now: immutable snapshots/SQL dedup/audit CAS]
├── telegram_bot/
│   ├── __init__.py
│   ├── handlers.py
│   ├── commands.py
│   ├── keyboards.py
│   ├── miniapp_auth.py
│   └── notifications.py
├── miniapp/
│   ├── server.py
│   ├── static/
│   │   ├── index.html
│   │   ├── app.js
│   │   └── style.css
│   └── api/
│       ├── dashboard.py
│       ├── trades.py
│       ├── ai.py
│       ├── news.py
│       └── settings.py
├── backtesting/
│   ├── __init__.py
│   ├── backtester.py
│   ├── metrics.py
│   └── promotion.py
├── data/
│   ├── candles/                         [now: empty runtime directories]
│   ├── news/
│   ├── models/
│   ├── logs/
│   ├── paper/                           [now: managed runtime checkpoint]
│   └── backups/
├── scripts/
│   ├── __init__.py                      [now]
│   ├── smoke_mock.py                    [now: synthetic only]
│   ├── smoke_news.py                    [now: OFFLINE headlines/calendar; zero orders]
│   ├── synthetic_news_fixtures.py        [now: TEST ONLY, not genuine coverage]
│   ├── inspect_news_sources.py           [now: no HTTP, nonsecret identifiers]
│   ├── smoke_ai.py                      [now: OFFLINE scripted-provider/toy-ML flow]
│   ├── synthetic_ai_fixtures.py         [now: TEST ONLY, not real AI/data]
│   ├── smoke_signals.py                 [now: complete synthetic signal flow]
│   ├── synthetic_signal_market.py       [now: TEST ONLY, not historical replay]
│   ├── smoke_risk.py                    [now: temporary synthetic risk smoke]
│   ├── check_mt5_readonly.py            [now: local Windows diagnostic]
│   ├── run_mt5_background.vbs
│   ├── run_bot_background.vbs
│   ├── install_task_scheduler.ps1
│   ├── compile_exe.ps1
│   └── backup_db.py
├── docs/
│   ├── PART_08.md                       [now: full current source/config/tests]
│   ├── PART_08_NOTES.md                 [now: news/calendar contracts/limits]
│   ├── RELEASE_08_MANIFEST.json          [now: integrity, not permission]
│   ├── PART_07.md                       [historical: prior source snapshot]
│   ├── PART_07_NOTES.md                 [now: AI/learning contracts/limits]
│   ├── RELEASE_07_MANIFEST.json          [historical: prior release only]
│   ├── PART_06.md                       [now: full current source/config/tests]
│   ├── PART_06_NOTES.md                 [now: contracts/limits/formulas]
│   ├── RELEASE_06_MANIFEST.json          [historical: prior release only]
│   ├── PART_05.md                       [now: full current source/config/tests]
│   ├── PART_05_NOTES.md                 [now: contracts/limitations]
│   ├── MIGRATIONS.md                    [now: SQLite/PostgreSQL procedures]
│   ├── RELEASE_05_MANIFEST.json          [historical: prior release only]
│   ├── PART_04.md                       [now: full Part 4 source]
│   ├── PART_04_NOTES.md                 [now]
│   ├── CURRENT_TREE.txt                 [now: actual archive tree]
│   ├── RELEASE_04_MANIFEST.json          [historical: hashes of prior release]
│   ├── ARCHITECTURE.md                  [now]
│   ├── VALIDATION.md                    [now]
│   ├── TARGET_TREE.txt                  [now]
│   └── PARTS_01_03.md                   [now: prior source snapshot]
└── tests/
    ├── __init__.py                      [now]
    ├── fake_mt5_sdk.py                  [now: TEST ONLY, not runtime]
    ├── test_trading_contracts.py        [now]
    ├── test_simulated_broker.py         [now]
    ├── test_trading_diagnostics.py      [now]
    ├── conftest.py                      [now]
    ├── test_settings.py                 [now]
    ├── test_database.py                 [now]
    ├── test_security_logging.py         [now]
    ├── test_foundation_cli.py           [now]
    ├── test_mt5_client.py               [now: fake/synthetic only]
    ├── test_order_calculator.py         [now: fake/synthetic only]
    ├── risk_helpers.py                  [now: TEST ONLY synthetic fixtures]
    ├── test_runtime_state.py            [now]
    ├── test_execution.py                [now]
    ├── test_trade_logger.py             [now]
    ├── test_state_store.py              [now]
    ├── test_stage_gate.py               [now]
    ├── test_stage_ledger.py             [now: TEST ONLY authored proofs]
    ├── test_migrations.py               [now]
    ├── test_risk_contracts.py            [now]
    ├── test_risk_concurrency.py          [now: real cross-process locks]
    ├── test_risk_diagnostics.py          [now]
    ├── test_risk_engine.py             [now: Part 5]
    ├── test_trailing_engine.py         [now: Part 5]
    ├── signal_helpers.py                [now: TEST ONLY fixtures]
    ├── test_candle_patterns.py          [now]
    ├── test_feature_engine.py           [now]
    ├── test_strategies.py               [now]
    ├── test_strategy_router.py          [now]
    ├── test_signal_engine.py            [now]
    ├── test_signal_execution.py         [now]
    ├── test_signal_binding.py           [now: hand-tagged TEST DTOs only]
    ├── test_signal_diagnostics.py       [now]
    ├── news_helpers.py                  [now: TEST ONLY, no native proof]
    ├── test_news_settings.py             [now]
    ├── test_news_types.py                [now]
    ├── test_news_http.py                 [now: local MockTransport only]
    ├── test_news_providers.py            [now: real parsing, scripted replies]
    ├── test_news_rss.py                  [now]
    ├── test_news_calendar.py             [now]
    ├── test_news_sentiment_exposure.py   [now]
    ├── test_news_manager.py              [now]
    ├── test_news_evidence_cache.py       [now]
    ├── test_news_alerts.py               [now: pending, not real delivery]
    ├── test_news_risk_integration.py     [now: synthetic shadow only]
    ├── test_news_safety_edges.py         [now]
    ├── test_news_poll_boundaries.py      [now]
    ├── test_news_diagnostics.py          [now: isolated offline smoke]
    ├── test_telegram_auth.py
    ├── ai_helpers.py                    [now: TEST ONLY scripted fixtures]
    ├── test_ai_json.py                  [now]
    ├── test_ai_clients.py               [now: HTTP MockTransport only]
    ├── test_ai_router.py                [now]
    ├── test_ai_supervisor.py            [now]
    ├── test_ai_suggestions.py           [now]
    ├── test_ai_settings.py              [now]
    ├── test_ai_diagnostics.py           [now]
    ├── test_learning_features.py        [now]
    ├── test_learning_ledger.py          [now]
    ├── test_model_training.py           [now: actual toy CPU fits]
    ├── test_model_registry.py           [now]
    ├── test_strategy_optimizer.py       [now: hand-written association DTOs]
    ├── test_position_review_binding.py  [now: hand-tagged/native eval DTOs only]
    ├── test_mock_trading_flow.py
    └── test_backtester.py
```

## Implemented Part 9 owner interface boundaries

- API actor derives only from original signed Telegram initData, fresh auth_date,
  exact configured owner/private context; no integer/body/local browser fallback.
- Private aiogram message/callback sender checks and optional random-secret HTTPS
  webhook. Polling/menu/webhook configuration are explicit, never import effects.
- GET projections are bounded SQL/snapshot reads, no broker/HTTP/AI/proposal writes;
  unknown/stale account-currency observations and floating P&L are labelled honestly.
- Separate challenge/run purposes reuse schema 2. Nonce hashes, short TTL,
  single-use captured scope, SQL reserve-before-effect, terminal replay/pending fence.
- Resume revision compare is in serialized SQL, preventing later pause/kill/heartbeat
  erasure. Downward control bypasses slow async close lock; no SDK order recall.
- Captured exact bot-owned close hash is verified at authorize/pre-send; partial
  close-all outcomes preserve already observed results and bounded remaining ledger,
  pause/halt entries and never claim broker-account-flat or automatic retry safety.
- Approve/reject records proposal decision ONLY, never settings/trading/live/apply.
- HTTPS/direct ASGI TLS or exact trusted proxy PEER attestation. No forwarded
  host/client-IP trust; exact CORS/hosts, request bounds, generic errors, CSP/no-store,
  in-memory/header-only bearer, process-local normal/reserved-stop rate limits.
- Explicit synthetic preview ignores .env/process flags; no owner/broker/providers,
  every mutation denied. Static standalone preview is likewise fake/read-only.
- Concurrent immutable model artifact writes now flush temporary bytes before
  atomic no-overwrite hard-link publication, preventing partially visible digest files.

The API-only launcher does not compose/start an engine or bot. Native SDK calls
remain executor-backed. Part 10 owns resources, polling/server/scheduler lifecycle,
health/backup/watchdog and Windows interactive logon tasks. Full contracts/limits,
route/command tables and runnable setup are in PART_09_NOTES.md.


## Part 10 — actual runtime/resource ownership and supervisor

Explicit `app.bot` or supervisor startup only; imports never start a resource.
Existing persistent schema verified before composition; OS runtime lock and SQL
lease exclude other runtimes. Guarded broker/ExecutionEngine, readonly SignalEngine,
AISupervisor/proposals/selected registry model and NewsManager share the same actual
market source, code/model/config profile and clock. PAPER's execution label never
relabels the underlying native market. Model filtering has no silent baseline fallback.

One UTC AsyncIOScheduler: protective position cycles 5s while PAUSED/KILLED;
entry evaluation 30s only when explicitly RUNNING; independent 10s lease heartbeat;
news poll 180s, typed advisory and optional extension reviews; 30s notifications;
24-hour report and disabled-by-default paused/owned-flat inactive-candidate learning;
03:00 UTC SQLite DB-only snapshot. Max_instances=1/coalescing/internal overlap locks,
no missed-order catchup, risk escalation, proposal apply or auto resume.

Shutdown fences new actions, persists downward-only pause, stops accepting jobs,
then drains active owner requests/jobs/aiogram handlers/native concurrent Futures
before releasing resources. API shutdown has no forced cancellation of an accepted
write. Native timeout is not thread completion: pending concurrent Futures are
tracked and further SDK queueing is refused. DB/OS lock stay alive through late
acknowledgements; quarantine/history/capital/latches remain unchanged.

30s watchdog owns only its direct shell-free Popen child; exact PID/create-time/
generation/config/code health evidence and SQL+OS leases, no PID-file adoption.
It restarts only a confirmed exited child, PAUSED, with a durable default 3 launches/
rolling hour (initial and failed launches count). Alive but stale/overdue/quarantined:
generation-bound graceful stop + alert, no kill/replacement. Source/.env changes
stop this supervisor instead of rebinding. Persistent operator stop survives logon;
clear action never grants resume/reset/live permission.

Owner notices claim before Telegram send, ack only success, uncertain never blindly
resent. No token means no network transport. Health JSON is bounded atomic private
evidence, not authorization and contains no account/position/credentials. Backups
use SQLite's WAL-aware API, bounded CRC/hash ZIPs; online DB-only != coherent paper
recovery. STOPPED coherent bundle requires matching OS/SQL released state plus paper/
model/news/candle artifacts. No auto-restore or reset. NTFS privacy and real Windows/
SDK/TLS/EXE/PostgreSQL validation remain operator obligations, not tested claims.
See PART_10_NOTES.md and DEPLOYMENT_WINDOWS.md for literal scripts/contracts.


## Part 11 — file replay and production-evidence boundary

Read-only hash-verified dataset -> availability cursor / HISTORICAL MarketData ->
actual SignalEngine + archived NewsWindow/reviewer -> durable ExecutionEngine/risk/
owner/StageGate -> existing shadow broker + closed-bar pessimistic OHLC adapter ->
actual PositionManager/TrailingEngine -> private SQLite/checkpoint/journals ->
account-currency metrics and research-only artifacts.

A separate native-only ledger exporter recomputes paper/demo metrics and core original
intent/deal/continuous account proofs. Signed fresh Telegram initData + exact file digest
+ stopped/paused/flat state import a separately reviewed production stage artifact.
Research format and synthetic/test provenance cannot qualify. No import starts/resumes
or grants live, copies production state, selects a model or resets loss history.

Frozen monetary contracts, strict local paths/bytes/rows, original quote/news/review
availability, complete M1 higher-frame groups, original context/model/config hashes,
no confidence/time refresh, real core caps and monotonic protection remain binding.
OHLC entries/protection use declared boundaries, carried-range stop first, adverse
spread/gaps/slippage and rollover-before-exit; never partial-bar inference. Date/cost
assumptions and sampled drawdown are explicit, not an authentic vendor/fill guarantee.

This v1 replay contract is baseline-only: enabled model-filter policy permits veto-only
analysis but approval replay requires a causal approved registry and refuses without one.
Production supervisor/learning/registry policy is unchanged. No genuine dataset or
native stage/Windows/provider/owner authentication was validated for this release.


## Part 12 — diagnostic-only branch outside execution/authorization

Current manifest -> stdlib raw bounded digest + runnable closure verification ->
file/defaults-only existing Settings + distribution metadata + local OS/file facts ->
optional stable main DB raw read -> fixed query-only/authorizer in MEMORY COPY ->
stdout reflex-readiness-v1. No broker/provider/Telegram/OS-DLL/process/original SQL
connection or financial/control writes. Integrity failure skips config/state; source
root identity prevents one install validating with another loaded Settings version.

Nonempty WAL/rollback journal is refused, not replayed/deleted/checkpointed. Original
state/leases/account/source remain authoritative in the actual runtime. No diagnostic
JSON is a signed owner identity, native entitlement/fact, StageGate report, model
activation or live/resume permission. Windows unknown mandatory session/ACL/feed/owner
checks stay blocked. Metadata presence/EXE existence/hash matching are narrowly reported,
never promoted to native-platform/supply-chain/authenticity claims.

No trading or risk policy changed; readiness source joins the ordinary code hash.
Original configuration still uses its runtime ENV precedence; new file-only diagnosis
reports recognized process override mismatch and never changes process ENV/.env.


## Part 13: fixed causal historical-model snapshot (0.11.0 / schema 2)

Historical manifest → immutable SHA-bound artifact/corpus → source/code/policy/schema
+ label/selection/embargo/age checks → real purged trainer reconstruction BEFORE output
creation → new private BACKTEST-only import → fixed RuntimeProfile/model → shared
SignalStore actual inference → archived AI/news checks → shared execution/risk inference
revalidation. The selected model read ALSO checks private run/SQLite/captured-manifest
identity. No probability DTO authorization, production registry access/activation, actual
owner authentication, midrun fitting/switching or research→stage conversion. Protective
owned-position management remains independent of new-entry ML availability. See ML_REPLAY.md.


## Part 14: research artifact closure and bounded read-only audit

Shared replay engines → successful engine/market shutdown + private DB close → exact
new bundle closure publication → bounded stable immutable file capture → raw integrity
FIRST → captured input/availability/gap checks → metric/decision/model/checkpoint/PRIVATE
MEMORY SQL consistency → second unchanged snapshot check → stdout observation only.
No original SQL connection, credentials/Settings load, SDK/provider/Telegram/children,
repair/reset/re-seal or research→StageGate converter. LightGBM/training are explicitly
not reexecuted by this inspector. See REPLAY_AUDIT.md; all original safety remains.
