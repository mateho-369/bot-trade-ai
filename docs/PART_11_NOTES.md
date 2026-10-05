# Part 11 — causal replay, tests, stage review and operator usage

**Release 0.9.0 · schema 2 · cumulative Parts 1–11 · 2026-10-04.**

This installment implements ordinary runnable Python files, not pseudocode. `PART_11.md`
contains their complete literal source alongside the current configuration, full
README, all tests, frontend and deployment scripts. Earlier installment guides and
manifests are immutable historical snapshots; `RELEASE_11_MANIFEST.json` is authoritative
for this cumulative delivery.

## Delivered code

- `backtesting/contracts.py`: strict frozen manifest/contracts/options; timezone-aware
  timestamps, exact monetary text, fixed reviewed linear contracts, explicit sessions.
- `backtesting/dataset.py`: local-only bounded hash-verified UTF-8 M1/tick CSV and strict
  JSON; no symlinks, URL fetching, duplicate columns/keys, nonfinite/coerced prices,
  backward chronology, hidden future publication, imputation or silently sorted feeds.
- `backtesting/market.py`: distinct `SourceKind.HISTORICAL`, causal quote cursor, original
  tick timestamps, complete availability-aware UTC M1→M5/M15/H1/etc aggregation,
  independent returned frames, signed bid/ask cash conversion and declared margin models.
- `backtesting/broker.py`: existing durable `SimulatedBroker` plus pessimistic OHLC
  carried-position stop/target resolution. Both touched ⇒ SL first. Gap ⇒ adverse
  opening price. Fees/swap/slippage retained. Midnight baseline rolls before the exit.
- `backtesting/news.py`: archived first-seen headlines and known-at calendar revisions;
  fresh, complete coverage only; deterministic existing impact/exposure classifiers;
  high/unknown/tentative events and unknown/stale coverage block, never lower risk.
- `backtesting/reviews.py`: exact bound timestamped archived entry/position reviews,
  delayed response visibility, no provider/network call or hash/confidence refresh.
  Optional explicitly artificial entry reviewer only for synthetic fixture origins.
- `backtesting/backtester.py`: chronological bounded scheduler using the actual
  `SignalEngine`, `ExecutionEngine`, `RiskEngine`, `OrderCalculator`, `PositionManager`
  and `TrailingEngine`, with a NEW isolated SQLite/checkpoint/owner session only.
- `backtesting/metrics.py`: closed cost-inclusive account-currency P&L/PF/expectancy/
  win/loss statistics, cash/credit-adjusted sampled drawdown, close-ordered loss streaks,
  stale sample disclosure, unannualized observed daily return ratio only with ≥20 days.
- `backtesting/artifacts.py`: immutable raw input copies and private machine/human journals.
- `backtesting/promotion.py`: qualifying native-ledger paper/demo exports and fresh
  signed-owner production-stage imports, with literal digest confirmation and ordinary
  StageGate revalidation. No research relabeling, risk reset, model selection, resume
  or live approval.
- CLIs: `scripts.make_backtest_fixture`, `scripts.backtest`, `scripts.stage_report`,
  `scripts.smoke_backtest`. Tests: `tests/test_backtest_*.py` and marked fixture helpers.

## Safety boundaries retained or strengthened

1. HISTORICAL is NOT MT5. Historical signal/execution adapters are BACKTEST-only;
   native StageGate still requires MT5 provenance and actual native account kind.
2. Replay uses a private ManualClock; no native SDK, process, provider, Telegram transport,
   deployment or production financial state is opened by the runner.
3. A new run never reuses, overwrites or resets an existing directory/ledger. Original
   credentials are discarded. An invalid non-Telegram sentinel token satisfies the
   existing internal owner configuration invariant in the private ledger only; it is
   not a real credential/owner authentication and is never used by any transport.
4. No entry reviewer/news archive ⇒ veto, not invented confidence. Six/day is a target;
   no trade is forced. Simulated entries require the explicit `--simulate-orders` flag.
5. The special `replay` review provider is accepted only for HISTORICAL BACKTEST baseline
   scope. Existing `test` reviews remain synthetic-only; native review gates are not relaxed.
6. HISTORICAL entries require an intact generated persisted signal, not a loose confidence
   DTO. Daily/aggregate risk, no averaging, position/spread/RR/freshness, owner pause/kill,
   idempotency and durable reconciliation remain the same core implementation.
7. A denied original intent is not blindly retried after resume. Protective monitoring
   continues while paused/killed. No native/paper/demo stage is automatically inserted.
8. An enabled ML filter is NOT disabled to improve a replay. This input version does not
   supply a causally selected approved BACKTEST registry: veto-only analysis is allowed;
   archive/artificial approval replay is refused. Existing production ML supervision,
   purged/embargoed learning, registry selection/rollback and promotion gates are unchanged.
9. Archived responses are visible at available_at, expire at the original review deadline,
   and do not refresh observation/hash binding. A changed news snapshot or newly unsafe
   calendar window invalidates the pending response before an entry.
10. OHLC entries and protection are restricted to declared bar boundaries. Neither prior
    extrema for a new position nor a mid-bar SL update is applied retroactively. Delayed
    reviews cannot authorize an inferred partial-bar entry. Unknown exact fill time and
    intrabar trailing remain disclosed, not simulated with a favorable guessed path.

## Results, not financial claims

The full working suite passed **1,970 tests in 228.70 s**: **259 additions** to Part 10's
1,711. Ruff lint/format (284 inputs), compilation, pip consistency/exact Linux lock,
eight offline smokes, JS/loader syntax, news inspection and isolated operator CLI passed.
Positive paper/demo export and import CLI tests use expressly fabricated native-tagged
SQL/proof rows and signed fixture bearers, never authentic broker or owner evidence.
The new smoke passed 13 checks, making exactly one artificial simulated entry/close.
Its closed-trade net was **−0.54 USD**. That is an engineered cost/reconciliation check,
not a financial estimate, broker fee attestation or strategy-performance result.
The separate clean extraction also passed **1,970 tests in 234.29 s**, all lint/format/
compile/lock/smoke/JS/news/CLI checks. Results and reused-dependency limitations are
recorded in `VALIDATION.md` and `RELEASE_11_VALIDATION.json`.

No genuine historical dataset, authentic archived AI/news, broker-specific historical
contracts, owner credential, live authorization or deployment permission was supplied.
No actual broker/provider/Telegram request, terminal/task/executable deployment or
real order occurred. No genuine strategy or stage was qualified.

## Read next

- `BACKTESTING.md`: exact input contracts, chronology/cost assumptions and executable CLIs.
- `OPERATOR_PLAYBOOK.md`: backtest→paper→demo→explicit small live, failure/recovery and owner review.
- `DEPLOYMENT_WINDOWS.md`: existing native deployment instructions and unexecuted-platform limits.
- `VALIDATION.md`: executed checks, independently extracted release and residual limitations.

All code remains production-oriented engineering, not native-platform certification,
exhaustive security auditing, profitable-strategy evidence or a guarantee against gaps.
