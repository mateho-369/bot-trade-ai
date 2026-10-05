# Part 15 — Preflight fixes, Groq strict JSON, rule-based AI fallback (0.13.0 · schema 2)

Offline software changes only. No broker connection, provider request, credential, live
trading or real order was used or enabled. Safe defaults are unchanged:
`LIVE_TRADING=false`, `PAPER_TRADING=true`, `START_PAUSED=true`, `MT5_BACKEND=mock`, native
adapters default to `DenyAllWrites`.

## 1. Deployment preflight fixes (`readiness/preflight_checks.py`)

| Bug | Effect before | Fix |
|---|---|---|
| Sync `httpx.Client` stream read with `aiter_bytes()` | AI provider probe ALWAYS reported "unreachable" | `iter_bytes()` |
| Oversized listing body parsed after truncation | Healthy provider reported "unreachable" | Truncated/unparsable 200 listing = reachable, `model_count=None` |
| Non-SQLite `DATABASE_URL` (e.g. PostgreSQL) treated as a local path | Wrong finding; server URL path resolved locally | `database_url_not_local_sqlite` warning, never probed/contacted |
| Empty / non-string HTTP `Date` header | Empty string skipped; integer crashed the parser | `external_clock_reference_unusable` (not a measurement) |
| `scripts/preflight.py` compared `overall` to `preflight_checks_passed`, a status the report never emits | `python -m scripts.preflight` ALWAYS exited 2, even when every check passed | Compares to `offline_checks_passed` (exit 0) like `scripts.readiness`; regression test added |

## 2. Groq via the OpenAI-compatible adapter

`AI_PROVIDER=openai` selects the adapter TYPE; the vendor is set by `OPENAI_BASE_URL`.

```ini
AI_PROVIDER=openai
AI_FALLBACK_PROVIDER=disabled
OPENAI_API_KEY=            # your Groq key, private .env only
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=qwen/qwen3.8-27b          # or openai/gpt-oss-20b | openai/gpt-oss-120b
OPENAI_RESPONSE_FORMAT=json_schema_strict
OPENAI_REASONING_EFFORT=               # empty = not sent; gpt-oss: low + AI_MAX_OUTPUT_TOKENS=2048
```

- `json_schema_strict` sends `response_format={"type":"json_schema","json_schema":{"strict":true,...}}`
  (Groq constrained decoding). The schema is a reviewed subset generated from the local reply
  models (`ai.schemas.strict_output_schema`): every field required, every object closed,
  Decimal fields as text. An unknown schema is refused before any HTTP request.
- `json_object` (code default) keeps the previous JSON-syntax mode for other providers.
- Either way, every reply is still strictly decoded by the local pydantic model and bound to the
  request/proposal/news/code/model hashes. Provider mode never widens what is accepted.
- HTTPS is required for non-loopback endpoints; no redirects/proxies/retries; bounded bytes.
- Model IDs come from Groq's structured-outputs page (strict mode: `openai/gpt-oss-20b`,
  `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`). Re-check model availability before use.

## 3. Rule-based fallback (`strategy/rule_fallback.py`)

When the AI cannot answer, the persisted **technical signal score** decides instead of
blocking every entry.

| Situation | Result |
|---|---|
| Provider timeout | rule fallback |
| HTTP 429 / 5xx / 401 / 403 / 404 / network error / circuit open | rule fallback |
| Provider not configured (e.g. Groq key not yet set) | rule fallback (no HTTP request) |
| Invalid JSON / invalid envelope / reply bound to another request | rule fallback (reply discarded) |
| Unbound/malformed reply that still says reject/WAIT/low confidence | **veto** (intent honoured) |
| Valid AI reject / WAIT / confidence below threshold | **veto** (never overruled) |
| `AI_PROVIDER=disabled`, expired request | veto |
| Unknown/stale/unsafe news, learning-filter veto | veto (checked before AI) |
| Reviewer crashes with an unexpected defect | veto (fail closed) |
| BACKTEST / historical replay | never used |
| LIVE | only with `AI_RULE_FALLBACK_ALLOW_LIVE=true` |

Approval rule: `technical score >= AI_RULE_FALLBACK_MIN_SCORE` (default **80**), with a side and a
structural stop. Settings validation requires the minimum to be ≥ `AI_CONFIDENCE_THRESHOLD` and
≥ `MIN_SIGNAL_SCORE` when enabled.

Binding: the stored review has `provider="rule_fallback"`, `provider_model="technical-score-v1"`,
no request hash, no risk change and `confidence == signal score` exactly. Finalization,
`SignalStore.approved_context` and the pre-send risk recheck all re-verify those facts against
the CURRENT policy, so a later config change (e.g. fallback disabled) revokes unsent approvals.
Approved rows carry `reason="technical_rule_fallback_news_approved"`; audit events
`ai.rule_fallback_review`, `ai.rule_fallback_not_permitted`, `signal.ai_review_unavailable`.

Unchanged: risk sizing and caps, SL requirement, 30/60/90 profit locks, no martingale/grid,
owner pause/kill/resume, stage gates, durable idempotency, `DenyAllWrites`, startup PAUSED.
Position reviews/TP extension still require a real AI review (no fallback extension).

## 4. Cent accounts (Exness USC / EUC)

`trading/currency.py` treats the exact codes `USC` and `EUC` as fixed 1/100
denominations of USD and EUR (`USDC`, `USDT` or anything else is never relabelled).

| Concern | How it works |
|---|---|
| Detect the account | MT5 `account_info().currency`. The client refuses (quarantine) if it differs from `ACCOUNT_CURRENCY`, and the risk engine vetoes `account_currency`. `scripts.resolve_symbols` reports the terminal currency and proposes `ACCOUNT_CURRENCY=USC`. |
| Risk % of balance | Budgets are `risk_capital × %` in account currency: 0.5% of 100,000 USC = 500 USC = $5, identical to 0.5% of $1,000. |
| Lot size | Native `order_calc_profit` (mock: the same converter) values a candidate lot in USC, so the broker's cent tick value is used and the lot equals the USD-equivalent account's (0.02, never 2.00). |
| USD amounts | Target, commission and swap convert ×100 exactly: a $2 target = 200 USC; `TrailingEngine.lock_targets_account()` shows the 30/60/90 tiers as 60/120/180 USC. |
| Snapshot FX | USC→USD asset/liability rate exactly 0.01; EUC uses `ACCOUNT_TO_USD_SYMBOLS_JSON={"EUR": "EURUSDc"}` then ÷100. |
| Paper/mock | With `ACCOUNT_CURRENCY=USC`, `PAPER_INITIAL_BALANCE` is in cents (100000 = $1,000). |

`tests/test_cent_account.py` runs the whole open → reconcile → 30/60/90 trailing flow on a
100,000 USC account and on a $1,000 account and asserts identical lots, stops and USD
locks, with every account-currency amount exactly ×100.

## 5. Dynamic multi-symbol support

`SYMBOLS` accepts any logical names (FX, metals, crypto, indices). `trading/symbol_resolver.py`
plus the read-only CLI `python -m scripts.resolve_symbols --env-file .env [--write]`:

- read the broker's symbol list and Market Watch (`symbols_get`, `visible`), never orders;
- detect suffixes: `XAUUSD.`, `XAUUSDm`, `XAUUSDc`, `EURUSD.raw`, `US30-ecn`, `_i`, `#`, `pro`
  (an UPPERCASE tail such as `ETHUSDT` is a different instrument, never a suffix);
- break ties by the account's own convention (suffix vote), Market Watch visibility or
  `--suffix`; anything still ambiguous stays disabled with a named reason;
- report contract size, tick size, tick value (profit/loss), point, digits, lot min/max/step,
  stops level, current and typical spread for every symbol;
- propose a per-instrument spread cap = median broker candle spread × 2 (never below
  `MAX_SPREAD_POINTS`), so BTCUSD (thousands of points) is not blocked forever while FX
  keeps a tight cap; the spread/ATR filter still applies;
- infer news exposure from fiat base/profit currencies (XAUUSD → USD, USDJPY → USD, JPY);
- find the FX route for a non-USD parent (EUC → `EURUSDc`).

`--write` updates ONLY `SYMBOL_ALIASES_JSON`, `SYMBOL_SPREAD_LIMITS_JSON`,
`SYMBOL_NEWS_CURRENCIES_JSON`, `ACCOUNT_TO_USD_SYMBOLS_JSON` and (on mismatch)
`ACCOUNT_CURRENCY`, atomically, keeping the file mode, with no backup copy of secrets, and
restores the original if the result fails Settings validation. Owner entries always win.

Why persist instead of re-binding at runtime: aliases are part of the safety fingerprint
shared by the runtime, the watchdog (health `config_hash`), backups and bound approvals. A
silent in-memory rename would make the watchdog treat the bot as foreign. At startup the
`SymbolManager` therefore never re-binds; if `XAUUSD` is missing but `XAUUSDm` exists it
disables the symbol and names the fix. All spread-cap lookups now share
`Settings.spread_limit_points(logical, native)`.

## 6. Multi-day operation

APScheduler INFO lines (two per job run, every 5-30 s) are now suppressed to WARNING, so a
multi-day run keeps real events inside the bounded rotating log (5 MiB × 6 files).
Verified in a throwaway copy: discovery → `check-config` → `init-db` → `app.bot` and
`watchdog.py` start PAUSED, health `ready`, `scripts.stop_runtime` exits cleanly (exit 0).

## 7. Release manifest

`docs/RELEASE_15_MANIFEST.json` is produced by the reviewed builder
`python -B -m scripts.build_release_manifest --pytest-passed N` and checked by the unchanged
read-only `python -B -m scripts.verify_release`. Hashes are not a signature or permission.
Any later byte edit requires review, a full test run and a rebuilt manifest.

`.gitattributes` (`* -text`) prevents Git CRLF conversion on Windows, which would otherwise
change file bytes and fail the integrity check after `git clone`.

## 8. Compatibility

New code/config changes the code and safety fingerprints. Existing pending approvals and
config-bound paper checkpoints from Part 14 are intentionally invalid; keep the matched
code/config/DB together and never edit a hash to bypass validation (see `MIGRATIONS.md`).
