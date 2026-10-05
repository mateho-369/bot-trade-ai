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

## 4. Release manifest

`docs/RELEASE_15_MANIFEST.json` is produced by the reviewed builder
`python -B -m scripts.build_release_manifest --pytest-passed N` and checked by the unchanged
read-only `python -B -m scripts.verify_release`. Hashes are not a signature or permission.
Any later byte edit requires review, a full test run and a rebuilt manifest.

`.gitattributes` (`* -text`) prevents Git CRLF conversion on Windows, which would otherwise
change file bytes and fail the integrity check after `git clone`.

## 5. Compatibility

New code/config changes the code and safety fingerprints. Existing pending approvals and
config-bound paper checkpoints from Part 14 are intentionally invalid; keep the matched
code/config/DB together and never edit a hash to bypass validation (see `MIGRATIONS.md`).
