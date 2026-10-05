# Part 6 — strategies, persisted signals and weighted router

**Release 0.4.0 · cumulative Parts 1–6 · database schema 2 unchanged.**

This installment contains working Python modules, typed review contracts, tests
and a runnable isolated diagnostic. Full literal source/configuration/tests are
in `PART_06.md`; ordinary repository files are authoritative. Historical Parts
1–5 guides/manifests describe their original releases, not current file hashes.

**Still not the assembled autonomous bot.** No strategy backtest, trained model,
provider evaluation, native Windows terminal, real broker order, Telegram/Mini App,
scheduler or unattended deployment was performed. AI/news provider adapters arrive
in Parts 7–8; their absence produces a veto, never a fabricated confidence score.
`MT5Client` still defaults to **DenyAllWrites**. Explicit durable composition is
not live permission; native risk/owner/stage/account/expiry checks remain mandatory.

## Implemented files

| File | Actual responsibility |
|---|---|
| `strategy/base_strategy.py` | Immutable feature/vote/proposal/review/result DTOs; bounded scores/reasons; reviewer protocol, not a permissive implementation |
| `strategy/candle_patterns.py` | Closed-body engulfing, hammer/shooting star, doji/inside-bar geometry; no predictive guarantee |
| `strategy/indicators.py` | Causal pandas/ta EMA20/50/200, RSI14, Wilder ATR14/ADX14/DI, Bollinger20, MACD12/26/9, channels/activity/efficiency |
| `strategy/feature_engine.py` | Bounded finalized histories, 200-bar warmup, higher-timeframe as-of alignment, source/alias/UTC validation and input hashes |
| `strategy/trend_strategy.py` | EMA/DI/ADX trend candidate with M15/H1 corroboration and extension veto |
| `strategy/mean_reversion_strategy.py` | Verified range regime plus closed Bollinger re-entry and reversal candle; never averaging |
| `strategy/breakout_strategy.py` | Previous-only channel breakout, relative tick activity, body/close geometry and higher-trend veto |
| `strategy/momentum_strategy.py` | MACD/RSI/price continuation or separately constrained range reversal |
| `strategy/volatility_filter.py` | Fresh quote/bar, gap/ATR/shock/climax/spread/point-grid/permissions veto independent of scores |
| `strategy/news_filter.py` | Known symbol exposure and fresh headline **and calendar** coverage; no fake RSS/calendar producer |
| `strategy/strategy_router.py` | Immutable owner weights, participation/agreement/score/count thresholds and deterministic tie-to-WAIT |
| `strategy/signal_store.py` | Cross-process locked unique bar publication, immutable input integrity, one-way review, revision revocation and audited errors |
| `strategy/signal_engine.py` | Read-only async analysis/review/multi-symbol service; threaded indicators/SQL; absent/error/timeout provider veto |
| `strategy/signal_execution.py` | Strict original-command decode for durable duplicate retrieval, never authorization to resend |
| `trading/execution.py` | Adds explicit `execute_signal(id)` bridge, preserving original payload/result across duplicate/restart/close |
| `trading/risk_engine.py` | Rechecks generated persisted signal/stop/drift/spread/review proof immediately before sending; single-use signal binding in simulation too |
| `scripts/smoke_signals.py` | Environment-isolated full synthetic publication/review/risk/fill/duplicate/TP/ledger diagnostic |
| `scripts/synthetic_signal_market.py` | **TEST/DIAGNOSTIC ONLY** engineered timeframe fixtures, always synthetic; not aggregation-consistent market history |

No AI or HTTP payload can supply a strategy implementation, set weights, pick an
opposite side, remove a stop, enlarge volume or call `order_send` through this layer.
Internal plugin/protocol objects remain trusted Python code, not a hostile-code sandbox.

## New owner configuration

```dotenv
STRATEGY_WEIGHTS_JSON={"trend":0.30,"mean_reversion":0.25,"breakout":0.20,"momentum":0.25}
STRATEGY_MIN_AGREEING=2
STRATEGY_MIN_COVERAGE=0.50
STRATEGY_MIN_AGREEMENT=0.80
STRATEGY_MIN_VOTE_SCORE=60
STRATEGY_STOP_ATR_MULTIPLIER=1.8
STRATEGY_MAX_ENTRY_DRIFT_ATR=0.5
STRATEGY_MAX_RECENT_GAP_BARS=3
VOLATILITY_MIN_ATR_PERCENT=0.01
VOLATILITY_MAX_ATR_PERCENT=2
VOLATILITY_MAX_ATR_SHOCK=2
VOLATILITY_MAX_BAR_ATR_RATIO=3
VOLATILITY_MAX_SPREAD_ATR_RATIO=0.15
```

All four weights are required, each finite in [0,1], totaling **exactly 1** in
Decimal. Zero explicitly disables a strategy. Duplicate JSON keys, bool/NaN,
unknown strategies, impossible agreement count or inverted volatility bounds
fail configuration validation. Weights/maps are read-only and included in both
safety and mode-independent strategy-policy hashes. No silent normalization,
auto-adaptation or trade-quota tuning occurs. Existing adaptation flags are future
Part 7 capabilities, not a claim these services already change weights automatically.

These are uncalibrated conservative engineering defaults, **not backtested optimal
parameters**. Asset-specific costs/hours/news exposure and actual broker names
still need owner review. High ATR, spreads or unachievable minimum lots mean skip.

## Causal feature contract

1. Read only the requested enabled logical symbol, resolve its explicit native
   alias and verify matching `SymbolInfo`/`Tick`/optional history symbol fields.
2. Input frames must be aware-UTC, chronological, non-overlapping, bounded to
   10,000 incoming rows and finite valid positive OHLC with nonnegative activity.
   Finalized feature windows use the configured lookback (300 default, ≤5000).
3. Require **200 actual finalized bars for each unique timeframe**. No zero-fill,
   backward-fill, synthetic higher-frame substitution or centered windows.
4. Exclude every row with close after the analysis cutoff. Independently supplied
   conservative native `close_time` is respected; nominal open+period is the
   minimum bound. Missing/naive times, overlaps, duplicate columns/times/closes,
   bool/string/complex candle numbers or malformed histories fail closed.
5. **Align M15/H1 to the latest primary M5 CLOSE**, not their opening time or a
   later polling time. A higher bar closing after the chosen M5 close is excluded
   even if it has closed by the time a slow poll finishes. No future context joins.
6. Latest primary age ≤420 seconds by default. Higher coverage cannot be older
   than two of its nominal periods; the last three intervals/durations additionally
   enforce the recent gap threshold. No weekend/market gap interpolation occurs.
   This conservatively delays entries after discontinuous sessions.
7. Channel high/low, volume and ATR-median baselines exclude the candidate bar.
   ATR true range includes previous-close gap distance. Tick volume is relative
   broker activity, **not centralized FX real volume**. Missing volume is not
   invented confirmation; flat RSI is neutral 50, not ta's flat 0/0 →100 convention.
8. Canonical streamed hashes bind the exact normalized input windows, source,
   native symbol and time boundaries. They detect revisions, not provider honesty.

EMA/RSI/ATR are recomputed on the exact configured trailing history window.
Recursive warmup seeding is therefore part of the rule definition; it is not a
proof of an infinite-history EMA. Part 11 must use the **same window/conventions**
in replay, never future rows or arbitrary prefilled indicators.

Patterns are corroboration, not independent predictions. Indicators/strategy
votes are correlated measurements of the same prices. Requiring two different
strategy identities does not establish statistical independence or a calibrated
win probability. Scores are technical heuristics in [0,100], not probabilities.

## The actual weighted router

Discard abstentions, owner-disabled weights and votes below 60. For each side:

```text
mass(side) = Σ(owner_weight × eligible_vote_score)
coverage(side) = Σ(owner_weight of eligible supporting strategies)
agreement(side) = mass(side) / (mass(BUY) + mass(SELL))
base_score(side) = mass(side) / coverage(side)
final_score(side) = base_score(side) × agreement(side)
```

An exact strength tie is WAIT. Otherwise the stronger side must have ≥2 eligible
strategy identities, coverage ≥0.50, agreement ≥0.80 and final score ≥70. Any
failed threshold is WAIT, not another signal selected to reach six trades/day.
Inactive strategies do not quietly get their weight redistributed. Examples:

- Trend 85 at weight .30 plus momentum 80 at .25: coverage .55, agreement 1,
  score ≈82.727273. May become a **pending** technical candidate, not an order.
- Mean reversion 86 at .25 plus range-turn momentum 78 at .25: coverage .50,
  agreement 1, score 82. Requires the complete range/reversal rules to qualify.
- One 99-point trend vote at .30: WAIT; too few identities/coverage.
- Conflicting high-weight opposite votes penalize agreement; ties never choose a
  convenient direction. AI cannot override the computed side or quality veto.

### Candidate regimes

- Trend: primary fast/mid/slow EMA stack; higher close/mid/slow alignment and
  normalized slope; primary ADX≥20 and correct DI. BUY RSI50–74 / SELL26–50,
  bounded distance from EMA20; no chase into an overextended move.
- Mean reversion: ADX≤22/25/30 on primary/higher/trend, efficiency≤.35 and bounded
  higher slope; previous close outside its previous Bollinger band, current close
  back inside, constrained RSI and verified reversal candle.
- Breakout: current **closed** price outside a previous-only 20-bar channel,
  .1–.7 ATR penetration, body fraction≥.5, directional close location≥.75,
  tick activity≥1.25× previous average, ADX≥20 and no opposing higher bias.
- Momentum: six/three-bar price progress, MACD/RSI corroboration and higher bias;
  range reversal instead requires low ADX, RSI/MACD turn and a reversal candle.

Quality filters then independently veto zero/very low/extreme ATR, ATR shock
against the previous median, gap/climax true range, excessive spread relative to
ATR, absolute broker-point spread, stale quote/bar, unsupported side/SL/TP or
recent data gaps. A 99 score cannot override one of those vetoes.

## Structural protection is not lot sizing

An eligible candidate proposes an immutable stop before sizing:

- BUY: below both current executable bid minus ≥1.8 ATR and the recent bid-based
  swing low minus .1 ATR buffer.
- SELL: above executable ask plus ≥1.8 ATR and recent bid-based swing high plus
  current spread and .1 ATR buffer.
- Stop/freeze metadata plus a point, actual tick size and outward directional
  rounding provide a legal candidate. No later SL rewrite to make a vote fit.

Current entry may not drift more than .5 ATR from the chosen finalized close.
Native profit/margin/cost/FX sizing still comes exclusively from `OrderCalculator`
and `RiskEngine`; no lot is rounded up to a broker minimum. The $5 objective can
increase only to satisfy the configured net reward/risk floor, never by increasing
risk to manufacture $5. Fills, prices, profitability and maximum realized loss
remain unguaranteed during gaps/spread changes/disconnections.

## Persisted signal lifecycle

```text
read-only histories + fresh quote → frozen feature/candidate envelope
           ↓ unique mode/native-symbol/timeframe/bar/router/config key
          pending / wait / rejected
           ↓ ONLY a fresh bound AI review + known safe news/calendar
          approved / rejected / expired
           ↓ explicit ExecutionEngine.execute_signal(id), never implicit
 durable intent + owner/risk/stage revalidation → one broker attempt / veto
```

`approved` means **technical/AI/news quality approval**, not owner consent, live
approval or proof the position/risk/stage checks passed. Paused/killed startup
never auto-resumes because a provider or technical rule returns approve.

- Frozen envelope binds format, source, bar times, native alias, quote, input
  history, structural stop, ATR, route votes, full config/policy and code/model.
- A reviewer must approve the **same proposal hash**, source/code/model and news
  digest with fresh bounded confidence. It cannot change side/price/stop/weights.
  `provider=test` is accepted only for synthetic fixtures, never real/native data.
- Unknown/stale headlines OR calendar, no entitled news exposure, missing/invalid/
  timeout/failed AI, veto/WAIT, low confidence or proposal/source mismatch rejects.
  No provider transport or real confidence was invented in this installment.
- AI risk increase is rejected. A smaller risk is accepted only with the explicit
  owner `AUTO_REDUCE_RISK=true`; it never exceeds the configured cap.
- One pending row per bar; its original observation is **not refreshed by polls**.
  It expires after the 30-second review/order window. Rejected/expired decisions
  are not flipped to approved by a later callback for that same bar.
- Repeated reads of unchanged input return the original row/hash. A changed
  consumed history, contract shape, code/model/source revokes the old decision,
  preserves its original features and creates no replacement trade that bar.
  Volatile tick-value/visibility fields are excluded from the stable contract hash.
- Every published/reviewed/wait/veto/duplicate/revision/read/provider error is
  audited with bounded internal codes; no raw provider body/credential is logged.
  A data failure before a known finalized bar creates an audit, **not a fabricated
  Signal time/bar**, and returns a blocked result.

Signal payloads contain stored context/review proof and prices, not broker login,
password, owner token, authorization header or provider key. Their hashes detect
accidental integrity changes, not tampering by a local DB/code administrator.
Authenticated owner transport/rate limits are Part 9; do not expose these internal
methods as unauthenticated public endpoints or trust an HTTP `owner_id` field.

## Explicit signal-to-execution bridge

`ExecutionEngine.execute_signal(signal_id)` loads/validates the stored final
context. It then obtains fresh prices, checks drift, calls the native calculator
and stages the usual durable authority transaction. Native approval still needs
backtest→real-data-paper→broker-demo evidence, actual account type/identity and
expiring owner live confirmation where applicable. No flags replace these gates.

The account/mode/config/code/model/signal-bound entry key is deterministic. If an
intent already exists, the bridge decodes **its original order/target/context**,
not a newly priced/sized payload, before requesting the cached result. Therefore:

- Concurrent repeats send once; restart, changed quote, revoked signal or closed
  position cannot make a duplicate reopen or change the original payload.
- A paused, definitely-unsent veto is cached REJECTED. Resume does not replay it;
  wait for a new genuine closed-bar opportunity instead of forcing a daily quota.
- Prepared/submitting/unknown with no definitive result remains uncertain, halted
  and never blindly resubmitted. Reconnect/expiry/empty history are not no-fill proof.
- Broker/SQL uncertainty remains governed by Part 5's reservation/quarantine rules.
- Generated Signal/stop/drift/spread-to-ATR/context and **review timestamp** are
  independently checked again by risk immediately pre-send. Signal revocation
  after a grant can still veto before mutation. A request already in flight may
  still fill; there is no atomic lock against a later broker/manual race.
- Below-minimum sizing returns no order. Terminal protective work still uses
  known ownership and remains available while entries are paused/killed.

## Service usage

Initialize your chosen market/broker **explicitly** first; signal initialization
never logs in or starts a terminal implicitly. For trusted application composition:

```python
from strategy.signal_engine import SignalEngine

signals = SignalEngine(
    execution.broker,
    execution.database,
    execution.settings,
    profile=execution.profile,
)
await signals.initialize()

# Actual provider adapters are Parts 7–8. With no reviews, this rejects.
result = await signals.evaluate("EURUSD")
assert not result.approved

# analyze() returns a pending proposal for the future supervisor; finalize()
# requires its typed bound review and NewsWindow. It never auto-resumes/executes.
# After finalization, an explicitly invoked execute_signal(id) still faces
# durable owner, risk, portfolio and promotion gates.
```

This composition is a service API, not an installed `main.py run` loop. Indicator
and SQL operations run through `asyncio.to_thread`; native market calls retain
MT5's one-worker serialized contract. Provider work is awaited with bounded
concurrency and timeout; polling/watchdog scheduling remains Part 10.

## Runnable synthetic diagnostic

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_signals
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
.venv/bin/python -m scripts.smoke_signals
.venv/bin/python -m pytest -q
```

The complete `smoke_signals.py` is a runnable example, not an abbreviated template.
It ignores host mode/credentials/.env, uses a temporary DB/checkpoint, imports no
native SDK and makes no external call. It proves missing reviews veto an EURUSD
candidate, a separate GBPUSD candidate gets a **scripted synthetic** review without
auto-resume, one explicit simulated entry is sent despite duplicates, an engineered
TP exits, every fee leg/balance reconciles and a duplicate cannot reopen it.

Observed artificial example: technical score 83.328045, coverage .55, agreement 1,
0.02 lot, SL1.09814, original objective 5.236 USD and simulated net 5.56 account
units. These histories are independently engineered timeframes, **not coherent
aggregation/historical replay**. Scripted 90 AI confidence is not a model/provider
assessment. This is accounting/causality/idempotency regression evidence only,
**NOT a backtest, profit prediction, promotion evidence or trading permission**.

## Compatibility / remaining work

Schema remains 2: no Part 6 DDL migration. New policy fields and runnable code
change hashes. Existing promotion/live approvals are invalid; old paper
checkpoints may refuse changed config. Do not replace the DB/reset capital or
manually rebind a checksum to manufacture a new stage. Keep matched old release,
config, DB and checkpoint; review/reconcile it with its matching release first.
See `MIGRATIONS.md` for the explicit boundary and preserved latch rules.

Part 7 supplies AI routing, structured provider validation and learning/model
registry. Part 8 supplies real entitled news/calendar producers. Backtesting,
walk-forward/evaluation reports, authenticated owner UI and the scheduler/deployment
are later installments. This release's heuristic parameters have no market
performance validation. Keep paper/mock/paused defaults and diagnostics only now.
