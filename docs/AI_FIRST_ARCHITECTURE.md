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
| `ai/config_adjuster.py` | Bounded AI overlay with owner approval, plus `revert_all` (`/ai_reset`). |
| `ai/decision_journal.py` | `ai_decision_journal` and `ai_config_overlay` tables (additive; core schema 2 unchanged). |
| `ai/learning_loop.py` | Per-trade review (AI or deterministic), lessons, outcome attachment, strategy-weight proposals, nightly deep review. |
| `ai/ai_first.py` | `AIFirstLayer`: one composition for the runtime (`app/dependencies.compose`). |
| `telegram_bot/ai_notifications.py` | Owner Telegram notifications (bounded, sanitized, at most once). |

## Models (Groq, OpenAI-compatible)

```
AI_PROVIDER=openai
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=<your Groq key, private .env only>
OPENAI_MODEL=qwen/qwen3.8-27b          # fast decisions (entries, trailing, config review)
AI_DEEP_MODEL=openai/gpt-oss-120b      # nightly deep review and learning
OPENAI_RESPONSE_FORMAT=json_schema_strict
```

Notes:
- With an empty key the brain stays in **rule mode** (technical score). This does not count as a
  circuit failure.
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
- trades today < effective `max_daily_trades` (≤ 15)
- open positions < `max_open_positions` (≤ 3)
- the symbol is not disabled by the overlay
- no AI pause advisory is active

It then still passes the unchanged `SignalEngine` and `RiskEngine`.

The AI can only reduce size: it does so when `AUTO_REDUCE_RISK=true`, and the suggestion is
journaled either way. A valid `wait` is final; it is never shopped to the rules.

If the AI fails (timeout, transport error, invalid JSON, circuit open), the decision falls back to
the technical signal score as `rule_fallback` / `technical-score-v1`. Its confidence equals the
score, and it executes only when score ≥ `AI_RULE_FALLBACK_MIN_SCORE`. Trading is not blocked.

## Dynamic config (requirement 2)

| Parameter | AI bounds | Auto-apply (minor) |
|---|---|---|
| risk_percent_per_trade | 0.1–1.0 % | ±0.1 % |
| target_profit_per_trade | $1–20 | ±$1 |
| max_daily_trades | 3–15 | never (owner approval) |
| max_spread_points | 10–50 | never |
| skip_trailing_levels | subset of 30/60/90 (AI consultation only) | never |
| strategy_weights | trend, mean_reversion, breakout, momentum (sum 1) | never (stopped owner projection) |
| symbols_to_trade | subset of owner-configured symbols | never |

Rules:
- **Forbidden** (always rejected and audited): `max_open_positions`, kill switch, live/paper
  flags, daily loss and drawdown limits, news and stop-loss requirements, native write mode.
- **Effective values** are clamped by the owner settings: the AI may lower risk, daily trades or
  spread limits below the owner's settings, never raise them above.
- **Major changes** create an `ai_config_adjustment` proposal. Approve it with `/approve ID` or
  through the Mini App proposal flow (synced within 5 min), or reject it with `/reject ID`.
- **Owner override:** `/ai_reset` reverts every AI adjustment.
- **Audit:** every step is in `audit_logs` (`ai.config_auto_applied`, `ai.config_pending_owner`,
  `ai.config_out_of_bounds`, `ai.config_forbidden`, `owner.ai_config_decided`,
  `owner.ai_config_reset`).

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

## Journal and owner views

`ai_decision_journal` records each decision with these fields:

`time, kind (entry|trailing|config|lesson|deep_review), source (ai|cache|rule_fallback|mechanical|deterministic), model, input_summary + input_hash, action, confidence, reason, adjustments, executed, rejection_reason, final_action, position_id, threshold_reached, outcome_usd`

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
python -m pytest -q tests/test_ai_first.py tests/test_ai_first_runtime.py tests/test_ai_adaptive_trailing.py
python -m scripts.smoke_ai_first
python -m scripts.smoke_ai_first --provider rule
OPENAI_API_KEY=... python -m scripts.smoke_ai_first --provider groq   # optional real Groq call, still mock broker
```
