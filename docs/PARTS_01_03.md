# MT5 AI ReflexBot — Parts 1–3

**Full ordered implementation guide.** Each file block contains its complete
source, not a snippet. The downloadable repository is
`MT5_AI_ReflexBot_Parts_01_03.zip`. Do not mistake the target tree for implemented
trading functionality: this installment cannot submit broker orders.

# PART 1 — Overview, safety assumptions, architecture and complete target tree


## Implementation status

This is installment 1: **Parts 1–3**. Configuration, database, logging, secret
sanitization, an operator CLI and foundation tests are implemented. Parts 4–11
are planned below and are not represented by empty Python stubs. The current
CLI cannot connect to a broker, run a trading loop, or send Telegram commands.
This repository is not yet a completed or independently audited live system.

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
3. Risk per trade defaults to 0.5% of equity, **0.1% maximum for initial live
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
    Default paper/demo gates each require at least 14 days and 100 entries,
    profit factor above the configured floor and drawdown within the stage cap.
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
serialized; APScheduler jobs use max_instances=1 and coalescing. A single-instance
runtime lock prevents two bot processes for the same configuration/account.

### Idempotency and uncertain acknowledgement

```text
prepared -> submitting -> acknowledged -> reconciled
                  |             |
                  |             +-> persist actual fill / remaining exposure
                  +-> explicit rejection -> rejected (bounded safe retry only)
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
SL guaranteeing $3 net profit: minimum stop/freeze distances, fees and slippage
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

`[now]` means implemented in Parts 1–3. Unmarked code files are supplied in
Parts 4–11. This is the **target tree**, not a claim all modules already exist.

```text
mt5_ai_reflex_bot/
├── main.py                              [now: foundation CLI only]
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
│   ├── __init__.py                       [now]
│   ├── settings.py                       [now]
│   ├── logging_setup.py                  [now]
│   ├── database.py                       [now]
│   ├── models.py                         [now]
│   └── security.py                       [now: JSON/redaction helpers]
├── trading/
│   ├── __init__.py
│   ├── types.py
│   ├── mt5_client.py
│   ├── mock_mt5.py
│   ├── paper_mt5.py
│   ├── execution.py
│   ├── risk_engine.py
│   ├── position_manager.py
│   ├── trailing_engine.py
│   ├── symbol_manager.py
│   ├── order_calculator.py
│   └── trade_logger.py
├── strategy/
│   ├── __init__.py
│   ├── base_strategy.py
│   ├── candle_patterns.py
│   ├── trend_strategy.py
│   ├── mean_reversion_strategy.py
│   ├── breakout_strategy.py
│   ├── momentum_strategy.py
│   ├── volatility_filter.py
│   ├── news_filter.py
│   ├── signal_engine.py
│   └── strategy_router.py
├── ai/
│   ├── __init__.py
│   ├── ai_router.py
│   ├── ollama_client.py
│   ├── openai_client.py
│   ├── prompt_templates.py
│   ├── trade_analyzer.py
│   ├── learning_engine.py
│   ├── feature_engineering.py
│   ├── model_trainer.py
│   ├── model_registry.py
│   ├── strategy_optimizer.py
│   └── ai_supervisor.py
├── news/
│   ├── __init__.py
│   ├── news_manager.py
│   ├── rss_parser.py
│   ├── economic_calendar.py
│   ├── sentiment_analyzer.py
│   └── news_cache.py
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
│   ├── candles/                          [now: empty runtime directories]
│   ├── news/
│   ├── models/
│   ├── logs/
│   └── backups/
├── scripts/
│   ├── run_mt5_background.vbs
│   ├── run_bot_background.vbs
│   ├── install_task_scheduler.ps1
│   ├── compile_exe.ps1
│   └── backup_db.py
├── docs/
│   ├── ARCHITECTURE.md                   [now]
│   ├── VALIDATION.md                     [now]
│   ├── TARGET_TREE.txt                   [now]
│   └── PARTS_01_03.md                    [now: full ordered source guide]
└── tests/
    ├── conftest.py                       [now]
    ├── test_settings.py                  [now]
    ├── test_database.py                  [now]
    ├── test_security_logging.py          [now]
    ├── test_foundation_cli.py            [now]
    ├── test_mt5_client.py
    ├── test_order_calculator.py
    ├── test_risk_engine.py
    ├── test_trailing_engine.py
    ├── test_signal_engine.py
    ├── test_news_filter.py
    ├── test_telegram_auth.py
    ├── test_ai_json.py
    ├── test_mock_trading_flow.py
    └── test_backtester.py
```


# PART 2 — Complete environment, requirements and README

## `.env.example`

```dotenv
# MT5 AI ReflexBot — fail-closed defaults. Copy to .env; never commit .env.
# MODE: four mutually exclusive outcomes (see README mode matrix)
BACKTEST_MODE=false
DEMO_MODE=true
LIVE_TRADING=false
PAPER_TRADING=true
START_PAUSED=true
MT5_BACKEND=mock

# MT5: real integration runs on Windows in the logged-on user's session.
MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe
# Leave ALL THREE empty to attach to the terminal's existing login.
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_MAGIC_NUMBER=20260217
MT5_CONNECT_TIMEOUT_MS=10000
MT5_RECONNECT_ATTEMPTS=3
MT5_RECONNECT_BACKOFF_SECONDS=5
MAX_TICK_AGE_SECONDS=10
MAX_CANDLE_AGE_SECONDS=420

# ACCOUNT RISK: percentages are percentage points, not fractions.
ACCOUNT_CURRENCY=USD
MAX_RISK_PERCENT_PER_TRADE=0.5
LIVE_MAX_RISK_PERCENT_PER_TRADE=0.1
MAX_TOTAL_OPEN_RISK_PERCENT=1.5
MAX_DAILY_LOSS_PERCENT=3
MAX_DRAWDOWN_PERCENT=10
MAX_MARGIN_USAGE_PERCENT=30
MAX_DAILY_TRADES=12
MIN_DAILY_TRADES_TARGET=6
MAX_OPEN_POSITIONS=3
MAX_SAME_SYMBOL_POSITIONS=1
MAX_SPREAD_POINTS=35
# Broker POINTS, not pips. Empty means use the conservative global limit.
SYMBOL_SPREAD_LIMITS_JSON={}
MAX_SLIPPAGE_POINTS=10
MIN_SIGNAL_SCORE=70
REQUIRE_STOP_LOSS=true
MIN_NET_REWARD_RISK=1.1
TRADING_DAY_TIMEZONE=UTC

# PROFIT TARGET: an objective, not guaranteed profit or a sizing instruction.
TARGET_PROFIT_USD_PER_TRADE=5
USE_DYNAMIC_TARGET=true
TARGET_R_MULTIPLE=1.1
# Replace these estimates with your broker's actual costs before evaluation.
COMMISSION_ROUND_TURN_USD_PER_LOT=7
ESTIMATED_SWAP_USD_PER_LOT_PER_DAY=0

# TRADING SYMBOLS: logical names; case-sensitive broker aliases are supported.
SYMBOLS=XAUUSD,BTCUSD,EURUSD,GBPUSD,ETHUSD
SYMBOL_ALIASES_JSON={}
# Add entries for custom symbols. Unknown news exposure blocks new entries.
SYMBOL_NEWS_CURRENCIES_JSON={"XAUUSD":["USD"],"BTCUSD":["USD"],"ETHUSD":["USD"],"EURUSD":["EUR","USD"],"GBPUSD":["GBP","USD"],"US30":["USD"],"NAS100":["USD"]}
# Used only when account currency is not USD; broker-specific names required.
ACCOUNT_TO_USD_SYMBOLS_JSON={}

# TIMEFRAMES: closed bars only; H1 bias is aligned as-of the decision time.
PRIMARY_TIMEFRAME=M5
HIGHER_TIMEFRAME=M15
TREND_TIMEFRAME=H1
CANDLE_LOOKBACK=300

# PROFIT-LOCK TRAILING: trigger percent : desired locked target percent.
TRAILING_LEVELS=30:30,60:60,90:90
TRAILING_SPREAD_BUFFER_POINTS=2
ATR_TRAILING_ENABLED=true
ATR_TRAILING_MULTIPLIER=1.5
ALLOW_TP_EXTENSION=false
TP_EXTENSION_FACTOR=1.2

# TELEGRAM: configure BOTH token and owner ID, or leave both empty.
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_ID=
TELEGRAM_MINIAPP_URL=
TELEGRAM_WEBHOOK_URL=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_INITDATA_MAX_AGE_SECONDS=300
# Polling is default; webhook and polling must never run simultaneously.
TELEGRAM_USE_WEBHOOK=false

# API: loopback default; expose only through a TLS reverse proxy/tunnel.
API_HOST=127.0.0.1
API_PORT=8000
API_RATE_LIMIT_PER_MINUTE=60
API_MAX_REQUEST_BYTES=16384
API_TRUSTED_HOSTS_JSON=["127.0.0.1","localhost"]
# An empty allowlist means no cross-origin browser API access.
API_CORS_ORIGINS_JSON=[]

# AI: absence, timeout or invalid JSON => reject new entries, not approve.
AI_PROVIDER=ollama
AI_FALLBACK_PROVIDER=openai
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
ALLOW_REMOTE_OLLAMA=false
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
AI_CONFIDENCE_THRESHOLD=70
AI_TIMEOUT_SECONDS=12
AI_MAX_CONCURRENT=2
AI_POSITION_REVIEW_SECONDS=300
AUTO_REDUCE_RISK=false
AUTO_ADAPT_STRATEGY_WEIGHTS=false
MAX_STRATEGY_WEIGHT_STEP=0.02

# NEWS: headlines are NOT a substitute for an economic calendar.
NEWS_API_KEY=
FINNHUB_API_KEY=
CRYPTOPANIC_API_KEY=
USE_FREE_NEWS_SOURCES=true
USE_RSS=true
USE_ECONOMIC_CALENDAR=true
BLOCK_TRADING_HIGH_IMPACT_NEWS=true
NEWS_IMPACT_THRESHOLD=high
NEWS_REQUIRED_FOR_ENTRY=true
# Supply feeds you are entitled to use; unavailable feeds stay unavailable.
RSS_URLS_JSON=[]
CALENDAR_SOURCE_URL=
CALENDAR_FILE=data/news/calendar.json
NEWS_POLL_SECONDS=180
NEWS_MAX_AGE_SECONDS=900
CALENDAR_MAX_AGE_SECONDS=21600
NEWS_PRE_EVENT_MINUTES=30
NEWS_POST_EVENT_MINUTES=15

# LEARNING + PROMOTION: owner approval is still required after these gates.
REQUIRE_STAGE_GATES=true
MODEL_MIN_LABELLED_TRADES=300
MODEL_WALK_FORWARD_FOLDS=5
MODEL_LABEL_HORIZON_BARS=12
MODEL_EMBARGO_BARS=12
STAGE_MIN_PAPER_DAYS=14
STAGE_MIN_DEMO_DAYS=14
STAGE_MIN_TRADES=100
STAGE_MIN_PROFIT_FACTOR=1.1
STAGE_MAX_DRAWDOWN_PERCENT=5
LIVE_APPROVAL_TTL_SECONDS=3600

# DATABASE: PostgreSQL requires the optional psycopg driver.
DATABASE_URL=sqlite:///data/reflexbot.db
# Example: postgresql+psycopg://reflexbot:password@localhost:5432/reflexbot
DATABASE_BUSY_TIMEOUT_MS=10000

# SCHEDULER / WATCHDOG
SIGNAL_INTERVAL_SECONDS=30
POSITION_INTERVAL_SECONDS=5
HEARTBEAT_INTERVAL_SECONDS=10
WATCHDOG_INTERVAL_SECONDS=30
WATCHDOG_STALE_SECONDS=90
WATCHDOG_MAX_RESTARTS_PER_HOUR=3

# LOGGING / LOCAL FILES
LOG_LEVEL=INFO
LOG_FILE=data/logs/bot.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=5
DATA_DIR=data
BACKUP_DIR=data/backups
```

## `requirements.txt`

```text
# Direct dependencies pinned to an available, Python 3.11-compatible baseline.
# Generate and audit a platform-specific transitive lock after validation;
# do not reuse a Linux lock for the Windows MT5 deployment.
pydantic==2.13.5
pydantic-settings==2.15.0
python-dotenv==1.2.4
SQLAlchemy==2.1.2
fastapi==0.142.2
uvicorn==0.54.0
aiogram==3.31.0
APScheduler==3.11.3
httpx==0.28.1
pandas==3.0.6
numpy==2.4.6
ta==0.11.0
scikit-learn==1.9.1
lightgbm==4.7.0
joblib==1.6.0
feedparser==6.0.14
defusedxml==0.7.1
psutil==7.2.2
tzdata==2026.4
pytest==9.1.1
pytest-asyncio==1.4.0
MetaTrader5==5.0.6231; sys_platform == "win32"

# Optional PostgreSQL, packaging and advanced ML; install explicitly as needed:
# psycopg[binary]==3.3.6
# pyinstaller==6.22.3
# torch  # choose a tested CPU/CUDA build for your platform; not required.
# pip-audit==2.10.1  # deployment dependency audit, not a runtime dependency.
```

## `README.md`

````markdown
# MT5 AI ReflexBot

Selective, owner-controlled MT5 trading automation. **No profit guarantee.**

## Current release: Parts 1–3 only

Implemented: validated fail-closed settings, all requested database tables plus
safety state, UTC/exact-decimal persistence, append-only audit protections,
secret-safe rotating logs, a foundation CLI and tests. The CLI cannot place
orders, start MT5, poll Telegram, serve a Mini App or run a backtest yet.
Broker/strategy/AI/news/UI/watchdog modules arrive in Parts 4–11; the target
architecture lists them explicitly as forthcoming, not fake stub code.

Read `docs/ARCHITECTURE.md` for the complete target tree, safety contract,
account-mode matrix, execution/idempotency design and training constraints.
`docs/PARTS_01_03.md` contains the full ordered source for this installment.

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
   not for the current CLI. Check broker symbol trading hours/permissions.
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
   both together. Keep Mini App/webhook values blank until Part 9. Telegram
   controls are not installed in this foundation release.
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

9. Keep `.env`, data, backups and logs readable only by your Windows user. Example
   ACL tightening after creating `.env` (inspect the resulting ACLs):

```powershell
$Principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .env /inheritance:r /grant:r "${Principal}:(R,W)"
icacls data /inheritance:r /grant:r "${Principal}:(OI)(CI)F" /T
```

ACL rules depend on your organization; do not lock yourself out. Use full-disk
protection where available. Add exclusions for sensitive paths in backups/shares.

## Mode selection (configuration validation, not trading authorization)

| Mode | BACKTEST_MODE | DEMO_MODE | PAPER_TRADING | LIVE_TRADING | MT5_BACKEND |
|---|---|---|---|---|---|
| Backtest | true | true | false | false | mock |
| Paper with mock data | false | true | true | false | mock |
| Paper with broker data | false | true | true | false | real |
| Broker demo | false | true | false | false | real |
| Broker live | false | false | false | true | real |

Broker execution is NOT enabled by this table alone. The later runtime verifies
actual account type/identity, staged evidence, protection, risk and an owner
approval. No Mini App setting will directly toggle live order permission.

## Operational deployment sequence (implemented in later parts)

Do not run the commands below until those files/modules have been supplied:

1. **Part 4–6**: configure real symbol aliases, points/tick sizes, lot steps,
   commission, swap and currency conversion. Test the MT5 adapter read-only.
   Keep `PAPER_TRADING=true`; a real data backend still uses simulated execution.
2. **Part 7–8**: run Ollama with the configured model or an explicitly configured
   cloud provider. Provider failure rejects entries; no fabricated confidence.
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
4. **Part 10**: run the assembled foreground runtime, inspect `/health`, then
   test `/status`, `/positions`, `/pause`, `/resume`, confirmations and kill switch.
   Test protective monitoring while entries are paused. Use polling OR webhooks.
5. **Part 11**: chronological cost-aware backtest, then >=14-day paper stage,
   then >=14-day broker-demo stage, each with sufficient trades and reviewed
   metrics. Persist hashed evidence for the same strategy/code/model. Mock-only
   reports cannot qualify as real demo/live evidence. Failed gates mean no
   promotion; do not edit the database to manufacture success.
6. Start `scripts/run_mt5_background.vbs` to launch MT5 minimized then the bot
   hidden. `scripts/run_bot_background.vbs` starts only the bot. Install the
   logon task using `scripts/install_task_scheduler.ps1`; its logon type must
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

The deployment scripts and `main.py run` command are deliberately absent until
Part 10. Background/watchdog operation must not be simulated by an empty loop.

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

The platform marker skips MetaTrader5; no import of it exists in this foundation.
The later MockMT5Client is for test/simulation only, not a broker bridge.

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

No automatic destructive migration exists. Future schema revisions require a
reviewed migration, offline backup and version update. Incompatibility fails
closed. Back up SQLite with its online backup API (Part 10), not by copying a
live `.db` file and omitting its WAL. Protect and test restores of backups.

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

See `docs/VALIDATION.md` for executed commands and results. Foundation tests
are not strategy, broker, profitability, Telegram or live-readiness validation.
No live permission has been granted and no market orders have been sent.
````


# PART 3 — Core configuration, database models and logging

The persistent state/approval schema is ready for later components. Its presence
is not an implemented live-promotion checker. No broker execution code is shipped
in this installment. All required safety checks must be wired and tested in later
parts before a real order can be authorized.

## `core/__init__.py`

```python
"""Safety-critical shared infrastructure. Imports have no runtime side effects."""

__version__ = "0.1.0"
```

## `config.py`

```python
"""Compatibility import; settings are loaded explicitly, never at import time."""

from core.settings import OperatingMode, Settings, get_settings

__all__ = ["OperatingMode", "Settings", "get_settings"]
```

## `core/settings.py`

```python
"""Immutable, validated configuration. Environment flags never authorize orders."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.security import sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
Provider = Literal["ollama", "openai", "disabled"]


class FrozenDict(dict):
    """Read-only configuration maps; values are also immutable scalars/tuples."""

    def _deny(self, *args: object, **kwargs: object) -> None:
        raise TypeError("configuration maps are read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny


class OperatingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


def _valid_url(value: str, *, allow_local_http: bool = False, https_only: bool = False) -> str:
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme not in allowed or not parsed.hostname:
        raise ValueError("a valid HTTP(S) URL is required")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL credentials and fragments are forbidden")
    if allow_local_http and parsed.scheme == "http" and not local:
        raise ValueError("non-loopback AI endpoints must use HTTPS")
    return value.rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
        populate_by_name=True,
    )

    # Trusted local/test override; defaults to the checked-out repository.
    project_root: Path = Field(default=PROJECT_ROOT, exclude=True)
    backtest_mode: bool = False
    demo_mode: bool = True
    live_trading: bool = False
    paper_trading: bool = True
    start_paused: bool = True
    mt5_backend: Literal["mock", "real"] = "mock"
    mt5_terminal_path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"
    mt5_login: int | None = Field(default=None, gt=0)
    mt5_password: SecretStr = SecretStr("")
    mt5_server: str = ""
    mt5_magic_number: int = Field(default=20260217, gt=0, le=2**31 - 1)
    mt5_connect_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    mt5_reconnect_attempts: int = Field(default=3, ge=1, le=5)
    mt5_reconnect_backoff_seconds: int = Field(default=5, ge=1, le=60)
    max_tick_age_seconds: int = Field(default=10, ge=1, le=60)
    max_candle_age_seconds: int = Field(default=420, ge=60, le=86400)

    account_currency: str = "USD"
    max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    live_max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    max_total_open_risk_percent: Decimal = Field(default=Decimal("1.5"), gt=0, le=5)
    max_daily_loss_percent: Decimal = Field(default=Decimal("3"), gt=0, le=10)
    max_drawdown_percent: Decimal = Field(default=Decimal("10"), gt=0, le=25)
    max_margin_usage_percent: Decimal = Field(default=Decimal("30"), gt=0, le=50)
    max_daily_trades: int = Field(default=12, ge=1, le=50)
    min_daily_trades_target: int = Field(default=6, ge=0, le=50)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    max_same_symbol_positions: int = Field(default=1, ge=1, le=1)
    max_spread_points: int = Field(default=35, ge=0, le=100000)
    symbol_spread_limits: dict[str, int] = Field(
        default_factory=dict, validation_alias="SYMBOL_SPREAD_LIMITS_JSON"
    )
    max_slippage_points: int = Field(default=10, ge=0, le=1000)
    min_signal_score: float = Field(default=70, ge=50, le=100)
    require_stop_loss: bool = True
    min_net_reward_risk: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    trading_day_timezone: str = "UTC"

    target_profit_usd_per_trade: Decimal = Field(default=Decimal("5"), gt=0, le=10000)
    use_dynamic_target: bool = True
    target_r_multiple: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    commission_round_turn_usd_per_lot: Decimal = Field(default=Decimal("7"), ge=0, le=10000)
    estimated_swap_usd_per_lot_per_day: Decimal = Field(default=Decimal("0"), ge=0, le=10000)
    symbols: Annotated[tuple[str, ...], NoDecode] = ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    symbol_aliases: dict[str, str] = Field(default_factory=dict, validation_alias="SYMBOL_ALIASES_JSON")
    symbol_news_currencies: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "XAUUSD": ["USD"],
            "BTCUSD": ["USD"],
            "ETHUSD": ["USD"],
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "US30": ["USD"],
            "NAS100": ["USD"],
        },
        validation_alias="SYMBOL_NEWS_CURRENCIES_JSON",
    )
    account_to_usd_symbols: dict[str, str] = Field(
        default_factory=dict, validation_alias="ACCOUNT_TO_USD_SYMBOLS_JSON"
    )
    primary_timeframe: Timeframe = "M5"
    higher_timeframe: Timeframe = "M15"
    trend_timeframe: Timeframe = "H1"
    candle_lookback: int = Field(default=300, ge=200, le=5000)

    trailing_levels: Annotated[tuple[tuple[float, float], ...], NoDecode] = ((30, 30), (60, 60), (90, 90))
    trailing_spread_buffer_points: int = Field(default=2, ge=0, le=1000)
    atr_trailing_enabled: bool = True
    atr_trailing_multiplier: Decimal = Field(default=Decimal("1.5"), ge=1, le=5)
    allow_tp_extension: bool = False
    tp_extension_factor: Decimal = Field(default=Decimal("1.2"), ge=1, le=1.5)

    telegram_bot_token: SecretStr = SecretStr("")
    telegram_owner_id: int | None = Field(default=None, gt=0)
    telegram_miniapp_url: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    telegram_initdata_max_age_seconds: int = Field(default=300, ge=30, le=600)
    telegram_use_webhook: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1024, le=65535)
    api_rate_limit_per_minute: int = Field(default=60, ge=5, le=300)
    api_max_request_bytes: int = Field(default=16384, ge=1024, le=65536)
    api_trusted_hosts: tuple[str, ...] = Field(
        default=("127.0.0.1", "localhost"), validation_alias="API_TRUSTED_HOSTS_JSON"
    )
    api_cors_origins: tuple[str, ...] = Field(default=(), validation_alias="API_CORS_ORIGINS_JSON")

    ai_provider: Provider = "ollama"
    ai_fallback_provider: Provider = "openai"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    allow_remote_ollama: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    ai_confidence_threshold: float = Field(default=70, ge=50, le=100)
    ai_timeout_seconds: int = Field(default=12, ge=1, le=60)
    ai_max_concurrent: int = Field(default=2, ge=1, le=4)
    ai_position_review_seconds: int = Field(default=300, ge=60, le=3600)
    auto_reduce_risk: bool = False
    auto_adapt_strategy_weights: bool = False
    max_strategy_weight_step: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.02"))

    news_api_key: SecretStr = SecretStr("")
    finnhub_api_key: SecretStr = SecretStr("")
    cryptopanic_api_key: SecretStr = SecretStr("")
    use_free_news_sources: bool = True
    use_rss: bool = True
    use_economic_calendar: bool = True
    block_trading_high_impact_news: bool = True
    news_impact_threshold: Literal["medium", "high"] = "high"
    news_required_for_entry: bool = True
    rss_urls: tuple[str, ...] = Field(default=(), validation_alias="RSS_URLS_JSON")
    calendar_source_url: str = ""
    calendar_file: Path = Path("data/news/calendar.json")
    news_poll_seconds: int = Field(default=180, ge=30, le=900)
    news_max_age_seconds: int = Field(default=900, ge=60, le=3600)
    calendar_max_age_seconds: int = Field(default=21600, ge=60, le=86400)
    news_pre_event_minutes: int = Field(default=30, ge=15, le=120)
    news_post_event_minutes: int = Field(default=15, ge=5, le=120)

    require_stage_gates: bool = True
    model_min_labelled_trades: int = Field(default=300, ge=100, le=100000)
    model_walk_forward_folds: int = Field(default=5, ge=3, le=20)
    model_label_horizon_bars: int = Field(default=12, ge=1, le=1000)
    model_embargo_bars: int = Field(default=12, ge=1, le=2000)
    stage_min_paper_days: int = Field(default=14, ge=7, le=365)
    stage_min_demo_days: int = Field(default=14, ge=7, le=365)
    stage_min_trades: int = Field(default=100, ge=50, le=10000)
    stage_min_profit_factor: Decimal = Field(default=Decimal("1.1"), gt=1, le=5)
    stage_max_drawdown_percent: Decimal = Field(default=Decimal("5"), gt=0, le=10)
    live_approval_ttl_seconds: int = Field(default=3600, ge=60, le=86400)

    database_url: SecretStr = SecretStr("sqlite:///data/reflexbot.db")
    database_busy_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    signal_interval_seconds: int = Field(default=30, ge=5, le=300)
    position_interval_seconds: int = Field(default=5, ge=1, le=30)
    heartbeat_interval_seconds: int = Field(default=10, ge=5, le=30)
    watchdog_interval_seconds: int = Field(default=30, ge=15, le=120)
    watchdog_stale_seconds: int = Field(default=90, ge=30, le=600)
    watchdog_max_restarts_per_hour: int = Field(default=3, ge=1, le=5)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Path = Path("data/logs/bot.log")
    log_max_bytes: int = Field(default=5242880, ge=1048576, le=52428800)
    log_backup_count: int = Field(default=5, ge=1, le=20)
    data_dir: Path = Path("data")
    backup_dir: Path = Path("data/backups")

    @field_validator("mt5_login", "telegram_owner_id", mode="before")
    @classmethod
    def empty_int_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = json.loads(value) if value.lstrip().startswith("[") else value.split(",")
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one symbol is required")
        symbols = tuple(str(item).strip() for item in value)
        if len(symbols) > 30 or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique; maximum 30")
        if any(not re.fullmatch(r"[A-Za-z0-9_.#-]{1,64}", item) for item in symbols):
            raise ValueError("invalid symbol name")
        return symbols

    @field_validator("trailing_levels", mode="before")
    @classmethod
    def parse_trailing_levels(cls, value: object) -> tuple[tuple[float, float], ...]:
        if isinstance(value, str):
            value = [item.split(":") for item in value.split(",")]
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one trailing level is required")
        try:
            levels = tuple((float(trigger), float(lock)) for trigger, lock in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("use trigger:lock pairs") from exc
        if any(not 0 < lock <= trigger <= 100 for trigger, lock in levels):
            raise ValueError("each level must satisfy 0 < lock <= trigger <= 100")
        if any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(levels, levels[1:], strict=False)):
            raise ValueError("trailing triggers and locks must increase strictly")
        return levels

    @field_validator("account_currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3,8}", value):
            raise ValueError("invalid account currency code")
        return value

    @field_validator("trading_day_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def safety_invariants(self) -> Self:
        if not self.start_paused or not self.require_stop_loss or not self.require_stage_gates:
            raise ValueError("startup pause, broker SL and stage gates cannot be disabled")
        if self.live_trading:
            if self.demo_mode or self.paper_trading or self.backtest_mode or self.mt5_backend != "real":
                raise ValueError(
                    "live requires DEMO_MODE=false, PAPER_TRADING=false, BACKTEST_MODE=false and real backend"
                )
            if not self.telegram_bot_token.get_secret_value() or not self.telegram_owner_id:
                raise ValueError("live requires owner-only Telegram controls")
            if not (
                self.news_required_for_entry
                and self.use_economic_calendar
                and self.block_trading_high_impact_news
            ):
                raise ValueError("live requires fresh news, calendar and high-impact blocking")
        elif self.backtest_mode:
            if self.paper_trading or not self.demo_mode or self.mt5_backend != "mock":
                raise ValueError("backtest requires PAPER_TRADING=false, DEMO_MODE=true and mock backend")
        elif self.paper_trading:
            if not self.demo_mode:
                raise ValueError("paper requires DEMO_MODE=true")
        elif not self.demo_mode or self.mt5_backend != "real":
            raise ValueError("broker demo requires DEMO_MODE=true and real backend")
        if self.mode in {OperatingMode.DEMO, OperatingMode.LIVE} and self.ai_provider == "disabled":
            raise ValueError("broker execution cannot bypass AI confidence gating")
        if self.live_max_risk_percent_per_trade > self.max_risk_percent_per_trade:
            raise ValueError("live risk cap cannot exceed the account risk cap")
        if self.max_risk_percent_per_trade > self.max_daily_loss_percent:
            raise ValueError("trade risk cannot exceed daily loss cap")
        if self.max_total_open_risk_percent > self.max_drawdown_percent:
            raise ValueError("open risk cannot exceed drawdown cap")
        if self.max_daily_loss_percent > self.max_drawdown_percent:
            raise ValueError("daily loss cap cannot exceed drawdown cap")
        if self.min_daily_trades_target > self.max_daily_trades:
            raise ValueError("trade-count target cannot exceed daily limit")
        if self.target_r_multiple < self.min_net_reward_risk:
            raise ValueError("dynamic target multiple cannot bypass the net reward/risk floor")
        if TIMEFRAME_MINUTES[self.higher_timeframe] <= TIMEFRAME_MINUTES[self.primary_timeframe]:
            raise ValueError("higher timeframe must exceed primary timeframe")
        if TIMEFRAME_MINUTES[self.trend_timeframe] < TIMEFRAME_MINUTES[self.higher_timeframe]:
            raise ValueError("trend timeframe must be at least the higher timeframe")
        if self.model_embargo_bars < self.model_label_horizon_bars:
            raise ValueError("validation embargo must cover the full label horizon")
        if self.watchdog_stale_seconds < 3 * self.heartbeat_interval_seconds:
            raise ValueError("watchdog staleness must allow at least three heartbeats")
        credentials = (
            bool(self.mt5_login),
            bool(self.mt5_password.get_secret_value()),
            bool(self.mt5_server),
        )
        if any(credentials) and not all(credentials):
            raise ValueError("supply all MT5 login/password/server values or leave all empty")
        if bool(self.telegram_bot_token.get_secret_value()) != bool(self.telegram_owner_id):
            raise ValueError("Telegram token and owner ID must be configured together")
        for url in (self.telegram_miniapp_url, self.telegram_webhook_url):
            if url:
                _valid_url(url, https_only=True)
        if self.telegram_use_webhook and not (
            self.telegram_webhook_url and self.telegram_webhook_secret.get_secret_value()
        ):
            raise ValueError("webhook mode requires an HTTPS URL and webhook secret")
        _valid_url(self.ollama_base_url)
        if not self.allow_remote_ollama and urlparse(self.ollama_base_url).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("remote Ollama is disabled")
        if self.allow_remote_ollama:
            _valid_url(self.ollama_base_url, allow_local_http=True)
        _valid_url(self.openai_base_url, allow_local_http=True)
        for url in self.rss_urls + self.api_cors_origins:
            _valid_url(url, https_only=True)
        if self.calendar_source_url:
            _valid_url(self.calendar_source_url, https_only=True)
        if not self.api_trusted_hosts or "*" in self.api_trusted_hosts:
            raise ValueError("explicit API trusted hosts are required; wildcard is forbidden")
        for symbol, limit in self.symbol_spread_limits.items():
            if not symbol or not 0 <= limit <= 100000:
                raise ValueError("invalid per-symbol spread limit")
        for name, alias in self.symbol_aliases.items():
            # Native broker names may contain spaces/punctuation. Logical names
            # remain filename-safe; aliases are passed only as literal API values.
            if (
                name not in self.symbols
                or not alias.strip()
                or len(alias) > 64
                or any(ord(char) < 32 or ord(char) == 127 for char in alias)
            ):
                raise ValueError("aliases require an enabled logical symbol and valid broker name")
        resolved = tuple(self.symbol_aliases.get(symbol, symbol) for symbol in self.symbols)
        if len(set(resolved)) != len(resolved):
            raise ValueError("two logical symbols cannot resolve to the same broker symbol")
        for currencies in self.symbol_news_currencies.values():
            if not currencies or any(not re.fullmatch(r"[A-Z]{3}", item) for item in currencies):
                raise ValueError("news exposure requires ISO currency codes")
        for path in (self.data_dir, self.backup_dir, self.log_file, self.calendar_file):
            self.resolve_path(path)
        for name in (
            "symbol_spread_limits",
            "symbol_aliases",
            "symbol_news_currencies",
            "account_to_usd_symbols",
        ):
            object.__setattr__(self, name, FrozenDict(getattr(self, name)))
        return self

    @property
    def mode(self) -> OperatingMode:
        if self.live_trading:
            return OperatingMode.LIVE
        if self.backtest_mode:
            return OperatingMode.BACKTEST
        if self.paper_trading:
            return OperatingMode.PAPER
        return OperatingMode.DEMO

    @property
    def effective_risk_percent(self) -> Decimal:
        return (
            min(self.max_risk_percent_per_trade, self.live_max_risk_percent_per_trade)
            if self.mode == OperatingMode.LIVE
            else self.max_risk_percent_per_trade
        )

    def resolve_path(self, path: str | Path) -> Path:
        root = self.project_root.resolve()
        candidate = (root / Path(path)).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("runtime paths must remain inside the project directory")
        return candidate

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.backup_dir, self.log_file.parent, self.calendar_file.parent):
            self.resolve_path(path).mkdir(parents=True, exist_ok=True)
        for child in ("candles", "news", "models", "logs", "backups"):
            (self.resolve_path(self.data_dir) / child).mkdir(parents=True, exist_ok=True)

    def safety_snapshot(self) -> dict[str, object]:
        # Include all strategy/risk/provider settings, exclude credentials and
        # infrastructure-only fields. Effective runtime overrides must be hashed too.
        infrastructure = {
            "project_root",
            "database_url",
            "data_dir",
            "backup_dir",
            "log_file",
            "log_level",
            "log_max_bytes",
            "log_backup_count",
            "api_host",
            "api_port",
            "api_trusted_hosts",
            "api_cors_origins",
            "telegram_miniapp_url",
            "telegram_webhook_url",
            "telegram_use_webhook",
            "mt5_terminal_path",
            "database_busy_timeout_ms",
        }
        values = self.model_dump(mode="json")
        excluded = infrastructure | {
            name for name in type(self).model_fields if isinstance(getattr(self, name), SecretStr)
        }
        return {
            **{key: value for key, value in values.items() if key not in excluded},
            "mode": self.mode.value,
        }

    def safety_fingerprint(self) -> str:
        return sha256_json(self.safety_snapshot())

    def strategy_fingerprint(self) -> str:
        # Stage evidence can span paper/demo/live and different account logins.
        # Owner execution approvals use safety_fingerprint PLUS actual account/session.
        snapshot = self.safety_snapshot()
        for name in (
            "mode",
            "backtest_mode",
            "demo_mode",
            "live_trading",
            "paper_trading",
            "mt5_backend",
            "mt5_login",
            "mt5_server",
            "telegram_owner_id",
        ):
            snapshot.pop(name, None)
        return sha256_json(snapshot)

    def public_config(self) -> dict[str, object]:
        # Strict allowlist. Never expose model_dump() to an API or to logs.
        return {
            "mode": self.mode.value,
            "backend": self.mt5_backend,
            "startup": "paused",
            "symbols": list(self.symbols),
            "risk_percent": str(self.max_risk_percent_per_trade),
            "live_risk_percent": str(self.live_max_risk_percent_per_trade),
            "effective_risk_percent": str(self.effective_risk_percent),
            "daily_loss_percent": str(self.max_daily_loss_percent),
            "drawdown_percent": str(self.max_drawdown_percent),
            "max_daily_trades": self.max_daily_trades,
            "min_daily_trades_target": self.min_daily_trades_target,
            "max_open_positions": self.max_open_positions,
            "target_profit_usd": str(self.target_profit_usd_per_trade),
            "min_net_reward_risk": str(self.min_net_reward_risk),
            "timeframes": [self.primary_timeframe, self.higher_timeframe, self.trend_timeframe],
            "trailing_levels": self.trailing_levels,
            "news_required": self.news_required_for_entry,
            "telegram_configured": bool(self.telegram_bot_token.get_secret_value()),
            "ai_provider": self.ai_provider,
            "config_fingerprint": self.safety_fingerprint(),
            "strategy_fingerprint": self.strategy_fingerprint(),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

## `core/models.py`

```python
"""Portable SQLAlchemy schema with exact decimals and UTC-aware timestamps."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive timestamps are forbidden")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ExactDecimal(TypeDecorator[Decimal]):
    # SQLite NUMERIC affinity silently uses binary floats. Store decimal strings
    # there; aggregate money in Python Decimal, never SQL SUM(text_column).
    impl = String(64)
    cache_ok = True

    def __init__(self, scale: int = 8) -> None:
        if not 0 <= scale <= 12:
            raise ValueError("invalid decimal scale")
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(28, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | str | int | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (float, bool)):
            raise TypeError("use Decimal(str(value)), not floats/bools, for financial columns")
        number = Decimal(value)
        if not number.is_finite() or abs(number) >= Decimal(10) ** (28 - self.scale):
            raise ValueError("financial value is non-finite or exceeds precision")
        try:
            number = number.quantize(Decimal(1).scaleb(-self.scale), rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError("financial value exceeds precision") from exc
        return format(number, "f") if dialect.name == "sqlite" else number

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(str(value))


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    __table_args__ = (CheckConstraint("id = 1", name="ck_schema_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BrokerDeal(Base):
    """Deduplicated fill/cash-flow ledger; reads deposits, never initiates them."""

    __tablename__ = "broker_deals"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_deal_account_ticket"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_deal_mode"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    ticket: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    entry: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    magic: Mapped[int] = mapped_column(BigInteger, default=0)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(ExactDecimal(12), default=Decimal("0"))
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    fee: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    comment: Mapped[str] = mapped_column(String(128), default="")


class OrderIntent(Base):
    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_intent_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_intent_direction"),
        CheckConstraint(
            "state IN ('prepared','submitting','acknowledged','rejected','unknown','reconciled','canceled')",
            name="ck_intent_state",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="prepared", index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    broker_request_id: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_key", "mode", "ticket", name="uq_trade_account_ticket"),
        UniqueConstraint("account_key", "mode", "position_identifier", name="uq_trade_position_id"),
        CheckConstraint("mode IN ('backtest','paper','demo','live')", name="ck_trade_mode"),
        CheckConstraint("direction IN ('buy','sell')", name="ck_trade_direction"),
        CheckConstraint("status IN ('open','closed','unknown')", name="ck_trade_status"),
        CheckConstraint("CAST(volume AS NUMERIC) > 0", name="ck_trade_volume"),
        CheckConstraint(
            "signal_score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_trade_scores"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket: Mapped[int | None] = mapped_column(BigInteger)
    position_identifier: Mapped[int | None] = mapped_column(BigInteger, index=True)
    order_intent_id: Mapped[str] = mapped_column(ForeignKey("order_intents.id"), unique=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    sl: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    tp: Mapped[Decimal] = mapped_column(ExactDecimal(12), nullable=False)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    profit: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    initial_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    target_profit_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    profit_lock_level: Mapped[float] = mapped_column(Float, default=0)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, nullable=False)
    news_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    comment: Mapped[str] = mapped_column(String(128), default="")
    close_reason: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "mode", "symbol", "timeframe", "bar_time", "strategy", "config_hash", name="uq_signal_bar"
        ),
        CheckConstraint("direction IN ('buy','sell','wait')", name="ck_signal_direction"),
        CheckConstraint("score BETWEEN 0 AND 100 AND ai_score BETWEEN 0 AND 100", name="ck_signal_scores"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    bar_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, default=0)
    final_decision: Mapped[str] = mapped_column(String(24), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class News(Base):
    __tablename__ = "news"
    __table_args__ = (
        CheckConstraint("impact IN ('low','medium','high','unknown')", name="ck_news_impact"),
        CheckConstraint("sentiment BETWEEN -1 AND 1", name="ck_news_sentiment"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(String(10), default="unknown")
    sentiment: Mapped[float] = mapped_column(Float, default=0)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AISuggestion(Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low','medium','high')", name="ck_suggestion_risk"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','expired')", name="ck_suggestion_status"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    suggestion: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), default="high")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    based_on_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_scope: Mapped[str] = mapped_column(String(12), default="candidate")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BotState(Base):
    __tablename__ = "bot_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_state_singleton"),
        CheckConstraint("desired_state IN ('paused','running','killed')", name="ck_state_name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    desired_state: Mapped[str] = mapped_column(String(10), default="paused")
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    session_id: Mapped[str | None] = mapped_column(String(36))
    settings_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_config_hash: Mapped[str | None] = mapped_column(String(64))
    heartbeat: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    balance: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))


class RiskState(Base):
    __tablename__ = "risk_state"
    __table_args__ = (UniqueConstraint("account_key", "mode", name="uq_risk_account_mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    day_start_equity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    equity_high_water: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    net_realized_today: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    accepted_entries_today: Mapped[int] = mapped_column(Integer, default=0)
    reserved_risk_usd: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    daily_loss_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    drawdown_latched: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_flow_total: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal("0"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DeploymentEvidence(Base):
    __tablename__ = "deployment_evidence"
    __table_args__ = (CheckConstraint("stage IN ('backtest','paper','demo')", name="ck_evidence_stage"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str | None] = mapped_column(String(160))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OwnerApproval(Base):
    __tablename__ = "owner_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','consumed','expired')", name="ck_approval_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_is_append_only(*args: Any) -> None:
    raise RuntimeError("audit logs are append-only")
```

## `core/database.py`

```python
"""Explicit schema initialization; no runtime auto-migration or global sessions."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import AuditLog, Base, BotState, SchemaVersion
from core.security import canonical_json, sanitize_data, secret_values
from core.settings import OperatingMode, Settings

SCHEMA_VERSION = 1
AUDIT_TRIGGERS = {
    "audit_logs_no_update": "UPDATE",
    "audit_logs_no_delete": "DELETE",
}


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.secrets = secret_values(settings)
        url = make_url(settings.database_url.get_secret_value())
        options: dict[str, Any] = {"pool_pre_ping": True, "echo": False}
        if url.get_backend_name() == "sqlite":
            if url.query:
                raise ValueError("SQLite URI/query overrides are forbidden")
            if url.database == ":memory:":
                if settings.mode in {OperatingMode.DEMO, OperatingMode.LIVE}:
                    raise ValueError("broker execution requires persistent risk state")
                options["poolclass"] = StaticPool
            else:
                if not url.database:
                    raise ValueError("SQLite database path is required")
                path = settings.resolve_path(url.database)
                path.parent.mkdir(parents=True, exist_ok=True)
                url = url.set(database=str(path))
            options["connect_args"] = {
                "check_same_thread": False,
                "timeout": settings.database_busy_timeout_ms / 1000,
            }
        elif url.get_backend_name() == "postgresql":
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            if url.drivername != "postgresql+psycopg":
                raise ValueError("PostgreSQL support requires psycopg 3")
            options["connect_args"] = {"connect_timeout": 10}
        else:
            raise ValueError("only SQLite and PostgreSQL are supported")
        self.engine: Engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._sqlite_connect)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)

    def _sqlite_connect(self, connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={self.settings.database_busy_timeout_ms}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        # A new session per operation/thread; callers can atomically write a
        # decision, order intent, reserved risk and its audit entry together.
        with self.session_factory.begin() as session:
            yield session

    def initialize(self) -> None:
        """Operator command only. Existing schemas are checked, never altered."""
        existing = set(inspect(self.engine).get_table_names())
        if existing:
            self.verify_schema()
            return
        with self.engine.begin() as connection:
            Base.metadata.create_all(connection)
            self._install_audit_triggers(connection)
        with self.session() as session:
            session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            session.add(BotState(id=1, desired_state="paused", kill_switch_active=False))
            self.add_audit(session, "database.initialized", "operator", {"schema_version": SCHEMA_VERSION})
        self.verify_schema()

    def _install_audit_triggers(self, connection: Connection) -> None:
        if connection.dialect.name != "sqlite":
            return
        for name, operation in AUDIT_TRIGGERS.items():
            # Both identifiers are fixed constants, never user input.
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE {operation} ON audit_logs "
                "BEGIN SELECT RAISE(ABORT, 'audit logs are append-only'); END"
            )

    def verify_schema(self) -> None:
        inspector = inspect(self.engine)
        actual = set(inspector.get_table_names())
        expected = set(Base.metadata.tables)
        if not expected.issubset(actual):
            raise RuntimeError(
                "database is uninitialized or incomplete; run init-db on an empty dedicated database"
            )
        with self.session() as session:
            versions = session.scalars(select(SchemaVersion)).all()
            if len(versions) != 1 or versions[0].id != 1 or versions[0].version != SCHEMA_VERSION:
                raise RuntimeError("database schema version mismatch; apply a reviewed migration")
            if session.get(BotState, 1) is None:
                raise RuntimeError("persistent bot state is missing; refuse to trade")
        for name, table in Base.metadata.tables.items():
            if {column["name"] for column in inspector.get_columns(name)} != set(table.columns.keys()):
                raise RuntimeError("database schema drift detected; refuse to trade")
        if self.engine.dialect.name == "sqlite":
            with self.engine.connect() as connection:
                triggers = set(
                    connection.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
                )
            if not set(AUDIT_TRIGGERS).issubset(triggers):
                raise RuntimeError("append-only audit protection is missing")

    def add_audit(self, session: Session, action: str, source: str, details: dict[str, Any]) -> AuditLog:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", action) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,64}", source
        ):
            raise ValueError("invalid audit action/source")
        clean = sanitize_data(details, self.secrets)
        if len(canonical_json(clean).encode("utf-8")) > 16384:
            raise ValueError("audit payload exceeds 16 KiB")
        row = AuditLog(action=action, source=source, details=clean)
        session.add(row)
        return row

    def audit(self, action: str, source: str, details: dict[str, Any]) -> None:
        with self.session() as session:
            self.add_audit(session, action, source, details)

    def status(self) -> dict[str, Any]:
        self.verify_schema()
        with self.session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise RuntimeError("persistent bot state disappeared; refuse to trade")
            return {
                "database": "ok",
                "schema_version": SCHEMA_VERSION,
                "mode": self.settings.mode.value,
                "state": state.desired_state,
                "kill_switch": state.kill_switch_active,
                "revision": state.revision,
                "heartbeat": state.heartbeat.isoformat() if state.heartbeat else None,
            }

    def close(self) -> None:
        self.engine.dispose()
```

## `core/security.py`

```python
"""Secret-safe JSON and logging helpers. Authentication is added in Part 9."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from pydantic import SecretStr

SENSITIVE_KEY = re.compile(
    r"password|passwd|token|api[_-]?key|secret|authorization|cookie|init[_-]?data|private[_-]?key",
    re.IGNORECASE,
)
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|token|api[_-]?key|secret|authorization|init[_-]?data)\b"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
BOT_URL_TOKEN = re.compile(r"(https?://api\.telegram\.org/(?:file/)?bot)[^/\s?]+", re.IGNORECASE)
URL_CREDENTIALS = re.compile(r"(https?://|postgresql(?:\+psycopg)?://)([^/@\s]+@)", re.IGNORECASE)


def secret_values(settings: object) -> tuple[str, ...]:
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if raw:
                values.append(raw)
    # Longer values first prevents partial redaction from exposing a suffix.
    return tuple(sorted(set(values), key=len, reverse=True))


def sanitize_text(text: object, secrets: Iterable[str] = ()) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = BEARER.sub("Bearer [REDACTED]", result)
    result = BOT_URL_TOKEN.sub(r"\1[REDACTED]", result)
    result = URL_CREDENTIALS.sub(r"\1[REDACTED]@", result)
    return ASSIGNMENT.sub(r"\1[REDACTED]", result)


def _json_default(value: object) -> object:
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite numbers are forbidden")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_data(value: Any, secrets: Iterable[str] = (), *, _depth: int = 0) -> Any:
    if _depth > 20:
        raise ValueError("audit payload exceeds nesting limit")
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("audit JSON keys must be strings")
        return {
            key: "[REDACTED]"
            if SENSITIVE_KEY.search(key)
            else sanitize_data(item, secrets, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item, secrets, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    if isinstance(value, (Decimal, datetime, Enum)):
        return _json_default(value)
    if value is None or isinstance(value, (bool, int, float)):
        # Final canonical_json check rejects NaN/Infinity.
        return value
    raise TypeError(f"unsupported audit type: {type(value).__name__}")
```

## `core/logging_setup.py`

```python
"""UTC JSON-lines rotating logs, including secret-safe tracebacks."""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from core.security import canonical_json, sanitize_text, secret_values

if TYPE_CHECKING:
    from core.settings import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        # Strict field allowlist; arbitrary logging extra/request bodies are not
        # serialized. JSON escapes newlines to prevent forged log entries.
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = "Invalid log formatting; raw body and arguments suppressed"
        payload = {
            "time": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": sanitize_text(record.name, self.secrets),
            "thread": sanitize_text(record.threadName, self.secrets),
            "message": sanitize_text(message, self.secrets),
        }
        if record.exc_info:
            payload["exception"] = sanitize_text(self.formatException(record.exc_info), self.secrets)
        if record.stack_info:
            payload["stack"] = sanitize_text(record.stack_info, self.secrets)
        return canonical_json(payload)


def configure_logging(settings: Settings) -> None:
    settings.ensure_runtime_dirs()
    formatter = RedactingFormatter(secret_values(settings))
    file_handler = RotatingFileHandler(
        settings.resolve_path(settings.log_file),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
        delay=False,
    )
    console_handler = logging.StreamHandler(sys.stderr)
    # Prevent logging internals from dumping raw args on formatter/I/O errors.
    logging.raiseExceptions = False
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.setLevel(settings.log_level)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # HTTP DEBUG logging can contain complete request data; never enable it.
    for name in ("httpx", "httpcore", "sqlalchemy.engine", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.WARNING)


def recent_log_lines(settings: Settings, limit: int = 100) -> list[str]:
    if not 1 <= limit <= 500:
        raise ValueError("log limit must be between 1 and 500")
    path = settings.resolve_path(settings.log_file)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=limit)
    secrets = secret_values(settings)
    return [sanitize_text(line.rstrip("\n"), secrets) for line in lines]
```

## `main.py`

```python
"""Installment 1 operator CLI. It cannot connect to MT5 or place orders."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from core.database import Database
from core.logging_setup import configure_logging
from core.settings import Settings, get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MT5 AI ReflexBot: safe foundation commands")
    parser.add_argument("command", choices=("check-config", "init-db", "status"))
    parser.add_argument("--env-file", type=Path, help="explicit .env path; must exist")
    args = parser.parse_args(argv)
    if args.env_file and not args.env_file.is_file():
        print("Explicit environment file does not exist.", file=sys.stderr)
        return 2
    try:
        settings = Settings(_env_file=args.env_file) if args.env_file else get_settings()
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "mode/safety invariants"
                for error in exc.errors()
            }
        )
        # Do not print pydantic error dictionaries: they can contain raw inputs.
        print("Invalid configuration in: " + ", ".join(fields), file=sys.stderr)
        return 2
    except (SettingsError, ValueError) as exc:
        print(f"Configuration loading failed ({type(exc).__name__}); check .env syntax.", file=sys.stderr)
        return 2
    database: Database | None = None
    try:
        configure_logging(settings)
        if args.command == "check-config":
            print(json.dumps(settings.public_config(), indent=2))
            return 0
        database = Database(settings)
        if args.command == "init-db":
            database.initialize()
        print(json.dumps(database.status(), indent=2))
        return 0
    except Exception:
        logging.getLogger("reflexbot.operator").exception(
            "Foundation command failed; no broker execution exists"
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

## Repository and test configuration

## `.gitignore`

```gitignore
# Secrets
.env
.env.*
!.env.example
*.pem
*.key

# Python / tools
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage/
htmlcov/
*.egg-info/

# Local runtime state, market data, models, logs and backups
data/**
!data/**/
!data/**/.gitkeep
*.db
*.db-shm
*.db-wal
*.sqlite*

# Build artifacts / OS
build/
dist/
*.spec
.DS_Store
Thumbs.db
.vscode/
.idea/
# Lock files may contain private package-index credentials; review before commit.
requirements.*.lock.txt
```

## `pyproject.toml`

```toml
[tool.pytest.ini_options]
minversion = "8.2"
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.ruff]
target-version = "py311"
line-length = 110

[tool.ruff.lint]
select = ["E", "F", "I", "B"]
```

## Executable foundation tests (additional to the trading tests in Part 11)

## `tests/conftest.py`

```python
"""Isolate tests from any real credentials or mode flags in the host environment."""

import pytest

from core.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # Env-file loading is disabled explicitly in each test.
    import os

    names = {name.lower() for name in Settings.model_fields}
    names |= {
        field.validation_alias.lower()
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }
    for name in list(os.environ):
        if name.lower() in names:
            monkeypatch.delenv(name)
```

## `tests/test_settings.py`

```python
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.settings import OperatingMode, Settings


def config(**values):
    return Settings(_env_file=None, **values)


def test_safe_defaults():
    settings = config()
    assert settings.mode == OperatingMode.PAPER
    assert settings.mt5_backend == "mock"
    assert settings.demo_mode and settings.start_paused and settings.require_stage_gates
    assert not settings.live_trading
    assert settings.live_max_risk_percent_per_trade == Decimal("0.1")


def test_complete_env_example_is_valid():
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env.example")
    assert settings.mode == OperatingMode.PAPER
    assert settings.symbols == ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    assert settings.trailing_levels == ((30, 30), (60, 60), (90, 90))


@pytest.mark.parametrize(
    "flags",
    [
        {"live_trading": True},
        {"demo_mode": False},
        {"backtest_mode": True},
        {"paper_trading": False},
        {"start_paused": False},
        {"require_stage_gates": False},
        {"require_stop_loss": False},
    ],
)
def test_ambiguous_or_unsafe_flags_are_rejected(flags):
    with pytest.raises(ValidationError):
        config(**flags)


def test_explicit_modes_do_not_imply_authorization():
    assert config(backtest_mode=True, paper_trading=False).mode == OperatingMode.BACKTEST
    assert config(paper_trading=False, mt5_backend="real").mode == OperatingMode.DEMO
    live = config(
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
    )
    assert live.mode == OperatingMode.LIVE
    assert live.public_config()["startup"] == "paused"


@pytest.mark.parametrize(
    "values",
    [
        {"max_risk_percent_per_trade": "1.01"},
        {"live_max_risk_percent_per_trade": "0.6"},
        {"min_daily_trades_target": 13},
        {"max_same_symbol_positions": 2},
        {"model_embargo_bars": 11},
        {"max_daily_loss_percent": "11"},
        {"target_r_multiple": "1"},
        {"trailing_levels": "60:60,30:30"},
        {"trailing_levels": "30:40"},
        {"symbols": "XAUUSD,XAUUSD"},
        {"symbols": "../secret"},
        {"telegram_bot_token": "token-without-owner"},
        {"log_file": "../outside.log"},
        {"ollama_base_url": "http://remote.example"},
        {"openai_base_url": "http://remote.example/v1"},
        {"API_TRUSTED_HOSTS_JSON": ["*"]},
    ],
)
def test_risk_and_security_configuration_rejected(values):
    with pytest.raises(ValidationError):
        config(**values)


def test_config_is_frozen():
    settings = config()
    with pytest.raises(ValidationError):
        settings.live_trading = True


def test_secrets_excluded_from_public_config_and_hash():
    first = config(telegram_bot_token="123:TOP-SECRET-A", telegram_owner_id=123)
    second = config(telegram_bot_token="123:TOP-SECRET-B", telegram_owner_id=123)
    assert "TOP-SECRET" not in str(first.public_config())
    assert first.safety_fingerprint() == second.safety_fingerprint()
    assert first.safety_fingerprint() != config(max_daily_trades=11).safety_fingerprint()


def test_unknown_dotenv_key_is_rejected(tmp_path):
    path = tmp_path / ".env"
    path.write_text("LIVE_TRDAING=true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=path)


def test_nested_config_is_read_only():
    settings = config(symbol_aliases={"XAUUSD": "XAUUSDm"})
    with pytest.raises(TypeError):
        settings.symbol_aliases["XAUUSD"] = "other"
    with pytest.raises(TypeError):
        settings.symbol_news_currencies["XAUUSD"] = ("EUR",)
    assert isinstance(settings.symbol_news_currencies["XAUUSD"], tuple)
    assert isinstance(settings.rss_urls, tuple)


def test_stage_fingerprint_spans_modes_but_approval_fingerprint_does_not():
    paper = config(telegram_bot_token="123:test-token", telegram_owner_id=123)
    live = config(
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
    )
    assert paper.strategy_fingerprint() == live.strategy_fingerprint()
    assert paper.safety_fingerprint() != live.safety_fingerprint()
    assert live.effective_risk_percent == Decimal("0.1")


def test_lower_r_targets_require_explicit_lower_reward_risk_floor():
    with pytest.raises(ValidationError):
        config(target_r_multiple="0.35")
    lab = config(target_r_multiple="0.35", min_net_reward_risk="0.3")
    assert lab.target_r_multiple == Decimal("0.35")


def test_alias_collision_rejected():
    with pytest.raises(ValidationError):
        config(symbols="XAUUSD,GOLD", symbol_aliases={"GOLD": "XAUUSD"})


def test_literal_broker_alias_with_spaces_supported():
    settings = config(symbols="V75", symbol_aliases={"V75": "Volatility 75 Index"})
    assert settings.symbol_aliases["V75"] == "Volatility 75 Index"
    with pytest.raises(ValidationError):
        config(symbols="V75", symbol_aliases={"V75": "Volatility\n75"})
```

## `tests/test_database.py`

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, StatementError

from core.database import Database
from core.models import AccountSnapshot, AuditLog, BotState, SchemaVersion
from core.settings import Settings


@pytest.fixture
def database(tmp_path):
    settings = Settings(_env_file=None, project_root=tmp_path)
    db = Database(settings)
    db.initialize()
    yield db
    db.close()


def test_new_database_paused_and_idempotent(database):
    assert database.status()["state"] == "paused"
    assert database.status()["heartbeat"] is None
    database.initialize()
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_persistent_kill_switch_survives_reopen(database):
    with database.session() as session:
        state = session.get(BotState, 1)
        state.desired_state = "killed"
        state.kill_switch_active = True
    second = Database(database.settings)
    try:
        second.initialize()
        assert second.status()["kill_switch"]
        assert second.status()["state"] == "killed"
    finally:
        second.close()


def test_exact_decimals_and_utc_round_trip(database):
    moment = datetime(2026, 1, 1, 12, 34, 56, tzinfo=timezone.utc)
    money = Decimal("123456789012345.12345678")
    with database.session() as session:
        session.add(
            AccountSnapshot(
                time=moment,
                account_key="paper:test",
                mode="paper",
                currency="USD",
                balance=money,
                equity=money,
                margin=Decimal("0"),
                free_margin=money,
            )
        )
    with database.session() as session:
        row = session.scalar(select(AccountSnapshot))
        assert row.balance == money
        assert row.time == moment
        assert row.time.tzinfo is not None


def test_naive_timestamp_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(AuditLog(time=datetime(2026, 1, 1), action="invalid", source="test", details={}))


def test_financial_float_rejected(database):
    with pytest.raises(StatementError):
        with database.session() as session:
            session.add(
                AccountSnapshot(
                    account_key="paper:test",
                    mode="paper",
                    currency="USD",
                    balance=1.23,
                    equity=Decimal("1.23"),
                    margin=Decimal("0"),
                    free_margin=Decimal("1.23"),
                )
            )


def test_audit_redacted(database):
    database.audit("test.event", "test", {"MT5_PASSWORD": "secret", "safe": "value"})
    with database.session() as session:
        row = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert row.details == {"MT5_PASSWORD": "[REDACTED]", "safe": "value"}


@pytest.mark.parametrize("statement", ["UPDATE audit_logs SET action='changed'", "DELETE FROM audit_logs"])
def test_sqlite_audit_append_only(database, statement):
    with pytest.raises(DBAPIError):
        with database.engine.begin() as connection:
            connection.execute(text(statement))


def test_schema_version_mismatch_refuses_startup(database):
    with database.session() as session:
        session.execute(update(SchemaVersion).values(version=999))
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        database.verify_schema()


def test_missing_state_refuses_startup(database):
    with database.session() as session:
        session.delete(session.get(BotState, 1))
    with pytest.raises(RuntimeError, match="persistent bot state"):
        database.verify_schema()


def test_transaction_rollback(database):
    with pytest.raises(RuntimeError):
        with database.session() as session:
            database.add_audit(session, "test.rollback", "test", {})
            raise RuntimeError("rollback")
    with database.session() as session:
        assert len(session.scalars(select(AuditLog)).all()) == 1


def test_unknown_schema_is_not_silently_modified(tmp_path):
    db = Database(Settings(_env_file=None, project_root=tmp_path))
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated (id INTEGER)"))
        with pytest.raises(RuntimeError):
            db.initialize()
    finally:
        db.close()
```

## `tests/test_security_logging.py`

```python
import io
import logging
import math
from decimal import Decimal

import pytest

from core.logging_setup import RedactingFormatter
from core.security import canonical_json, sanitize_data, sanitize_text, sha256_json


def test_nested_audit_secret_redaction():
    result = sanitize_data(
        {"password": "dont-store", "nested": {"initData": "raw-init-data"}, "reason": "secret-from-env"},
        ("secret-from-env",),
    )
    assert result == {"password": "[REDACTED]", "nested": {"initData": "[REDACTED]"}, "reason": "[REDACTED]"}


def test_traceback_and_message_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(("env-secret",)))
    logger = logging.getLogger("reflexbot.test.redaction")
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    try:
        raise RuntimeError("password=env-secret https://api.telegram.org/bot123:LEAK/sendMessage")
    except RuntimeError:
        logger.exception("Authorization: Bearer abc123")
    text = stream.getvalue()
    assert "env-secret" not in text
    assert "123:LEAK" not in text
    assert "abc123" not in text
    assert "Traceback" in text
    logger.handlers.clear()
    handler.close()


def test_url_and_unknown_assignment_redacted():
    value = sanitize_text("postgresql+psycopg://user:pass@localhost/db api_key=abcd")
    assert "user:pass" not in value and "abcd" not in value


def test_canonical_hash_stable():
    assert sha256_json({"b": 2, "a": 1}) == sha256_json({"a": 1, "b": 2})
    assert canonical_json({"money": Decimal("0.10")}) == '{"money":"0.10"}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, Decimal("NaN")])
def test_nonfinite_json_rejected(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


def test_deep_audit_payload_rejected():
    value = {}
    for _ in range(22):
        value = {"nested": value}
    with pytest.raises(ValueError):
        sanitize_data(value)


def test_log_message_newline_does_not_forge_another_entry():
    import json

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "line one\nFAKE ENTRY", (), None)
    output = RedactingFormatter().format(record)
    assert len(output.splitlines()) == 1
    assert json.loads(output)["message"] == "line one\nFAKE ENTRY"


def test_malformed_logging_does_not_dump_secret_arguments():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "%d", ("raw-secret",), None)
    output = RedactingFormatter().format(record)
    assert "raw-secret" not in output
    assert "suppressed" in output
```

## `tests/test_foundation_cli.py`

```python
import json

from main import main


def test_check_config_does_not_init_database(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text(f'PROJECT_ROOT="{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "paper" and output["startup"] == "paused"
    assert not (tmp_path / "data/reflexbot.db").exists()


def test_explicit_missing_env_file_fails(tmp_path):
    assert main(["check-config", "--env-file", str(tmp_path / "missing.env")]) == 2


def test_invalid_config_output_does_not_expose_input(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text('TELEGRAM_OWNER_ID="not-an-id-secret"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 2
    assert "not-an-id-secret" not in capsys.readouterr().err
```

## Executed validation and reproducibility


Environment: Linux x64, CPython 3.13.14 in an isolated venv. The reference
Windows broker installation remains Python 3.11 x64 and has NOT been tested.

Commands executed:

```text
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Dependency installation succeeded for the complete direct dependency list on
Linux (MetaTrader5 skipped by platform marker). `pip check` reported no broken
requirements. Foundation suite: **60 passed**. Ruff formatting/lint checks and Python
compileall also passed. CLI `check-config`, `init-db`, and `status` were executed
with .env.example: paper/mock, paused, heartbeat null.

Tests cover:

- default paper/mock/paused policy and the complete .env.example;
- conflicting live/demo/paper/backtest flags and required safety controls;
- hard risk limits, bounded trailing configuration, validation embargo;
- owner/token pairing, TLS URL rules, trusted hosts and runtime path boundaries;
- scalar and nested-map configuration immutability;
- credential exclusion, stable policy hashes and cross-mode stage hashes;
- SQLite schema creation, UTC and exact financial Decimal round trips;
- persisted kill switch, append-only audit triggers and transaction rollback;
- missing state/schema mismatch and unknown-schema fail-closed behavior;
- redaction of nested audit fields, messages, token URLs and tracebacks;
- rejection of non-finite/over-nested JSON;
- CLI no-order/no-database behavior for configuration inspection.

The pinned MetaTrader5 release was checked for an available Windows CPython
3.11 x64 wheel via package metadata. The package was NOT imported or executed
against a terminal. No MT5 connection, broker account, trading orders, paper
strategy loop, AI request, news fetch, Telegram API request or Mini App server
has been exercised. PostgreSQL is optional and has not been server-tested.

This is verification of foundation behavior, not profitability or production
certification. Parts 4–11 add broker/integration/simulation tests and promotion
criteria; real demo and owner-approved staged testing remain mandatory.


This Linux-only lock records the tested development environment, including Ruff.
It intentionally does NOT include the Windows MT5 package; generate a separate
Windows lock after installing and testing `requirements.txt` there.

## `requirements.linux.lock.txt`

```text
aiofiles==25.1.0
aiogram==3.31.0
aiohappyeyeballs==2.7.1
aiohttp==3.14.3
aiosignal==1.4.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
APScheduler==3.11.3
attrs==26.1.0
certifi==2026.7.22
click==8.5.0
cloudpickle==3.1.2
defusedxml==0.7.1
fastapi==0.142.2
feedparser==6.0.14
feedparser-sgmllib==2.1.0
frozenlist==1.8.0
greenlet==3.5.6
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
iniconfig==2.3.0
joblib==1.6.0
lightgbm==4.7.0
magic-filter==1.0.12
multidict==6.9.1
narwhals==2.26.0
numpy==2.4.6
opentelemetry-api==1.45.0
packaging==26.3
pandas==3.0.6
pluggy==1.6.0
propcache==0.5.4
psutil==7.2.2
pydantic==2.13.5
pydantic-settings==2.15.0
pydantic_core==2.46.5
Pygments==2.21.0
pytest==9.1.1
pytest-asyncio==1.4.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
ruff==0.16.10
scikit-learn==1.9.1
scipy==1.18.1
six==1.17.0
SQLAlchemy==2.1.2
starlette==1.7.0
ta==0.11.0
threadpoolctl==3.7.0
typing-inspection==0.4.4
typing_extensions==4.16.0
tzdata==2026.4
tzlocal==5.4.4
uvicorn==0.54.0
yarl==1.25.1
```

## Run the implemented foundation

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe main.py check-config
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: paper, mock backend, paused, no trading heartbeat.

**Next:** PART 4 — broker interfaces/types, Windows MT5 adapter, deterministic
MockMT5Client, paper-execution adapter, symbol management and risk-based order
calculator. Then risk/execution/trailing in Part 5.

Say CONTINUE to generate the next part.
