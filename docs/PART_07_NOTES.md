# Part 7 — AI supervision, providers and offline learning

**Release 0.5.0 · cumulative Parts 1–7 · database schema 2 unchanged.**

Working code, not AI-generated trading permissions: async Ollama/OpenAI-compatible
adapters, strict review schemas, a bounded router, owner proposal storage, actual
logistic/LightGBM training, chronological evaluation, immutable model registry and
rollback. The full literal current source/config/tests are in `PART_07.md`; ordinary
repository files are authoritative. Historical guides/manifests remain snapshots.

**No real provider, native Windows terminal, broker order, deployment or market
strategy evaluation was performed.** Tests use MockTransport/scripted confidence,
synthetic prices and toy learning labels. This is not the assembled autonomous
bot; news/calendar producers are Part 8, authenticated owner transport is Part 9,
scheduling/deployment is Part 10 and historical backtesting is Part 11. Native
clients still default to DenyAllWrites; existing owner/risk/stage/account gates
and fresh live account/session/nonce consent remain mandatory.

## Actual modules

| Module | Implemented behavior |
|---|---|
| `ai/json_validation.py` | UTF-8/byte/depth/node/array/string bounds; rejects duplicate keys, NaN/Infinity, giant integers, trailing prose and eval/repair |
| `ai/schemas.py` | Frozen strict Pydantic entry/market/position/news/settings replies; exact request/source/code/model/news/proposal/position bindings |
| `ai/http_transport.py` | Async bounded POST, identity encoding, no redirect/retry/environment proxy; generic failure codes and lazy client lifetime |
| `ai/ollama_client.py` | `/api/chat`, stream=false, JSON schema, deterministic temperature, bounded generation; complete assistant-only matching model envelope |
| `ai/openai_client.py` | `/chat/completions`, JSON object mode with local strict enforcement; matching alias/dated resolution, refusal/tool/truncation veto |
| `ai/prompt_templates.py` | Secret-free bounded hashed request, immutable context/deadline and untrusted-data boundaries; no browsing/tools/code execution |
| `ai/ai_router.py` | Bounded concurrency/total timeout, availability-only fallback, circuit cooldown and audited fail-closed responses |
| `ai/ai_supervisor.py` | EntryReviewer integration, read-only market/news/owned-position advice, deterministic daily report and explicit offline learning job |
| `ai/suggestion_store.py` | Immutable deduplicated pending proposals, owner approve/reject/expiry and stopped validated settings projection |
| `ai/owner_guard.py` | Internal owner/stopped/reconciled-flat/zero-reservation guard; NOT Telegram/initData authentication or broker-flat proof |
| `ai/feature_engineering.py` | Fixed 32-field normalized pre-entry feature schema; no realized profit/exit/trailing/label/account fields |
| `ai/dataset.py` | Exact money, chronology/availability, immutable feature vectors and bounded versioned selected-trade dataset |
| `ai/learning_engine.py` | Original Signal/context/intent and complete fill-leg proof; closed USD labels only, skips unknown/non-USD/unallocated-cost records |
| `ai/walk_forward.py` | Expanding, timestamp-grouped folds; actual event/availability purging plus time embargo; never shuffled CV |
| `ai/model_trainer.py` | Real CPU sklearn logistic/StandardScaler and LightGBM fits; fixed threshold and chronological OOS scoring; inactive candidate only |
| `ai/portable_model.py` | Numeric logistic JSON inference and constrained, version-bound LightGBM native text; no pickle/joblib deserialization |
| `ai/artifacts.py` | Confined content-addressed immutable JSON artifacts, symlink rejection, fsync and hash/size validation |
| `ai/model_registry.py` | File/DB/code/policy/evaluation binding; serialized single pointer, staged selection, stopped owner rollback and existing StageGate integration |
| `ai/trade_analyzer.py` | Cost-inclusive positive-proof whole-trade statistics; cash credits/deposits excluded, missing drawdown stays unknown |
| `ai/strategy_optimizer.py` | ≤.02 owner-reviewed OOS vote-association transfer; disabled weights remain zero, correlated votes never claimed independent alpha |
| `scripts/smoke_ai.py` | Environment-isolated complete scripted-provider/signal/risk/label/toy-training/registry/owner-projection diagnostic |
| `scripts/synthetic_ai_fixtures.py` | **TEST ONLY** scripted MockTransport and synthetic toy vectors/labels, never native/provider/market evidence |

`ai/__init__.py` is import-only. No endpoint, model server, terminal or model download
is started on import/initialization. HTTP clients are created lazily on explicit
review calls; CPU/SQL service operations use `asyncio.to_thread`, not blocking the
Telegram event loop directly. Cancellation cannot forcibly stop a CPU training
thread, but a cancelled job cannot subsequently select a model via this service.
Large-history/latency/throughput profiling has not been performed.

## Configuration additions

```dotenv
AI_MAX_RESPONSE_BYTES=65536
AI_MAX_OUTPUT_TOKENS=768
AI_CIRCUIT_FAILURES=3
AI_CIRCUIT_COOLDOWN_SECONDS=60
AI_SUGGESTION_TTL_SECONDS=3600
MODEL_ALGORITHM=logistic
MODEL_FILTER_ENABLED=false
MODEL_MIN_PROBABILITY=0.70
MODEL_MIN_EVAL_AUC=0.55
MODEL_MIN_BRIER_SKILL=0.01
MODEL_MIN_SELECTED_TEST_TRADES=30
MODEL_MAX_DATASET_ROWS=10000
MODEL_MAX_ARTIFACT_BYTES=1048576
MODEL_MAX_AGE_DAYS=30
MODEL_RANDOM_SEED=42
```

Existing safe defaults remain: local Ollama `llama3.1`, fallback OpenAI-compatible
`gpt-4.1-mini` **only if an explicit key is configured**, 12-second total budget,
concurrency 2, confidence threshold 70, auto risk reduction/weight adaptation false.
These identifiers are configurable examples, not availability/quality guarantees.
Local compatible servers still require an explicit nonempty API key configuration.

Non-loopback AI endpoints must use HTTPS; Ollama remote access additionally needs
`ALLOW_REMOTE_OLLAMA=true`. URLs cannot contain credentials, fragments, queries or
params; fixed configured origins are never replaced by a headline/LLM URL.
Responses requesting redirect, compression, unsupported media, tool/function calls,
refusal, truncation, unknown model or invalid JSON fail closed. Provider keys exist
only in the outbound auth header, never a prompt, model artifact or audit payload.
There is no automatic retry loop or model installation/download.

The OpenAI-compatible adapter uses chat-completions JSON-object mode and performs
strict schema enforcement **locally**. Servers that do not support that request
shape remain unavailable; the code does not silently remove JSON constraints.
Ollama allows the configured name or its `:latest` canonical suffix; OpenAI allows
the configured name or its dated resolution. Reported names are provider claims,
not cryptographic attestation of remote weights; `model_sha256` binds the local
rule/optional classifier execution profile, not the remote LLM weights.

## Routing is not approval shopping

```text
hard technical/news/context gates → request-bound primary review
   transport availability failure → one configured fallback within total deadline
   valid reject/WAIT/low confidence → preserve veto; DO NOT try another provider
   malformed/unbound/unsafe content → veto; DO NOT try another provider
   accepted typed review → persisted signal finalization → explicit risk/owner/stage gate
```

Only availability errors (including timeout, rate limiting, missing key/model and
transport HTTP availability failures) may use fallback. If fallback is configured,
primary has at most half of the total budget; otherwise primary keeps the full
budget. Queue wait is included; three failures open a monotonic 60-second circuit.
There is no persistent approval cache; every reply binds the original request and
its original deadline. A review is stamped at local request start, not a provider-
supplied timestamp or a later poll. An expired result cannot authorize an entry.

Disabled primary never silently enables a fallback. Provider errors log only
bounded internal codes/request IDs. No raw body/validation error/auth header is
logged. A cancelled await propagates cancellation, never a placeholder confidence.
The fixture transport produces `provider=test`; native-source entry/position
approval from a MockTransport is explicitly vetoed.

Prompt injection is **not solved by a sentence in the system prompt**. News is
untrusted delimited data and output is constrained, but a model can still give a
bad confidence estimate. Deterministic news/calendar, source/time, owner, sizing,
spread, stop, position, kill/loss/drawdown and stage checks remain independent and
cannot be overridden by a reply. Confidence is heuristic, not a win probability.

## Entry and position decisions

`AISupervisor` implements Part 6's `EntryReviewer`. It reloads the exact persisted
pending proposal, verifies its source/code/model/configuration, fresh known safe
headline **and calendar** coverage, and, when enabled, matching classifier filter.
Reply bindings must match request/proposal/news/source/code/model exactly. The
provider cannot choose another side, price, stop, volume, symbol or permission.

- Missing/error/invalid AI returns no review; Part 6 finalizes the veto.
- Approve is quality review only; it never resumes or sends an order.
- Risk increase is vetoed. A reduction with `AUTO_REDUCE_RISK=true` can reduce only
  that signal's authorized cap; normal risk/lot constraints remain mandatory.
- A proposed reduction without that explicit owner flag becomes a pending owner
  suggestion **and vetoes the entry**; it is never ignored to trade at larger risk.
- Position reviews require positive reconciled owned ID/ticket/volume/magic/source.
  Close/reduce remains a short-lived pending proposal, never an automatic close.
- Native TP extensions now require position/volume/ticket/TP/entry/magic/time and
  code/model-bound review. Generic legacy native PositionReview is insufficient.
  Improving SL does not invalidate a hold review; changing TP/volume/ticket does.
  The authority rechecks again pre-send; the original 120% cap/90% trigger and
  owner `ALLOW_TP_EXTENSION` remain unchanged. Nothing can worsen SL.

Market/news advice is advisory and cannot certify calendar coverage or synthesize
an empty safe feed. Entry review receives the bound coverage summary, not invented
headline text. Part 8's real producers supply actual news/evidence; the separate
bounded headline sentiment method never overrides deterministic coverage gates.

## Owner proposals and stopped projection

Only `reduce_risk`, `rebalance_weights` and `close_position` exist. Unknown fields,
mode/live changes, martingale/grid/sizing/SL-removal/withdrawal/password requests or
unbounded weight steps are rejected. All four weight keys total exactly one;
disabled weights cannot be enabled. Suggestions bind original parameters, base
configuration/code/model/source, request/evidence hash, reason and expiry.

`SuggestionStore.decide(id, owner_id=..., approve=True/False)` expects an identity
**already authenticated by Part 9**, not a client-supplied owner ID. Decisions are
one-way; duplicate requests preserve original parameters/time/TTL. Approval is not
application. `apply` requires stopped lease, no open/unknown trades/unsettled
intents/reservation and current unexpired owner approval.

`apply` returns a **new fully validated frozen Settings** and records a projection
in `BotState.settings_overrides`; it does NOT edit `.env`, mutate the current
runtime, start trading, clear a kill/loss/high-water counter, rebind a checkpoint or
refresh stage evidence. Persist/review/recompose the stopped application explicitly.
An unacknowledged prior projection blocks further stale-base application/selection.
`get_settings()` does not silently load DB overrides. Existing policy-bound paper
checkpoints may refuse a policy change; no automatic capital reset/rebinding tool
is supplied. Keep matched code/config/DB/checkpoint and follow `MIGRATIONS.md`.

Position proposals cannot be applied through the settings method; Part 9's owner
close handler must re-read live ownership and execute through the existing durable
close path. A projection/selected model is never an unauthenticated trading permit.
Internal Python/DB administrators and injected protocol objects remain trusted;
this is not protection against a compromised local host or hostile Python plugin.

## Learning data and honest labels

The existing Signals/Trades/OrderIntents/BrokerDeals tables store the pre-entry
snapshot and accounting proof, so **no schema migration** is needed. Learning
exports immutable versioned content-addressed JSON rather than adding a fake table.

A label is admitted only after a positively reconciled whole-trade close with
original bound Signal/DecisionContext/command hash and complete exact entry/exit
fill legs. Volume, source/code/model/policy/account, identity, currency and all
profit+commission+swap+fee legs must agree. Unknown/reversal/unproven/open/non-USD/
unallocated-cost/incompatible records are excluded with reason counts. Label
availability is at least the last exit and ingestion timestamp, never the bar's
opening time. Breakeven is non-profitable, not a fabricated win.

The 32 fields use original closed RSI/ADX/DI, ATR-normalized EMA/momentum/MACD,
activity/range/geometry, higher-frame slopes, technical routing and UTC time cycles.
They never use realized profit, close time/price, final SL/TP/lock level, account
balance or label as a feature. Original snapshots are not regenerated from revised
candles or overwritten with outcomes. Money stays Decimal; normalization is finite
and schema-versioned. Non-USD historical conversion is not invented.

**Selection bias:** these are outcomes conditional on trades the old system
actually chose. Skipped opportunities have no executed counterfactual. Vote
associations are correlated and not causal independent strategy returns. Dataset
source/proof labels are software claims, not independent provider/data attestation.
Old-policy/code trades are intentionally excluded from the current bound cohort;
no silent relabel/rewrite/import is provided to bypass compatibility.

## Actual training and evaluation

The explicit offline job is:

```text
LearningEngine.export(account) → immutable saved dataset
    ≥300 available compatible labels → five expanding timestamp-grouped folds
    train cutoff = test_start − 12 M5 bars (60-minute time embargo)
    remove unavailable/overlapping labels and full 12-bar horizon
    fit scaler/model ONLY on purged training rows → untouched later predictions
    fixed threshold .70 → classification/conditional cost-inclusive return report
    fit final candidate on all now-available rows → INACTIVE registry version
```

Actual exit/ingestion intervals longer than 12 bars are still purged. Simultaneous
multi-symbol times are never split across train/test. Invalid/one-class/insufficient
folds reject the job instead of switching to shuffled/random CV. Preprocessing is
refit per training fold; the final all-data fit is **not evaluated on its own labels**.
No threshold or hyperparameter search tunes the OOS folds in this installment.

Both backends are real CPU code: sklearn StandardScaler/LogisticRegression with
portable numeric coefficients, and deterministic one-worker LightGBM with bounded
native text, version/feature/tree validation. **No pickle or joblib loading.**
LightGBM uses a native parser and assumes a trusted locally generated hash-bound
artifact; it is not a hostile-artifact sandbox. Artifact SHA256 is not a signature.

Report: fold time/count/hash proof, ROC AUC, Brier/base-rate Brier/skill, log loss,
balanced accuracy, fixed-threshold coverage, selected net-R and finite profit
factor. Defaults require AUC≥.55, Brier skill≥.01, ≥30 selected held-out records
and finite factor≥1.1. These are uncalibrated engineering gates, not validated
market thresholds. A perfect/no-loss subset does not invent Infinity as a pass.

The report is explicitly **NOT stage evidence**: no coherent replay, unseen skipped
entry/portfolio dynamics, market strategy equity drawdown or forward broker
performance is measured. Toy AUC or selected-trade returns cannot promote a model.

## Registry and restrained adaptation

Content-addressed JSON is confined under `data/models`; file/DB hashes, fixed
feature format, source/code/policy, algorithm/version, evaluation and chronology
are rechecked on load. Runtime source/code/policy mismatch, corrupt/symlinked/
oversized files, wrong scope/age or multiple active rows reject. Locked DB writes
ensure one active pointer across registry processes. Orphan inactive artifact files
are possible after SQL failure; they never imply selection or trading permission.

Owner selection/rollback requires stopped reconciled-flat state. Sequence is
candidate→backtest→paper→demo→live; backtest selection permits synthetic research
only. Native-data paper/demo/live additionally needs real-source reconciled data,
a passed current evaluation, actual MT5 account DTO and the existing chronological
artifact/ledger StageGate proofs for the **new model fingerprint**. Stage periods
cannot overlap its training-label interval. Source tags alone do not create proof.
Fresh runtime account/session/expiring live owner nonce consent still happens
separately at execution. No hot swap, automatic selection/resume or old evidence
reuse after model/config/code change.

`MODEL_FILTER_ENABLED=true` requires explicit stopped composition with the selected
matching scope/source/hash/age; the classifier can only veto below .70, never
increase confidence/risk to defeat AI/technical gates. Default false retains the
explicit rule-based baseline hash, which is **not a trained model**.

`AUTO_ADAPT_STRATEGY_WEIGHTS=true` enables only generation of an owner-pending
≤.02 rebalance from enough purged OOS vote associations after compatible export/
evaluation. It never auto-applies an unapproved strategy or risk change. Identical
correlated voters or overlapping uncertainty intervals mean no proposal. Any new
policy still invalidates prior stage evidence and needs independent revalidation.

## Explicit service usage

```python
from ai.ai_supervisor import AISupervisor

supervisor = AISupervisor(
    execution.database,
    execution.settings,
    execution.clock,
    execution.profile,
)
await supervisor.initialize()  # schema/code checks only; no HTTP login/server
result = await signals.evaluate(
    "EURUSD",
    reviewer=supervisor,
    news=actual_news_window,
)
# Quality approval is NOT owner/risk/stage permission and never auto-executes.
# Missing actual_news_window coverage or unreachable providers rejects.
await supervisor.close()
```

This is trusted composition, not `main.py run` or an unauthenticated endpoint.
Actual entitled fresh headline/calendar `actual_news_window` producers are Part 8;
no placeholder producer is offered as safe real data here. The complete runnable
offline example is `scripts/smoke_ai.py`, not the abbreviated composition above.

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_ai
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
.venv/bin/python -m scripts.smoke_ai
.venv/bin/python -m pytest -q
```

The diagnostic ignores host flags/.env/secrets, uses temporary state, verifies
availability fallback and missing owner risk-change veto, explicit simulated entry
with duplicate/closed replay safety, a complete fee-inclusive label, actual toy
training over five purged folds, inactive candidate/research selection, synthetic
promotion rejection and stopped owner projection. Every HTTP response/90 confidence
and toy label is scripted. There is **zero real HTTP network/SDK/order** and no
market performance evidence. Validation and packaging details are in `VALIDATION.md`.

Next: Part 8 supplies real entitled news/RSS/calendar producers and deterministic
impact/exposure coverage. Keep paper/mock/paused defaults and diagnostics now.
