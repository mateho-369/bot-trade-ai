# Historical replay — executable guide

**0.12.0 / schema 2. Offline research by default; no automatic promotion or real orders.**

## 1. Engineering smoke without credentials

From the repository root with the pinned dependencies installed:

```bash
python -m scripts.smoke_backtest
python -m scripts.make_backtest_fixture --output data/fixtures/replay_001 --warmup-minutes 12240 --replay-minutes 2
python -m scripts.backtest --manifest data/fixtures/replay_001/manifest.json --output data/backtests/read_only_001
```

All fixture prices, quotes, volumes, contracts and empty-news coverage are artificial.
Default review mode is `veto`, simulated execution is paused, and a valid result can
contain **zero trades**. Do not lower quality or risk limits to reach a daily quota.

Explicitly exercise the **private simulated** entry/close path, not a broker:

```bash
python -m scripts.backtest --manifest data/fixtures/replay_001/manifest.json --output data/backtests/simulated_001 --review-mode synthetic_research --simulate-orders --close-at-end
```

The artificial reviewer is labeled `replay`/`artificial-fixture-not-ai`, restricted to
`origin.kind=synthetic_fixture`, and never qualifies a stage. It is not an Ollama/OpenAI
fallback and cannot be used on imported historical origins.

A separate OHLC fixture:

```bash
python -m scripts.make_backtest_fixture --output data/fixtures/ohlc_001 --quote-mode ohlc_conservative
python -m scripts.backtest --manifest data/fixtures/ohlc_001/manifest.json --output data/backtests/ohlc_001 --review-mode synthetic_research --simulate-orders --close-at-end
```

Choose a **new** output every time. Existing directory ⇒ refusal, never capital/history
reset. No process/server/Telegram/native terminal is started by these commands.

## 2. Import your licensed historical data

No downloader, exchange/broker entitlement or authentic market dataset is bundled.
Obtain/export data lawfully, document its provenance, and create a `reflex-history-v1`
manifest pointing only at relative local files. Do not describe generated data as real.
`BACKTEST_CONTRACT_SCHEMAS.json` contains generated input schemas; the runtime additionally
checks cross-field/grid/chronology bounds. The file structure is:

| Manifest field | Meaning |
|---|---|
| `format` | Exactly `reflex-history-v1` |
| `origin.kind` | `historical_import` or `synthetic_fixture`; declaration, not machine attestation |
| `origin.description/provider/acquired_at/license_note` | Source/acquisition/license record; aware acquired_at |
| `quote_mode` | `ticks` or `ohlc_conservative`, never mixed |
| `account_currency` | Exact currency code; must match research Settings, never silent USD/USDC/cent substitution |
| `replay_from/replay_until` | Completed aware interval, end later than start |
| `symbols` | 1–40 distinct logical/native fixed contracts, including required conversion legs |
| `bars` | Exactly one `{symbol,path,sha256}` M1 CSV per contract |
| `ticks` | Exactly one equivalent tick file per contract in tick mode; absent in OHLC mode |
| `sessions` | Ordered nonoverlapping minute-aligned `{symbol,start,end}` half-open market intervals |
| `news/reviews` | Optional `{path,sha256}` archived JSON; absence vetoes, not approval |
| `model` | Optional SINGLE frozen `reflex-replay-model-selection-v1`; exact SHA-bound artifact/corpus and aware pre-replay times |

Every SHA is lowercase SHA-256 of the **exact raw file bytes**:

```powershell
(Get-FileHash data\historical\EURUSD_M1.csv -Algorithm SHA256).Hash.ToLowerInvariant()
```

Paths cannot be URLs, absolute, drive-prefixed, backslashed or traversing. Symlinked
components are rejected. Limits: manifest ≤1 MiB, each input ≤64 MiB, total raw inputs
≤128 MiB, combined bar/tick rows ≤500,000, archive JSON ≤4 MiB with bounded nesting,
arrays and nodes. Parsed document/coverage access returns independent copies. The
loaded prices/bytes are read-only; no subsequent file modification revises the run.

### Fixed symbol contract

Each contract includes `logical_symbol`, native `name`, `point`, `tick_size`,
`tick_value_profit/loss` (optional/default zero), `contract_size`,
`volume_min/max/step`, integer `digits`, `currency_base/profit`, integer
`stops_level/freeze_level/trade_mode`, aware `effective_from` and `description`.
Financial JSON fields are exact decimal **strings**, never booleans/floats/exponents.
Prices must align with tick_size; metadata must obey the actual SymbolInfo invariants.

Only `profit_model=linear_contract` is supported:

```
gross in currency_profit = direction × (exit − entry) × contract_size × volume
```

Currency conversion is the existing signed bid/ask funding/proceeds implementation,
using explicitly configured `ACCOUNT_TO_USD_SYMBOLS_JSON` and observed fresh quote
legs. There is no unknown FX rate, tick-value shortcut or dollar relabel fallback.

`margin_model=notional`: price × contract_size × volume / configured leverage, in
currency_profit, then conservative funding conversion to account currency.
`margin_model=forex_base`: contract_size × volume / leverage in currency_base,
then the same verified conversion. These are declared simulation approximations;
not a proof of historical broker margin, tiers or exact CFD/futures/inverse contracts.
Unsupported nonlinear contracts and historical contract changes need a different
reviewed adapter, not guessed coefficients. effective_from must cover every input.

Logical/native aliases must agree with configured aliases. Only the configured
trading symbols present in the dataset are enabled; extra conversion contracts can
supply FX quotes but are not silently enabled for trading. Missing conversion ⇒ veto/failure.
The default USD EURUSD fixture needs no currency conversion mapping. A EUR account or
forex_base EUR margin requires explicit `{"EUR":"EURUSD"}` and that quote in the tape.

### M1 CSV — exact header/order

```csv
time,open_available_at,available_at,open,high,low,close,tick_volume,spread_open_points,spread_max_points,real_volume
```

`time`: aware minute-aligned opening; `open_available_at`: actual/declarable opening
observation; `available_at`: full final OHLC availability, never earlier than nominal
close (`time+1 minute`). In tick mode delayed publication is allowed in nondecreasing
order. In OHLC mode both `open_available_at=time` and `available_at=time+1 minute`
are required; otherwise the assumed boundary-price model would be unsupported.
High/low/close are not exposed at open_available_at.

OHLC is **bid based**. Tick volume is nonnegative exact integer text. Spread is
nonnegative decimal point counts with max≥open. OHLC sell ranges use **maximum spread**
for conservative ask SL/TP tests and maximum spread for the closing mark. Do not pass
pips or an average spread while claiming it is the actual maximum. Bid OHLC range,
positive prices, real_volume and chronology are validated; missing minutes are counted,
not forward-filled. Declared sessions are themselves unverified source information.

### Tick CSV — exact header/order

```csv
time,available_at,bid,ask
```

Both times aware; quote time≤availability; strictly increasing quote and availability
per symbol; finite positive grid prices; ask≥bid; session membership. Equal-availability
multiple ticks are rejected rather than dropping an earlier stop touch. For multiple
symbols a same-availability batch updates all conversion legs before valuation.
Original quote time is retained at every read, so stale/delayed ticks cannot be restamped
fresh. Tick completeness and price authenticity are not independently proved by CSV.

### Warmup and higher timeframes

Only available finalized M1 observations participate in returned data. Every higher
frame requires all N consecutive minute openings at a UTC-aligned boundary. Its
available_at is the maximum constituent availability; close_time is the nominal bound.
No partial/current H1, missing-minute aggregate or future revision is exposed.
Each requested strategy timeframe still needs ≥200 finalized observations. Defaults
M5/M15/H1 need at least 200 H1 windows; the fixture supplies 204. Loading a shorter
history is allowed, but core feature quality vetoes entries. UTC aggregation is not
an attestation of a broker's custom server-time D1/session bar convention.

## 3. Archived news/calendar, not hindsight

`reflex-replay-news-v1` contains ordered `snapshots` (≤4,096). Each snapshot supplies:

- available_at, original headlines_fetched_at and calendar_fetched_at;
- covered_from/covered_until, explicit actual-bool complete, logical symbols;
- bounded headlines: title/summary, published_at≤first_seen_at≤snapshot availability,
  currencies/instruments, language (`en` or unsupported), impact (low/medium/high/unknown);
- bounded events: event_id, known_at≤snapshot availability, scheduled_at, currency,
  impact and actual-bool precise_time.

Future **scheduled** events are legal if already known. Future **revisions/headlines/polls**
are not. Pick the latest available snapshot at the replay clock, without refreshing old
poll/first-seen times. Complete scope and ordinary news/calendar TTLs remain mandatory.
High/unknown events block default −30/+15 minutes; tentative times block their coverage.
Existing headline sentiment/impact/exposure mapping may raise risk but never grant entry
or lower a high/unknown impact. No archive ⇒ UNKNOWN. An empty complete fixture snapshot
is expressly artificial, not an authentic "no news" claim.

## 4. Archived AI reviews and latency

`reflex-replay-reviews-v1`: description, ordered unique `entries` (≤20,000), optional
ordered unique `position_entries` (≤20,000). Entry rows include:

- available_at and original observed_at≤available_at;
- **exact proposal_hash/news_hash/code_hash/model_sha256/request_hash**;
- approve/reject/wait, actual finite numeric confidence 0–100;
- optional decimal-text risk_percent (reduction only under core policy);
- original_provider (`ollama`/`openai`) and safe provider_model identifier.

The original provider declaration is not independently authenticated; no external
provider is called to turn a backtest into a green decision. Replay DTO provider is
`replay`, never a claim that Ollama/OpenAI was called by this runner. A changed input,
news snapshot, code/model, stale review, low confidence or escalation vetoes.

Use `signals.jsonl` for exact context diagnostics. Filling a missing archive after
seeing outcomes, generating convenient confidence, remapping an unrelated native
proposal hash, editing observed_at, or using a future-trained provider/model while
claiming contemporaneous capture is NOT valid qualification. An exact archived reply
must have existed at its declared clock time; human/vendor provenance requires review.
Native MT5 signal hashes are not silently rebound to HISTORICAL proposal hashes.

In tick mode a pending reply is revealed at available_at and finalized at that event
only if its original news/context is still valid, before the original AI/order deadline.
Missing replies expire, not regenerate. Changed news invalidates pending green context.
OHLC entries can occur only at a declared next bar open; a late reply cannot create a
favorable inferred partial-bar fill or refresh the same signal to wait indefinitely.

Position rows additionally bind logical_symbol, positive position_identifier,
position_hash, code/model/news, confidence, momentum_continues/volatility_safe and
original_provider. The shared PositionManager/TrailingEngine's optional 120% extension
still requires that exact current ownership/hash, freshness, momentum/volatility/news
and configured cap. No generic/artificial position approval is manufactured. ATR and
profit-lock protection use finalized available candles and only improve protection.

**ML filtering (Part 13):** enabled approval replay still refuses without a causal
bound model. Optional `manifest.model` now declares ONE immutable pre-replay artifact
and complete corpus. Schema/source/code/policy/labels/selection/embargo/age are checked,
purged evaluation and exact trainer bytes reconstructed BEFORE run creation, and a
snapshot imported into ONLY a new private BACKTEST ledger. Actual inference on the
fixed vector is required at approval AND execution revalidation. No production model
is read/activated, no owner activation is faked, and no intrarun fitting/switching or
probability substitution occurs. No model + enabled ML permits veto-only analysis;
model + disabled ML refuses rather than changing the setting. See `ML_REPLAY.md` for
bounds, exact contract, artificial example and independent provenance limitations.

## 5. Chronological engine and explicit execution

```bash
python -m scripts.backtest --manifest data/historical/manifest.json --output data/backtests/history_001 --env-file research.env --review-mode archive --simulate-orders --max-events 500000
```

An explicit environment must exist and be separately reviewed. A live-enabled config
is refused. Secrets are scrubbed; no production model/news/checkpoint/DB is copied.
The original data currency, enabled symbols/aliases and risk/strategy policy remain
binding; effective source/config/code/model hashes are recorded. No native SDK import,
Ollama/OpenAI/news HTTP call, bot/API/watchdog/server or owner-production resume occurs.
Without --env-file the repository .env is not read (inherited environment still obeys
normal Settings validation, including refusing live). Use a clean shell for research.

The simulated scheduler uses original feed/snapshot/reply availability plus bounded
position/heartbeat/signal jobs and exact review deadlines. The full union is capped
before any output ledger is created. Tick/gap/server stops are processed before new
protection/entries. OHLC closed-bar exits precede new opening quotes and protection;
only carried positions see that bar's range. OHLC signal/protection jobs run at declared
boundaries even with fractional replay starts; no arbitrary intra-bar path is inferred.

Everything passes through shared persistent ownership/intents/risk/control/leases.
A rejected/unknown original intent is not blindly reissued after a later resume. Paused/
killed protection continues; maximum 12/day and one-position-per-symbol/no averaging
remain caps, not targets. Minimum 6/day is never enforced. Progress tiers 30/60/90 are
achievable net-stop estimates, not guaranteed cash, and may be infeasible due to spread,
stop/freeze levels, tick grids and costs. No martingale/grid/doubling/risk escalation.

## 6. Costs and limitations

- Entry bid/ask side, paper adverse slippage and executable exit side are used.
- SL gaps fill at worse observed market (OHLC adverse open if crossed); TP gaps do not
  gift a better target fill. Both OHLC levels touched ⇒ stop first.
- Round-turn commission USD/lot is split entry/exit and conservatively converted.
- Swap uses configured USD/lot/day × elapsed holding time; midnight baseline rolls
  **before** marking/exiting the gap. It is not a triple-swap/broker rollover history model.
- Risk sizing uses conservative max-slippage valuation and net RR, not actual favorable fills.
- Static leverage/margin/linear contracts are assumptions. No liquidity queues, market
  impact, rejection/latency distribution, requote prediction, variable funding, margin
  liquidation, broker commission tiers or contract-change reconstruction is proved.
- OHLC does not know exact fill timestamps or intrabar trailing/drawdown. Close/open
  equity samples can understate intratick extremes; the report explicitly says sampled.
- End positions stay open/marked by default. `--close-at-end` explicitly attempts core
  owned closes at the last executable quote; stale/FX/ownership gates may leave residue.
- Account P&L is never mislabeled USD for non-USD accounts. No historical realized FX
  conversion is manufactured. Closed PF excludes marked open gains, includes trade costs,
  and is NULL/undefined rather than infinity when there are no losses.

## 7. Output files and interpretation

- `run.json`: frozen options/effective public policy/code/model/source hashes, input manifest name.
- `inputs/`: exact original hash-verified raw files, no live re-read.
- `data/replay.db`: private persistent full core SQL decisions/intents/trades/audits/risk history.
- `data/paper/state.json`: matching durable simulated checkpoint, not native state.
- `signals.jsonl`: analysis/finalization inputs, decisions, availability and bindings.
- `operations.jsonl`, `ohlc_resolutions.jsonl`: execution/protection/veto/stop-first decisions.
- `equity.jsonl`, `trades.jsonl`: exact account financial samples and trade outcomes.
- `report.json`, `report.md`: cost-inclusive metrics, assumptions, stale/gap/undefined fields.
- `completion.json`: written only after successful engine shutdown/release.
- `failure.json`: incomplete run; do not promote or pretend it finished.

The dataset digest binds raw manifest and declared file digests/lengths; it is not a
signature or vendor entitlement/provenance proof. Header origin descriptions cannot
make artificial prices authentic. All replay outputs set promotion_eligible=false.
**`reflex-backtest-v1` is not `reflex-stage-v1`**. See the operator playbook for separate
independent provenance review, original strategy/model/config bindings, authentic
paper/demo ledgers, signed-owner digest confirmation and explicit small-live permission.


## 8. Part 14 completed research closure / read-only audit

Each NEW completed current run publishes `bundle.json` AFTER engine/market shutdown
and private database close. Complete exact input/output/checkpoint/model/SQLite file
closure and raw SHA/lengths are recorded; failure preserves artifacts with incomplete
status. Inspection: `python -B -m scripts.audit_backtest --run <completed-private-output>`.
No production DB/owner/model is opened or changed, no original SQL connection or WAL
replay/checkpoint/reset, no fitting/trading/stage promotion. Bounds/metric/decision/
archived-review/news/model/memory-SQL semantics and limitations are in REPLAY_AUDIT.md.
Old unsealed outputs are refused, not automatically repaired/resealed. All research
remains promotion-ineligible even if internally consistent. No native facts are certified.
