# Part 13 — frozen causal ML artifacts for offline replay

**0.11.0 · schema 2 · cumulative Parts 1–13 · 2026-10-04.**

Safest continuation: one **strictly offline, frozen, pre-replay research model** plus
its exact learning corpus. No model/history/credentials were supplied by the user.
All demonstrations use deliberately engineered vectors, labels, quotes and archived
approval fixtures; they are NOT predictive skill or authentic historical availability.
Nothing in this installment connects, deploys, selects a production model, authenticates
an actual owner, qualifies a stage, resumes the production runtime or authorizes trading.

## Actual source additions and integration

- `backtesting/contracts.py`: optional frozen `manifest.model` with distinct confined
  SHA-bound artifact/corpus roles, explicit aware availability/selection times and
  `purpose=research_only`. One model only; selection cannot be after replay starts.
- `backtesting/dataset.py`: captures both new raw input files with the existing bounded
  read/hash/total-input guards. File roles (including manifest) reject case collisions.
- `backtesting/model_replay.py`: bounded corpus/schema/source/code/policy/label/horizon/
  embargo/age validation, actual purged OOS recomputation and **exact existing trainer
  artifact reconstruction**, BEFORE creating any replay directory or financial ledger.
- `ai/replay_binding.py`: immutable selection observation; causal validation and exact
  private run/manifest/SQLite identity checked whenever the selected model is read.
- `ai/model_registry.py`: preserves ordinary native/synthetic registration/owner/stage
  gates; adds read/revalidation of the verified HISTORICAL research binding and actual
  same-snapshot `replay_inference`. An ordinary historical candidate cannot be selected
  as an active replay model without the private research binding.
- `ai/model_trainer.py`: existing real sklearn logistic/LightGBM CPU recipe, now explicitly
  one BLAS worker for logistic fitting. `threadpoolctl==3.7.0` is an explicit direct pin;
  that exact transitive distribution was already installed/locked. No dependency upgrade.
- `strategy/signal_store.py`: enabled historical ML requires real registry inference on
  the **immutable pre-entry vector** at BOTH finalization and persisted-context/risk
  revalidation. It stores probability, acceptance, threshold, vector/schema/proposal/
  model/source/code/policy/config/selection digests. Rehashing a fake probability is
  insufficient; inference is recomputed. Valid subthreshold observations are also logged.
- `backtesting/backtester.py`: verifies a single fixed model before run creation, imports
  the snapshot ONLY into new `data/replay.db`/`data/models`, binds the actual model profile
  to all shared engines and preserves AI/news/risk/owner/idempotency/position protection.
- `scripts.make_backtest_model_fixture`, `scripts.synthetic_replay_model`,
  `scripts.verify_replay_model`, `scripts.smoke_replay_model` and six new test modules.
- Active read-only release/readiness defaults now use `RELEASE_13_MANIFEST.json`.
  Previous guides/manifests/archives remain historical snapshots, not current manifests.

The private import **does not call `ModelRegistry.activate`**, bypass its native owner
checks, or describe the offline principal as genuine authentication. It requires the
new untouched paused replay ledger, exact captured input/run identity and the initial
replay clock; it cannot import into existing model/trade/risk state or midrun. Only the
new research DB's model pointer is populated. The production registry is not read.

## Causal and resource contracts

`LearningDataset` remains the original strict selected-trade-label contract: immutable
32-feature vectors; decision ≤ entry < exit ≤ label availability ≤ export; deterministic
ordered unique IDs; finite net/risk USD and explicit costs/source/policy/code bindings.

Required chain:

```
all training labels <= corpus export <= artifact created_at
                    <= declared available_at <= selected_at <= replay_from
trained_through < replay_from - configured embargo
max(exit_at, decision_at + configured label horizon) < replay_from
```

The original grouped expanding walk-forward folds keep training labels strictly before
fit cutoff, purge overlap and embargo; no shuffled substitute or OOS threshold tuning.
Evaluation must genuinely pass its existing fixed configured gates. Final weights,
folds and evaluation are reconstructed from the **exact supplied corpus**, at the artifact's
original declared created_at, and must match the supplied **canonical trainer artifact
bytes/SHA** exactly. No silent normalization, substitute algorithm or accepting declared
metrics without recomputation. Reconstruction runs in a worker before replay begins;
there is no fitting, switching, threshold tuning or model fallback inside the replay.

Hard research bounds: **5,000 rows / 16 MiB corpus**, in addition to configured dataset
limits; fixed 32 features; existing 3–20 folds and bounded estimators; artifact <= configured
64 KiB–4 MiB maximum (default 1 MiB); original complete input cap 128 MiB. Arrays/nodes
are bounded BEFORE sample/matrix construction. Portable logistic JSON or version-matched
bounded LightGBM native text only; no pickle/joblib, URLs, executable paths or downloads.

Exact reconstruction depends on the reviewed current code, CPU recipe and installed
libraries. A different float result/library/model version fails closed. It is a strong
**internal consistency check**, not an attestation that those bytes actually existed in
the past. `as_of`/manifest timestamps are declared historical inputs, not observed original
training wall-clock records. Reconstructed probabilities are not proven calibrated.

A fixture-origin corpus is accepted ONLY with an explicitly synthetic fixture history.
Changing source from MT5/SYNTHETIC to HISTORICAL, relabeling an old corpus/code/policy,
changing model/corpus/evaluation hashes, inventing earlier label times, or presenting
selected-trade labels as unbiased opportunity data does not establish authenticity.
Authentic captured historical model availability and predeclared model/hyperparameter/
threshold selection require external independent review. No such input was supplied.

## Executable artificial example

```powershell
# New disposable research paths; never overwrite an existing ledger.
.\.venv\Scripts\python.exe -m scripts.smoke_replay_model
.\.venv\Scripts\python.exe -m scripts.make_backtest_model_fixture --output data/fixtures/ARTIFICIAL_ml_001
.\.venv\Scripts\python.exe -m scripts.verify_replay_model --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env
# Default veto/paused analysis; model alone is NOT permission to approve or enter.
.\.venv\Scripts\python.exe -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env --output data/backtests/ARTIFICIAL_ml_analysis_001
# Explicit PRIVATE simulated entry/close; artificial approval is never an actual AI call.
.\.venv\Scripts\python.exe -m scripts.backtest --manifest data/fixtures/ARTIFICIAL_ml_001/manifest.json --env-file data/fixtures/ARTIFICIAL_ml_001/ARTIFICIAL_research.env --output data/backtests/ARTIFICIAL_ml_sim_001 --review-mode synthetic_research --simulate-orders --close-at-end
```

On Linux use `python` from the active tested environment instead of the Windows path.
`--algorithm lightgbm` uses the real bounded LightGBM recipe; `--relationship inverse_quality`
deliberately engineers a corpus that vetoes the fixture candidate. Both are fixture
construction choices, NOT trading parameter suggestions or profitable strategies.
Use a clean research shell: ordinary backtest/verifier CLI Settings retains environment
precedence, validates inherited flags and refuses live; it does not silently disable ML.
The fixture generator and smoke use init-only Settings and load no credential file.

See `ML_REPLAY.md` for the exact model input contract and rejection/workflow details;
`BACKTESTING.md` for unchanged causal quotes/news/reviews/costs/limits; `README.md` and
`DEPLOYMENT_WINDOWS.md` for complete original setup/deployment instructions. Extraction
is not installation/service/native validation. No Task Scheduler task is registered here.

## Validation status and unchanged safety

Full current working source: **2327 passed in 472.53s**, **184 new regressions** since
Part 12. Lint/format/compile/pip/exact freeze/ten smokes/JS/news/isolated CLI passed.
Focused new regressions previously passed 181 before three final sanitized-estimator
cases were added. Authoritative complete extraction also passed **2327 tests in 476.68s**
and the full lint/format/compile/lock/ten-smoke/JS/news/CLI pipeline. Results are recorded
in `VALIDATION.md` / `RELEASE_13_VALIDATION.json`; extraction reused tested Linux dependencies,
not a native Windows install. Final docs/archive rebuild preserves the tested runnable bytes.
Initial RAM-backed-temp subprocess timeouts were resolved by moving owned fixtures to
disk, without changing any test deadline/assertion or production gate. New smoke: **16 fixture-only checks**, actual portable inference
approval/veto, default zero trades and original sentinel ledger preserved.

Original `.env.example` still has paper/mock/paused/demo=true/live=false and ML disabled
by default. Explicit research ML does not disable the ordinary AI-confidence/news/risk
or persisted execution guards. Original DB/kill/loss/DD/SL/capital/history are not reset.
Missing/corrupt/stale model vetoes entries; protective management of already owned
positions continues. Six/day remains a target, never a forced minimum; max 12 remains.
No martingale, averaging, doubling, automatic risk escalation or withdrawals.

Schema **2** is retained: the new research binding is metadata inside the existing model
row of a disposable private DB, not a new production table or migration. This code changes
code-bound approvals/evidence; old artifacts are not automatically relabeled current.
Actual existing `data/reflexbot.db` was preserved, not opened/migrated to bypass readiness.

Every replay report remains **promotion_eligible=false**. Model evaluation, proof labels,
exact reconstruction, valid archive shape, synthetic auth/SQL and read-only readiness
are NOT `reflex-stage-v1`, genuine owner auth, native facts or live approval. All original
backtest → paper → demo → explicit small-live gates remain mandatory and unqualified.
