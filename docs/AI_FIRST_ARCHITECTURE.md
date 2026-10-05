# AI-FIRST architecture

The AI is consulted on every trade action:
- **Entries:** `AIFirstReviewer` → `AIBrain.decide`.
- **Profit locks:** lock-first `AdaptiveTrailing` (see [TRAILING_AI_DESIGN.md](TRAILING_AI_DESIGN.md)).
- **Config:** `AIConfigAdjuster`.
- **After every closed trade:** `LearningLoop`.

The AI only adds information and vetoes. Hard limits, the risk engine, the kill switch and the
paper/mock execution path stay in deterministic code.

Paper/mock only by default. No broker connection is required to test anything below.

## Components

| Module | Role |
|---|---|
| `ai/market_awareness.py` | `MarketAwarenessEngine.snapshot()`: quote and spread vs limit; M5/M15/H1 RSI, MACD, EMA trend, ATR, BB, ADX, volume ratio; last 5 candles; 20-bar price action; support/resistance; regime (trending/ranging/volatile/news_impact); open positions with P&L; news state, risk and events in the next 2 h; owner config; performance 24 h/7 d (`PerformanceTracker`); recent lessons. The prompt is bounded (< 8 KB). |
| `ai/ai_first_schemas.py` | Strict Groq JSON contracts: `decision`, `trailing`, `config`, `trade_review` (`additionalProperties:false`, all fields required). |
| `ai/ai_brain.py` | Async request queue (concurrency `AI_MAX_CONCURRENT`, spacing `AI_QUEUE_MIN_INTERVAL_MS`), 90 s minute-bucketed cache, circuit breaker (3 consecutive failures → rule mode + owner notification, half-open after cooldown), deterministic entry gate, technical fallback. |
| `ai/ai_first_reviewer.py` | Produces the bound `AIEntryReview` that `SignalEngine.finalize` already requires. |
| `ai/config_adjuster.py` | Two-layer AI-dynamic limits: bounded proposals, >50 % owner approval, strong-trend rule for increases, `revert_all` (`/ai_reset`). |
| `trading/ai_controls.py` | Owner `AI_FALLBACK_MODE` (DB, audited), AI status heartbeat, `dynamic_config`, `effective_limits()` read by the risk engine and execution, broker ceilings (hard caps). |
| `ai/decision_journal.py` | `ai_decision_journal` and `config_history` tables (additive; core schema 2 unchanged; old `ai_config_overlay` is renamed in place). |
| `app/alerts.py` | Alert Center: `alerts` table, levels, Telegram delivery, dedup, rate limit, CRITICAL auto-pause. |
| `ai/learning_loop.py` | Per-trade review (AI or deterministic), lessons, outcome attachment, strategy-weight proposals, nightly deep review. |
| `ai/ai_first.py` | `AIFirstLayer`: one composition for the runtime (`app/dependencies.compose`). |
| `telegram_bot/ai_notifications.py` | Owner Telegram notifications (bounded, sanitized, at most once). |

## Models (Groq, OpenAI-compatible)

```
AI_PROVIDER=openai_compatible          # alias of "openai"; "groq" is accepted too
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=<your Groq key, private .env only>
OPENAI_MODEL_FAST=qwen/qwen3.8-27b     # = OPENAI_MODEL: entries, trailing, config review
OPENAI_MODEL_DEEP=openai/gpt-oss-120b  # = AI_DEEP_MODEL: nightly deep review and learning
OPENAI_RESPONSE_FORMAT=json_schema_strict
AI_TIMEOUT_SECONDS=10
AI_MAX_RETRIES=3
AI_QUEUE_MIN_INTERVAL_MS=500           # request spacing
AI_FALLBACK_MODE=BLOCK_ON_AI_FAILURE   # or TECHNICAL_ONLY
```

Notes:
- With an empty key the brain stays in **rule mode** (AI unavailable): with the default
  `BLOCK_ON_AI_FAILURE` that means **no new entries**. Switch to `TECHNICAL_ONLY` to trade on the
  technical score without a key. This does not count as a circuit failure.
- `AI_PROVIDER=ollama` keeps the reviewed supervisor path.
- `AI_PROVIDER=disabled` is a deliberate veto, never a rule approval.

## Entry decision (requirement 1)

The AI returns:

```
{action: open_buy|open_sell|wait|close_position|modify_sl|modify_tp, confidence 0-100, reason,
 suggested_risk_percent 0.1-1.0, suggested_target_profit, suggested_sl_distance,
 news_risk low|medium|high, market_condition trending|ranging|volatile|news_impact}
```

An entry is executable only if **all** of these hold:
- confidence ≥ `AI_CONFIDENCE_THRESHOLD`
- the action matches the technical side
- news is known and clear, and the news risk is not high
- spread ≤ the effective limit
- trades today < effective `max_daily_trades` (AI 6–20, hard cap 25)
- open positions < effective `max_open_positions` (AI 1–5, hard cap 5)
- the symbol is not disabled by the overlay
- no AI pause advisory is active

It then still passes the unchanged `SignalEngine` and `RiskEngine`.

The AI can only reduce size: it does so when `AUTO_REDUCE_RISK=true`, and the suggestion is
journaled either way. A valid `wait` is final; it is never shopped to the rules.

If the AI fails (timeout, transport error, invalid JSON, circuit open) after `AI_MAX_RETRIES`
bounded retries (backoff 0.25/0.5/1 s), the owner-controlled **`AI_FALLBACK_MODE`** decides:

| Mode | AI unavailable ⇒ | Journal source |
|---|---|---|
| `BLOCK_ON_AI_FAILURE` (default, safest) | **no new entries**; trailing locks and protection continue | `ai_blocked` |
| `TECHNICAL_ONLY` | technical score must be ≥ `AI_RULE_FALLBACK_MIN_SCORE` (confidence = score) | `rule_fallback` / `technical-score-v1` |

The mode is set in `.env` (default) and switched at runtime by the owner only:
`/ai_fallback_block`, `/ai_fallback_technical`, `/ai_fallback_status`, or the Mini App
**Settings → AI fallback mode** buttons (`POST /api/ai_fallback`). Every change is written to
`audit_logs` (`owner.ai_fallback_mode_changed`, from → to, owner id). The toggle never touches the
kill switch, pause state, risk checks or the news block, which apply in both modes. Log lines:
`AI_FALLBACK: AI unavailable, blocking new entries` / `... using technical fallback`.

### AI approval required, registry, attribution and audit

* **`AI_REQUIRE_APPROVAL=true` (default) overrides `TECHNICAL_ONLY`.** An entry needs a valid AI approval
  at or above `AI_CONFIDENCE_THRESHOLD`. Otherwise the result is `ai_blocked`: the journal reason is
  `no_ai_approval` and the owner gets one Telegram line ("BLOCKED: no AI approval", at most once per symbol
  per 15 min). The technical score trades only when ALL of these hold: `AI_REQUIRE_APPROVAL=false`,
  `AI_RULE_FALLBACK_ENABLED=true` (default false) and the owner mode is `TECHNICAL_ONLY`. Such trades are
  labelled `RULE_FALLBACK`, never shown as an AI decision.
* **Registry** (`ai/provider_registry.py`, `AI_PROVIDERS`). Each label has its own client and its own circuit,
  and labels are asked in priority order. Failover happens ONLY on timeout, 429, 5xx, a missing key or an open
  circuit. An auth error or an invalid reply means no trade. A valid reply (including wait/reject) is final.
  `AI_DECISION_MODE=all_must_approve` asks every decision AI and records the label `a+b`. Failures and circuit
  changes become `ai.provider_failure` / `ai.provider_circuit` audits; circuit changes also reach Telegram.
* **Who decided** (`ai/trade_attribution.py`, additive `trade_attribution` table; core `trades` and
  `SCHEMA_VERSION` are unchanged). It stores `decided_by`, `ai_model`, `ai_confidence`,
  `approval_journal_id`, `trailing_by` (the AI label or `MECHANICAL`), `close_by` and `demo_fast_track`.
  Trades from before this table read `unknown`. The link is exact: `link_execution` stamps the filled position
  id on the approving journal row. Entry and close Telegram messages are sent once per trade.
* **Audit** (`ai/trade_audit.py`, `/audit`, `python -m scripts.audit_trades`, daily job): flags
  `NO_AI_APPROVAL`, `RULE_FALLBACK_TRADE`, `UNKNOWN_DECIDER`, `JOURNAL_MISMATCH` and `TRADE_AFTER_AI_WAIT`.
  Any flag raises an Alert Center ERROR and a Telegram summary.

## Dynamic config: AI-dynamic trade frequency (two layers)

**Layer 1 — AI-adjustable** (`config_history` + `dynamic_config`):

| Parameter | AI bounds | Owner default |
|---|---|---|
| max_daily_trades | 6–20 | `MAX_DAILY_TRADES` (12) |
| max_open_positions | 1–5 | `MAX_OPEN_POSITIONS` (3) |
| risk_percent_per_trade | 0.1–1.0 % | `MAX_RISK_PERCENT_PER_TRADE` (0.5) |
| target_profit_per_trade | $1–20 | `TARGET_PROFIT_USD_PER_TRADE` (5) |
| max_spread_points | 10–50 | global cap (never above it) |
| skip_trailing_levels / strategy_weights / symbols_to_trade | approval only | — |

**Layer 2 — hard caps the AI can never exceed:** 25 trades/day, 5 open positions, 1.0 % risk.
Daily loss 3 % and drawdown 10 % latch an auto-pause in the risk engine. The broker/simulation
guards enforce the hard caps as defense in depth.

Rules:
- **Direction:** an *increase* of trades/day, positions or risk is accepted only in a strong trend
  (regime `trending`, i.e. ADX ≥ 25, with news risk not high). Otherwise it is rejected with
  `increase_requires_strong_trend`. Choppy or high-news markets can only reduce.
- **Approval:** a change of more than 50 % against the owner default creates an
  `ai_config_adjustment` proposal (`/approve ID`, `/reject ID` or the Mini App). Changes within
  50 % auto-apply (`AI_CONFIG_AUTO_APPLY_MINOR=true`).
- **AI unavailable ⇒ defaults:** the risk engine and execution use `dynamic_config` values only
  while a fresh AI heartbeat says the AI is answering (`ai_status` job every 60 s, stale after
  180 s, `rule` mode = unavailable). Otherwise the owner defaults apply.
- **LIVE mode:** trades/day, open positions and risk can only be reduced below the owner settings;
  the broker ceilings stay at the owner settings.
- **Forbidden** (always rejected and audited): kill switch, live/paper flags, start paused, daily
  loss and drawdown limits, news and stop-loss requirements, native write mode, and the owner's
  `MAX_RISK_PERCENT_PER_TRADE` setting itself.
- **Owner override:** `/ai_reset` or Mini App **Settings → Reset AI to defaults** reverts every
  adjustment and clears `dynamic_config`. `/limits` and `GET /api/limits` show the effective
  values, defaults, bounds and hard caps.
- **Audit:** every attempt is in `config_history` with its reason, in `ai_decisions.log`, and in
  `audit_logs` (`ai.config_auto_applied`, `ai.config_pending_owner`, `ai.config_out_of_bounds`,
  `ai.config_forbidden`, `owner.ai_config_decided`, `owner.ai_config_reset`). The nightly review
  includes a daily summary of adjustments.

## Schedule (runtime)

| Job | Interval |
|---|---|
| signals (market awareness + AI entry) | `SIGNAL_INTERVAL_SECONDS` (1–5 min) |
| news | `NEWS_POLL_SECONDS` (5–15 min) |
| positions (lock-first trailing) | position interval |
| ai_learning (closed trades → lessons, owner-decision sync) | 5 min |
| ai_config_review (performance + config suggestions) | 30 min |
| ai_nightly_review (deep model) | daily, one hour after the report hour |
| notifications (runtime, news, AI) | notification interval |
| ai_status (AI availability heartbeat for dynamic limits) | 60 s |
| alerts (Alert Center flush + audit scan) | 5 s |

## Journal and owner views

`ai_decision_journal` records each decision with these fields:

`time, kind (entry|trailing|config|lesson|deep_review), source (ai|cache|ai_blocked|rule_fallback|mechanical|deterministic), model, input_summary + input_hash, action, confidence, reason, adjustments, executed, rejection_reason, final_action, position_id, threshold_reached, outcome_usd`

The owner can read it in two places:
- **Telegram:** `/ai` shows the journal summary and the last decisions.
- **Mini App:** the **AI** tab (`GET /api/ai_journal`) shows decision history, quality summary and
  config adjustments.

Read paths never create tables. Run `python main.py init-db` once to create them; the runtime also
creates them additively.

## Owner notifications

Telegram notifications are sent for:
- executable AI entries
- the circuit opening (rule mode) and closing
- AI config changes applied, or pending with the `/approve` hint
- AI trailing decisions and mechanical fallbacks

The owner can always override with `/pause`, `/kill`, `/close`, `/close_all`, `/reject` and
`/ai_reset`.

## Test in mock/paper mode

```
python -m pytest -q tests/test_ai_first.py tests/test_ai_first_runtime.py tests/test_ai_adaptive_trailing.py tests/test_ai_fallback_mode.py tests/test_alerts.py
python -m scripts.smoke_ai_first
python -m scripts.smoke_ai_first --provider rule
OPENAI_API_KEY=... python -m scripts.smoke_ai_first --provider groq   # optional real Groq call, still mock broker
```
