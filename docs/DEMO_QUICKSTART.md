# Demo quickstart (Exness DEMO, Windows)

DEMO account only. The bot checks the account type **reported by the MT5 terminal**; a REAL,
contest or unknown account, or `LIVE_TRADING=true`, is refused. Every trade needs a valid AI approval.

## Run

1. Install Python 3.11 x64 and MetaTrader 5. Log MT5 in to your **Exness DEMO** account and keep it open.
2. In the project folder open PowerShell and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_demo.ps1
   ```
   The first run creates `.env` from `.env.demo.example` and opens it in Notepad. Fill in
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` and `OPENAI_API_KEY` (your Groq key). For a cent account,
   set `ACCOUNT_CURRENCY=USC`. Save the file and run the same command again.
3. The script installs the packages, runs `check-config` and `init-db` (only if there is no database yet),
   then `resolve_symbols` (detects suffixes such as `XAUUSDm`) and `check_mt5_readonly` (must report
   `demo`). It then fetches news once, runs preflight and starts the bot **paused**.
4. In Telegram send `/status`, then `/resume`, and confirm once. Use `/pause` to stop new trades and
   `/kill` for the kill switch.

Demo orders use the broker **minimum lot** and are tagged `demo_fast_track`. They never count as
evidence for LIVE promotion. If news is unavailable the bot opens no new trades
(`NEWS_UNAVAILABLE_POLICY=block`). Check the news feeds any time with
`.venv\Scripts\python -m scripts.inspect_news_sources --fetch`.

## Add a second AI key

Put the key in `.env` under a name you choose, then list both AIs in `AI_PROVIDERS` on one line:
```
GROQ_API_KEY_2=your-second-key
AI_PROVIDERS=[{"label":"groq","base_url":"https://api.groq.com/openai/v1","model":"qwen/qwen3.8-27b","api_key_env":"OPENAI_API_KEY","priority":0},{"label":"groq2","base_url":"https://api.groq.com/openai/v1","model":"openai/gpt-oss-120b","api_key_env":"GROQ_API_KEY_2","priority":1}]
```
`api_key_env` holds the variable **name**, never the key. The second AI is only asked when the first
times out, is rate-limited (429) or is down (5xx). A valid "wait" or "reject" from the first AI is final.
If you want every AI to agree, set `AI_DECISION_MODE=all_must_approve`. Restart the bot after editing `.env`.

## Read `/ai_stats`

There is one line per AI label: approvals, rejections, trades (closed), win rate, net profit (exact,
from the closed trades), average confidence and failures (timeouts, 429, 5xx). `RULE_FALLBACK` only
appears if you turned off `AI_REQUIRE_APPROVAL`. The same view is in the Mini App under **AI journal**.

## Read `/audit`

`Trade audit: CLEAN` means every trade has a matching valid AI approval. Otherwise each flagged trade
is listed:

| Flag | Meaning |
|---|---|
| `NO_AI_APPROVAL` | no approved AI decision is linked to the trade |
| `RULE_FALLBACK_TRADE` | opened by the technical fallback, not by an AI |
| `UNKNOWN_DECIDER` | the deciding AI is not recorded (for example, trades from before this version) |
| `JOURNAL_MISMATCH` | the approval disagrees with the trade (side, symbol, confidence or timing) |
| `TRADE_AFTER_AI_WAIT` | the last AI decision before the open was "wait" |

The audit runs daily and alerts in Telegram on any flag. Offline: `.venv\Scripts\python -m scripts.audit_trades`.
It exits with 0 when clean, 1 when flagged and 2 when no database exists.
