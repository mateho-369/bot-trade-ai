# Offline frozen-model replay — operator contract

**0.11.0 / schema 2 / research-only. No production model selection, broker startup or trading authorization.**

## 1. What changed, and what did not

Part 11's baseline replay deliberately refused enabled-ML approval without a causal
bound model. Part 13 does NOT remove that requirement. It now verifies one immutable
pre-replay model, its complete selected-trade training corpus and selection declaration,
recomputes the purged evaluation and exact artifact, then imports a snapshot into a
**new private BACKTEST registry**. No production registry/data/credentials are copied.

`MODEL_FILTER_ENABLED=false` remains the example default. With it true and no model,
non-veto replay still refuses; veto-only analysis remains possible. A supplied model
with ML disabled is an error, not an automatic config change. A probability/model
alone never grants an AI approval, owner resume, executable order or stage qualification.

## 2. Optional `reflex-history-v1` model field

Add ONE `model` object to the existing historical manifest:

```json
{
  "model": {
    "format": "reflex-replay-model-selection-v1",
    "purpose": "research_only",
    "description": "Explain the source, original capture and selection record; not an authenticity claim",
    "available_at": "2024-01-08T12:00:00+00:00",
    "selected_at": "2024-01-08T12:05:00+00:00",
    "artifact": {
      "path": "models/frozen_model.json",
      "sha256": "REPLACE_WITH_EXACT_64_LOWERCASE_RAW_FILE_SHA256"
    },
    "learning_dataset": {
      "path": "training/frozen_learning.json",
      "sha256": "REPLACE_WITH_EXACT_64_LOWERCASE_RAW_FILE_SHA256"
    }
  }
}
```

This is a field illustration, NOT runnable fake-hash configuration. Use the real raw
SHA-256s of captured local bytes. Paths must be distinct portable relative local files,
not URLs, drives, symlinks, traversals, executables or case aliases of another input
(including the manifest). Both payloads contribute to the complete dataset digest and
are preserved in `inputs/`; they cannot be silently replaced by a registry lookup.
Extra fields/model schedules/hot-switching/production-purpose claims are refused.

The artifact must be the exact canonical `reflex-model-v1` output of the current real
`ModelTrainer`. Its schema/32 feature names, historical source, code/feature-origin code,
policy, corpus semantic digest, sample count, chronology and full OOS evaluation must
bind. Corpus must be strict `reflex-learning-dataset-v1`; vectors are pre-entry only,
labels closed/available, finite costs/risk included. A native MT5 artifact cannot simply
be relabeled historical. `fixture` origin requires `synthetic_fixture` history.

Raw corpus-file SHA and canonical learning-dataset SHA are different concepts: each is
checked in its proper binding. Reformatting a corpus can change its file SHA while
retaining its semantic SHA. The MODEL artifact must match the trainer's canonical bytes
exactly, not a pretty-printed/repackaged model with a new convenient hash.

## 3. Availability, purge and freeze

All sample events are UTC-aware and satisfy decision ≤ entry < exit ≤ label availability.
Every label ≤ export ≤ created_at ≤ declared availability ≤ declared selection ≤ replay_from.
Latest label must be **strictly** earlier than replay_from minus the configured embargo;
nominal label horizons/actual exits must be strictly pre-replay. Selected/trained source
and current policy/code/schema must match. Model age is checked at start and AGAIN as
replay time advances; inference is restricted to the declared replay interval.

Availability/selection may equal replay_from, provided training/embargo constraints
already pass; this means known at the starting boundary, not information revealed later.
No intrarun schedules, retroactive selection, sliding fitting or threshold changes.
The actual trainer is invoked in a worker once before creating run output; all subsequent
probabilities use the frozen supplied portable weights. Purged timestamp-grouped expanding
folds, train-only scaling and fixed-before-OOS threshold remain the original recipe.

Every fit cutoff, sample/fold/evaluation digest, final weight and canonical artifact SHA
must reproduce exactly under current trusted code/libraries. A numeric/library mismatch
withholds the input; no epsilon relaxation, repair, alternative model, guessed probability
or lowering the filter occurs. Original label corpus origin and account-scope hash are
retained. Maximum research corpus 5,000 rows / 16 MiB; arrays/nodes are bounded before
sample/matrix allocation. Existing configured minima/maxima/folds/estimator bounds also apply.

## 4. Private import, not owner activation

The runner creates a NEW output directory and complete input copies only after all
preflight reconstruction/archive/schedule checks. It imports into exactly
`<new-output>/data/replay.db` and `<new-output>/data/models/<SHA>.json`. Import requires:

- initial replay clock and exact run/config/model/corpus identity;
- new untouched paused state, zero sessions/heartbeats/overrides/kill/history;
- no existing model, signal, order, trade, risk or deployment-evidence rows;
- unchanged captured model/corpus and matching captured manifest.

No original owner/model directories or DB are read. `ModelRegistry.activate()` is not
called. The snapshot's BACKTEST pointer is NOT production activation or genuine owner
approval. The existing private replay principal exists only for explicit simulated
resume; a raw ID/dummy token is not actual Telegram authentication or stage/live consent.
Only `--simulate-orders` may resume that new private ledger; the production runtime is
never resumed. Existing output directory => refusal, not reset/recovery-by-overwrite.

## 5. Both approval and execution revalidate

For enabled historical ML, shared `SignalStore.finalize` extracts the 32-feature vector
from the fixed published snapshot and calls the ACTUAL private registry. It logs an
observation containing probability/acceptance/threshold and proposal/vector/schema/model/
selection/source/code/policy/config hashes. Below-threshold probabilities reject even
an artificial/archived confidence-90 approve; missing/invalid/stale selection rejects.

The approved context embeds the same observation and digest. `approved_context` reruns
inference and rechecks all bindings when fetching a signal and when shared risk evaluates
the command at pre-send revalidation. Removing a gate, editing probability or rehashing
every caller context does not fabricate a model pass. Artifact/registry/run-marker/
captured-manifest changes, ambiguity, scope/code/config mismatch and expiry fail closed.
Unexpected estimator/file/ledger exceptions become sanitized entry vetoes, not fallbacks.

All ordinary technical, exact archived-review timing/news/code/model hashes, AI confidence,
news/calendar, spread, costs, daily/aggregate loss/DD/margin/trade/position caps, owner state,
ownership, idempotency, order age/drift and uncertain-result controls still apply. Replay
reviews remain provider=`replay`, NEVER relabeled Ollama/OpenAI to evade a provider fence.
`archived_review_matches` counts proposal lookups, not bound model/AI approvals; check
final signal/operation/veto records. Missing model must not disable owned-position SL work.

## 6. Executable artificial tests, not research conclusions

```bash
python -m scripts.smoke_replay_model
python -m scripts.make_backtest_model_fixture --output data/fixtures/ARTIFICIAL_ml_001
python -m scripts.verify_replay_model --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env
python -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env --output data/backtests/ARTIFICIAL_ml_001 --review-mode synthetic_research --simulate-orders --close-at-end
# A different NEW fixture, with deliberately reversed engineering labels:
python -m scripts.make_backtest_model_fixture --output data/fixtures/ARTIFICIAL_ml_veto_001 --relationship inverse_quality
# Optional actual bounded algorithm fixture, never an algorithm recommendation:
python -m scripts.make_backtest_model_fixture --output data/fixtures/ARTIFICIAL_lgb_001 --algorithm lightgbm
```

The generator creates explicitly artificial corpus/model/history files and a no-credential
research env. It intentionally designs noisy labels around a technical-score relationship
(or its inverse). High observed probabilities/finite artificial OOS factor are consequences
of that construction, NOT economic skill, provider confidence or historical strategy proof.
It does not export authentic broker/training records. Verification creates no DB/run/model
selection. Backtest CLI inherits ordinary environment precedence; use a clean reviewed
research shell, never live configuration or your production credential env.

For authentic research, independently capture/review the historical inputs and the original
model/corpus availability/selection records BEFORE inspecting replay outcomes. Construct
and train the strict historical learning corpus under the reviewed matching policy/code;
`ModelTrainer.train(..., as_of=declared_created_at)` alone does not prove that time was the
actual original training wall clock. Supply the original canonical artifact and real
external provenance, not this fixture workflow. No corpus/model was supplied in this task.

## 7. Outputs and limits

`run.json`/`report.json` include model/selection/corpus/fold/binding digests and fixed-time
observations; `signals.jsonl` contains actual approval/veto probability observations;
`inputs/` preserves the exact source bytes. Model metadata/audit lives only in private
SQLite. The ordinary cost/equity/trades/operations/OHLC reports remain unchanged.

All outputs explicitly have **promotion_eligible=false**, provenance unverified, no actual
owner authentication/production activation and no genuine paper/demo ledger. Model OOS
selected-trade labels are not unbiased all-opportunity labels or independent stage results.
Hashes/JSON timestamps/proof strings can be fabricated; local administrators who modify
both code/DB/run marker are outside the trusted-filesystem model. File checks are bounded
consistency defenses, not pinned-FD race-free hostile-owner containment. Dependencies,
interpreter and libraries are trusted; untrusted binary/plugin execution is not qualified.

No downloader, authentic feed entitlement, data/model timestamp attestation, calibration,
profitability guarantee, automatic optimizer/promotion, native connection, task deployment,
real provider/Telegram request or live consent is produced. All original progression,
kill/loss/protection and genuine owner controls stay mandatory. See `VALIDATION.md` for
actual Linux/complete extraction results and outstanding Windows/broker/native obligations.
