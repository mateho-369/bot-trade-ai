# 3-day continuous Paper test (Windows, PowerShell)

The bot always starts **PAUSED** and never resumes by itself. Paper mode never sends an
order to a broker: fills are simulated (`PAPER_TRADING=true`, native writes DenyAll).
Nothing here enables live trading.

## 0. Install once

```powershell
git clone -b master https://github.com/mateho-369/bot-trade-ai.git   # -b not needed once master is the default branch
cd bot-trade-ai
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q          # expect 0 failures
.\.venv\Scripts\python.exe -B -m scripts.verify_release
Copy-Item .env.example .env                     # .env is private; never commit it
```

## 1. Choose the test type

| | A. Mock soak (offline) | B. Paper on Exness demo data |
|---|---|---|
| Market data | synthetic, static quotes | real quotes from your MT5 demo terminal (read-only) |
| Orders | simulated | simulated (never sent) |
| Trades expected | **none**: the synthetic market is too calm (signals are vetoed `spread_too_large_for_atr`) | yes, when signals, news and risk gates pass |
| Proves | 72 h stability: scheduler, DB, watchdog, health, logs, Telegram control | strategy/AI/fallback/risk/trailing behaviour on live prices |

### A. Mock soak: `.env` stays as copied (`MT5_BACKEND=mock`).

### B. Paper on demo data: edit `.env`

```text
MT5_BACKEND=real
DEMO_MODE=true
PAPER_TRADING=true
LIVE_TRADING=false
START_PAUSED=true
MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe
MT5_LOGIN=<demo login>      MT5_PASSWORD=<demo password>      MT5_SERVER=<Exness demo server>
```

Cent demo account: the discovery step below detects `USC` and proposes `ACCOUNT_CURRENCY=USC`.
Then set `PAPER_INITIAL_BALANCE` in cents (100000 = $1,000).

News: on real data every entry needs fresh, managed news and calendar evidence
(`RSS_URLS_JSON`, `CALENDAR_SOURCE_URL` or a reviewed `CALENDAR_FILE`, and
`NEWS_SOURCE_COVERAGE_JSON`; check them with `python -m scripts.inspect_news_sources`).
Without coverage the bot runs and monitors, but vetoes every entry with an unknown-news reason.

AI: leave `OPENAI_API_KEY` empty to test the rule-based fallback alone, or add your Groq key.

## 2. Owner control (required to resume)

Create a bot with @BotFather and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` (your
numeric ID). `/status`, `/positions`, `/pause`, `/resume` and `/kill` are owner-only;
`/resume` needs a fresh single-use confirmation.

## 3. Prepare

```powershell
.\.venv\Scripts\python.exe -B -m scripts.resolve_symbols --env-file .env          # review the report
.\.venv\Scripts\python.exe -B -m scripts.resolve_symbols --env-file .env --write  # persist aliases/spread caps
.\.venv\Scripts\python.exe main.py check-config --env-file .env
.\.venv\Scripts\python.exe main.py init-db --env-file .env      # once; never reset history to pass gates
.\.venv\Scripts\python.exe main.py status --env-file .env       # expect "state": "paused"
.\.venv\Scripts\python.exe -B -m scripts.preflight --env-file .env
```

For B, `python -B -m scripts.check_mt5_readonly --env-file .env` is an optional read-only check.

## 4. Run for 3 days (watchdog restarts a crashed child, always PAUSED)

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
.\.venv\Scripts\python.exe watchdog.py --env-file .env
```

Leave that window open: locking the screen is fine, signing out is not. Then in Telegram:
`/status`, then `/resume` and confirm. The bot now scans closed M5 bars and executes in paper only.

## 5. Observe (second PowerShell window)

```powershell
.\.venv\Scripts\python.exe -B -m scripts.runtime_status --env-file .env   # control/health
.\.venv\Scripts\python.exe main.py status --env-file .env
Get-Content data\logs\bot.log -Tail 50 -Wait                               # bounded: 5 MiB x 6 files
```

Telegram `/status` and `/positions`, and the Mini App if configured, show trades, risk latches
and AI or rule-fallback decisions (`technical_rule_fallback_news_approved`).

## 6. Stop

```powershell
.\.venv\Scripts\python.exe -B -m scripts.stop_runtime --env-file .env                  # clean stop, no kill
.\.venv\Scripts\python.exe -B -m scripts.stop_runtime --env-file .env --clear-request  # before the next start
```

`/pause` stops new entries without stopping the process; `/kill` latches the kill switch.
Neither one flattens positions by itself.
