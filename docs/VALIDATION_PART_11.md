# Validation record — cumulative Parts 1–11

Date **2026-10-04**, release **0.9.0**, schema **2** retained (15 tables).
Executed environment: **Linux / Python 3.13.14 / Ruff 0.16.10**. Python 3.11+ and
actual Windows native MT5 remain targets, not validated production environments.
No genuine credentials/data/owner launch, actual broker/provider/Telegram requests,
real orders, native deployment, task registration, EXE compilation or live authorization.

## Working ordinary source — executed final-code validation

| Check | Executed result |
|---|---|
| Full pytest | **1970 passed in 228.70s** |
| Additions since Part 10 | **259** (1711 → 1970) |
| Ruff check | Passed |
| Ruff format check | **284 inputs** already formatted (Python + documented code fences at that point) |
| compileall | Passed for actual app/core/trading/strategy/AI/news/API/backtesting/scripts/tests/entry files |
| pip check | No broken requirements |
| pip freeze | Exact normalized match with requirements.linux.lock.txt |
| Eight offline smokes | mock, risk, signals, AI, news, owner_interface, runtime, backtest all passed |
| JS/Telegram-loader syntax | `node --check` passed |
| News source inspection | Passed; no HTTP/provider requests |
| Isolated operator CLI | check-config/init-db/status passed, new temp schema2 / paper / mock / paused |

No new runtime dependency. Smokes use temporary data rather than production DB/financial
history. Parts 1–10 regression suite remains included. Previous Part 10 validation is
retained as `VALIDATION_PART_10.md` and in its immutable historical archive/manifests.
New documentation may increase the final formatter input count; runnable bytes are
matched to the independently validated extraction and final lint/format rerun.

### New historical replay regression scope

- Raw digest/size/UTF-8/header/type/decimal/grid/UTC/session/order/availability bounds;
  symlink/traversal, file-authoritative document cloning, immutable bytes/copy-on-read.
- Tick/batch original freshness, full M1 aggregate publication and missing/higher-frame
  group rejection, prefix causality, signed FX/declared linear profit/margin and alias scope.
- Archived known-at headline/calendar semantics, completeness/freshness/impact/exposure,
  unknown-news veto and strict entry/position review time/news/code/model/proposal binding.
- Net costs, defined/undefined PF, non-USD labeling, close-ordered streaks, daily returns,
  credit/deposit/withdrawal-neutral sampled equity drawdown and stale/gap disclosure.
- New isolated runner/ledger/owner control, default veto, synthetic entry + core costs,
  durable idempotency and terminal rejection after resume, protection while killed,
  ticks/SL gaps and carried OHLC stop-first ambiguity, midnight baseline-before-gap,
  original context/persisted historical binding, enabled ML policy refusal, unknown news,
  delayed archived reply visibility, exact private run inputs and bounded/failure journals.
- Fractional replay start: declared OHLC boundary job causality, no invented partial-bar
  price, timer-grid-independent entry, correct completed-M1 coverage counting.
- Production stage tooling: research/wrong source/scope/model/policy/dataset/digest/time/
  metrics/path/active-state/auth rejection; signed fixture identity, append/idempotence,
  never resume/live; missing-schema CLI never creates an empty SQLite file or parent.
- Positive paper and demo ledger **software export shape**: hand-authored TEST_ONLY native
  tags/intents/entry+exit legs/100 trades/14-day synthetic account snapshots; full existing
  ledger proof verifier without reducing policy. Export never inserts/promotes/overwrites.
  Actual CLI import with signed TEST_ONLY bearer/clock creates disposable test evidence
  only. None proves native collection, genuine owner identity or valid qualification.

### Offline smoke facts

`smoke_backtest`: **13 checks**, one artificial simulated entry + close, **−0.54000000 USD**
closed-trade net with costs, immutable re-loadable input bytes, existing directory refusal,
no production state/reset. **0 native broker/provider/Telegram requests, real orders,
child processes, loaded credentials or SDK imports**. Artificial fixture/reviews and
promotion_eligible=false. Not a genuine-history strategy or profitability evaluation.

`smoke_runtime`: 13 checks with scripted Popen objects, no actual child/native/provider/
Telegram/order operation, no automatic resume or evidence eligibility.
`smoke_owner_interface`: 9 checks with ASGI/aiogram fixtures and simulated owned actions,
not actual TLS/private Telegram launch/owner authentication.
`smoke_news`: scripted HTTP/news/calendar and no genuine entitlement/completeness proof.
Other smokes are likewise marked engineered quotes/toy labels/transports, not stages.

## Independently extracted candidate

Authoritative complete extraction: `_part11_complete_clean/mt5_ai_reflex_bot`.

| Clean check | Executed result |
|---|---|
| Full pytest | **1970 passed in 234.29s** |
| Ruff check / format | Passed; **286 inputs** already formatted |
| compileall / pip check / exact freeze | Passed |
| All eight offline smokes | Passed; no genuine provider/Telegram/native broker requests |
| JS/loader syntax / news / isolated operator CLI | Passed |
| Candidate ZIP + manifest | CRC and every recorded SHA/length verified |
| Complete module set | All **261 Python source files** included; set parity checked |
| Final runnable/config bytes | Matched exactly with independently validated extraction |

Raw command records are included in `RELEASE_11_VALIDATION.json`. Extracted source uses
the existing tested Linux dependency venv, **not a fresh install or Windows/native
wheel/platform validation**. Final rebuild changes documentation/manifest only; final
CRC, all recorded SHA/lengths, full literal guide and source parity are rechecked.

An initial candidate's overbroad credential-name exclusion also excluded the legitimate
`tests/test_telegram_initdata.py` (82 tests). The separate extraction exposed **1888 vs
1970**, so that candidate was rejected. Credential exclusion now preserves source
modules, complete source-set equality and exact full-count assertions are mandatory.
The corrected complete extraction above is authoritative, not the earlier partial run.

## Historical releases preserved

- Part 10: 0.8.0, 1711 tests; immutable ZIP SHA
  `9d18d97ab70a1c166a4c5eea2a4ffebda1f1b93bee2410a161e57484a8271a73`.
- Part 9: immutable ZIP SHA
  `7462674886582d221cf905aad2b01e881e2f453374d6a52c6d97973661571304`.
  Its historical manifest stale part/test fields are retained, not retrospectively
  rewritten. The source integrity was verified in Part 10; current-source hashes are
  in RELEASE_11_MANIFEST.json only.
- Part 8 SHA `cae0ca6228ada308a1ac4423f55d229fece7b0440f5a93e7169f2f0c221f544e`.

Earlier manifests bind their historical snapshots, NOT the modified cumulative source.
Every archive's SHA was rechecked unchanged; no .env/DB/log/checkpoint/environment/provider
credential is bundled. SHA/manifest integrity is not a signed release or trading permission.

## Explicit residual limits

Actual Windows MT5/SDK/terminal/broker errors/fees/contracts/currency/soak, VPS logged-on
VBS/PowerShell/Task Scheduler execution, genuine provider entitlement and complete real
calendar, genuine Mini App/initData/TLS, NTFS ACLs/backups/recovery, PostgreSQL/psycopg,
PyInstaller and vulnerability audit are not validated. No real-history robustness,
financial profit, genuine stage promotion, actual owner live approval or deployment.

Historical file origin/session/spread/availability/contract/provider assertions are
not independent authenticity attestation. Static linear/margin/elapsed swap/slippage
assumptions are not liquidity/variable-contract/broker-fill predictions. OHLC cannot
establish intra-bar path/fill time/trailing/intratick drawdown; sampled DD can understate
extremes. No chosen favorable intrabar path or guaranteed profit-lock cash.

Backtest v1 cannot perform ML-filtered approval replay without a bound causal approved
registry and **refuses** instead of disabling the enabled filter. Production learning/
registry gates remain unchanged. Archived position reviews have schema/binding/causality
coverage and core trailing has its existing regression coverage; a historical end-to-end
TP-extension dataset with genuine archived review/provenance was not supplied or qualified.

No research→qualified-stage converter exists. Every reflex-backtest-v1 report remains
ineligible even if its origin is declared historical_import. Separately reviewed signed-
owner authentic production artifacts and original native paper/demo proofs remain required.
Administrative tampering with trusted local SQLite/artifacts or an owner bearer/token is
outside software-only provenance claims; least privilege/private files/source review and
actual-platform security/operational validation remain necessary before deployment.
