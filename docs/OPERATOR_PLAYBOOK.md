# Operator playbook — reviewed progression, never automatic trading permission

**0.9.0 / schema 2. No credentials, genuine data or deployment authorization were supplied.**

## 0. Freeze the installation and protect state

Review all ordinary files in the cumulative archive; verify SHA and current manifest.
Install Python 3.11+ on the actual Windows/VPS environment, pinned dependencies and
native MetaTrader5 wheel there. The executed release baseline is Linux Python 3.13.14,
not a Windows SDK/terminal/network/NTFS/PostgreSQL/EXE/TLS certification.

Create a private .env from .env.example; keep DEMO_MODE=true, LIVE_TRADING=false,
PAPER_TRADING=true, mock backend and PAUSED until the applicable stage is reviewed.
No owner token or broker credentials are included. No password changes, withdrawals,
external account control, unrelated access or risk escalation is authorized.

`main.py` is non-trading: check-config, init-db for a genuinely NEW install, status,
explicit reviewed migration and diagnostic operations. Existing financial history,
latches, intents, owner stops, selected model and matching checkpoint must be preserved.
Do not initialize another DB or delete a checkpoint to make a gate green. A source/
policy/model change invalidates old evidence; schema 2 itself did not change in Part 11.

## 1. Historical stage: research → independent review

Run the offline guide in BACKTESTING.md. Start with engineering smoke, then your lawfully
obtained real history, causal availability, original AI/news/calendar capture, reviewed
contracts and costs. A zero-trade or losing result is valid. No threshold/quota adjustment,
revised confidence, future information, artificial "no news" or selectively deleted loss.

Review holdout/walk-forward selection and embargo/purging in PART_07_NOTES.md, regime
coverage, repeated parameter search, data completeness/licensing and fill sensitivity.
Backtest metrics alone do not prove robustness. Historical CSV and provider declarations
are not independently authenticated. The supplied replay is baseline-only and research-
classified; ML-filtered approval replay without a causal registry is refused.

A production backtest prerequisite must be a **separately independently reviewed**
`reflex-stage-v1` historical_real artifact, binding current strategy_config_hash,
code_hash, model_sha256, completed started_at/finished_at, reviewed dataset_path and exact
raw dataset_sha256, owner scope, cost-inclusive/lookahead-reviewed metrics:
closed_trades ≥100, finite profit_factor ≥1.1, sampled max_drawdown_percent ≤5,
unexplained_gaps=0, costs_included=true, lookahead_free=true (configured ceilings apply).
There is no one-click research-label conversion. Do NOT rename/edit the replay report
and claim it is genuine qualification. If authentic contemporaneous AI/context/provenance
or an appropriate model cannot be proved, remain in development and do not advance.

Production `source=historical_real` is an owner/provenance assertion requiring independent
review; the import helper verifies cryptographic byte binding/policy/quality, not vendor
truth. No reviewed production artifact is bundled, and no native stage is passed here.

## 2. Stage artifact import — actual signed owner, stopped/flat

The CLI is a local privileged operator seam, not an unauthenticated HTTP endpoint.
It does not start a broker or API. Import uses the existing original Telegram Mini App
HMAC verifier; a raw owner integer, fake token, unsigned initDataUnsafe or stale bearer
is never accepted. Signed initData is replayable within its short TTL: store it only in
`data/private/owner.initdata`, not command-line arguments, logs, Git or the release ZIP.
Use the ORIGINAL fresh owner launch string; do not reserialize its signed parameters.

Stop the runtime normally, reconcile owned positions/intents, obtain genuine owner
initData, review the exact production artifact, and confirm its literal SHA:

```powershell
# No placeholder credentials or evidence is provided by this project.
$report = 'data/stages/independently_reviewed_backtest.json'
$sha = (Get-FileHash $report -Algorithm SHA256).Hash.ToLowerInvariant()
.\.venv\Scripts\python.exe -m scripts.stage_report --env-file .env import --report $report --confirm-sha256 $sha --owner-initdata-file data/private/owner.initdata
```

The existing DB/schema must already be present. Import requires native-source config,
no active session, PAUSED desired state, reconciled flat trades and no unsettled intents.
It revalidates current code/model/policy, report/dataset bytes, owner, dates and metrics;
paper/demo also recheck original authorized intents, complete fill legs, actual-source
trade records and continuous cash/credit-adjusted account snapshots. Identical reviewed
artifact import is idempotent. Audit stores owner/stage/hash only, never the bearer.
It appends an evidence record ONLY: **not resume, risk reset, model activation or live approval**.

## 3. Paper on real native data (not mock evidence)

Requires current independently reviewed backtest evidence, actual MT5 read-only data,
known broker symbols/contracts/currency, genuine licensed realtime news and complete
calendar, actual structured AI policy, a separately selected model if enabled, configured
owner interface and account reconciliation. Native-source paper is virtual execution over
real data; synthetic/mock/FakeSDK paper is permanently development-only/ineligible.

On Windows, follow DEPLOYMENT_WINDOWS.md: interactive logged-on user, terminal path
configurable (default C:/Program Files/MetaTrader 5/terminal64.exe), background/minimized
terminal where supported, one explicit daemon OR watchdog, never simultaneous children.
No Session 0 unattended service, password-based logon task or native process force kill.
Task Scheduler is logon-only; watchdog 30s; actual native execution remains untested here.

`python -m app.bot --env-file .env` starts PAUSED and verifies existing schema, OS lock,
SQL lease, private health, sources/model/news/profile. Get owner private Telegram/Mini App
status; prepare/confirm fresh owner resume. Observe real-source paper for ≥14 completed
days, ≥100 reconciled closed trades, quality limits and continuous account coverage.
If fewer valid entries exist, continue observing rather than forcing six/day.

## 4. Export paper/demo ledger evidence without inventing numbers

Given the exact PRIVATE account_key from your local SQL ledger and completed UTC range:

```powershell
.\.venv\Scripts\python.exe -m scripts.stage_report --env-file .env export --stage paper --account-key 'YOUR_EXACT_PRIVATE_ACCOUNT_SCOPE' --from '2026-10-04T00:00:00Z' --until '2026-10-18T00:00:00Z' --output data/stages/paper_review.json
```

These dates/scope are illustrative placeholders for operator input, **not supplied
observations or a passing stage**. Future dates, insufficient count/PF/losses/duration,
wrong source/scope/credit, missing legs, charges, gaps or stale policy/model fail.
Exporter recomputes account-currency PF with unattributed negative charges and sampled
cash/credit-adjusted drawdown and calls the same core ledger proof verifier. A filename
or backend flag cannot manufacture a native ledger. No authentic native data exists
in this delivery, so a native export here should fail instead of generating fake proof.
Export does not insert evidence; review and separately signed-owner import are required.

## 5. Broker demo, then explicitly approved small live

Broker demo requires actual native DEMO account identity/source (not simulated account
or a real account mislabeled demo), valid chronological backtest→paper artifacts and
fresh owner/risk/news/model controls. Select DEMO_MODE=true, PAPER_TRADING=false,
LIVE_TRADING=false, real backend privately; startup remains PAUSED. Observe ≥14 completed
days and ≥100 actual closed demo trades within native caps. Export `--stage demo`,
independently review and import with the same stopped/flat signed-owner workflow.

Only after chronological valid backtest→paper→demo, cash/credit/source/ownership
reconciliation and native platform/security review may the actual owner separately
configure LIVE_TRADING=true, DEMO_MODE=false, PAPER_TRADING=false, real backend and
explicitly prepare/confirm the current single-use account/session/config/code/model/
evidence-bound live nonce. Default live risk ≤0.1%/trade; never raise to chase losses or
force turnover. Broker account must be actual native REAL. Runtime still starts PAUSED.
No stage import, model selection, proposal approval or daemon restart supplies this
live approval or clears kill/daily-loss/drawdown/recovery state.

## 6. Daily operations and failures

- GET dashboard/status reads stored projections, not broker/provider/order requests.
- Pause/kill fences new entries, not account flatten/recall of already accepted work.
  Protective owned-position monitoring remains active. Owner close requires confirmation,
  exact ownership and idempotency; close-all reports per-position residue, not atomic flatten.
- Daily loss/drawdown/aggregate/position/spread/low-confidence/news/MT5 instability vetoes
  are primary. Do not weaken settings or fabricate a baseline to resolve them.
- Timeout ≠ no fill. Accepted/unknown native work is quarantined and drained/reconciled;
  never resend blindly, delete intents, kill a still-alive MT5-calling thread or replace
  a stale alive child. Watchdog restarts only a proved-exited direct child, PAUSED.
- Owner proposals approve/reject ONLY. No automatic settings application, registry
  activation, adaptation, trade or risk increase. Learning produces inactive candidates
  under preserved labels, purged/embargoed evaluation and reviewed scoped registry changes.
- Profit locks are net estimates at a nominal achievable SL, not cash insurance. Gaps,
  spread/FX/fees/freeze rules and rejected modifications can defeat estimated locks.
- Graceful stop: `scripts.stop_runtime` persists downward-only stop; wait for jobs,
  accepted writes/native Futures, SQL lease and OS lock release. Never clear global stop
  or copy data merely to satisfy a watchdog. No automatic resume on logon/restart.
- Backups: WAL-aware SQLite API; online DB-only is NOT coherent paper/model recovery.
  Stopped coherent bundles need matching DB/checkpoint/selected-model/news/candle state,
  verified hashes/ownership/privacy and actual-platform restore testing. No auto restore.
- Owner notices are at-most-once ATTEMPTS, not exactly-once guaranteed delivery. No token
  means no Telegram transport. Diagnose missing alerts locally; do not replay uncertain sends.

## 7. Validation still required before deployment

Actual Windows SDK/terminal response/errors/fees/contracts/currency, login-only task/VBS/
PowerShell syntax/execution, TLS/Mini App launch/genuine initData, provider entitlement,
PostgreSQL roles, NTFS ACLs/backup recovery, PyInstaller, vulnerability audit and long-running
soak/drain/reconnect tests are unexecuted here. Human source review is not native validation.
The delivered tests are offline/synthetic/software/cryptographic fixtures, not real owner
approval, genuine quote evidence or capital/strategy qualification. See VALIDATION.md.


## Part 13 frozen historical ML operator boundary

Use `ML_REPLAY.md` for optional single frozen model/corpus contracts and
`scripts.verify_replay_model` for offline reconstruction WITHOUT any ledger/selection.
Default model filter remains disabled; deliberately enabled approval replay requires
verified pre-replay input and actual finalization/execution inference, never a guessed
probability or disabling the setting. New private import does not call owner activate()
or authenticate genuine Telegram identity. Verify original captured model availability,
training/label/selection chronology and unbiasedness independently; hashes/declarations/
reconstruction cannot do that. Synthetic/model-evaluation/replay outputs remain ineligible
stage evidence. Preserve production capital/DB/model/stops; no reset, automatic resume,
conversion to StageGate evidence or real trading authorization.


## Part 14 completed-bundle audit boundary

`python -B -m scripts.audit_backtest --run <private-completed-replay>` verifies exact
closed research files, recomputes metrics/decision bindings and reads ONLY a captured
private MEMORY SQL image. An optional external bundle SHA is not a local signature.
A consistent bundle can retain simulated exposure/latched stops; auditor never resumes,
closes, resets, repairs/re-seals, activates production models or emits StageGate evidence.
Old unsealed/partial/changed/WAL-bearing runs refuse. REPLAY_AUDIT.md documents bounds,
LightGBM/training non-reexecution and authentic history/owner/native limits. All existing
financial-history/capital/protection and genuine stage/live obligations remain separate.
