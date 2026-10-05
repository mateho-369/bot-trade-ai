# Part 8 — news, sentiment, complete-range calendar and expiring entry proof

**0.6.0 · cumulative Parts 1–8 · schema 2 retained · 2026-10-03.**

This installment supplies real Python adapters/services, config, fixtures and tests,
not an autonomous daemon or production-validated trading system. The assembled
Telegram/Mini App, runtime scheduler/watchdog and historical backtester arrive in
Parts 9–11. No credentials were supplied, no entitled provider was called, and no
native Windows MT5 order/deployment/live permission occurred. Tests use local
`httpx.MockTransport`, synthetic clocks/headlines/calendar and shadow execution.
Public official documentation was consulted; browsing docs is not API validation.

## Actual modules

| File | Executable responsibility |
|---|---|
| `news/types.py` | Immutable bounded headline/batch/calendar/event DTOs; strict JSON/UTC; plain text; display-only sanitized links |
| `news/http_client.py` | Async bounded GET, TLS, no redirect/proxy/retry; streaming size/media/encoding/age checks; query-token log filter |
| `news/rss_parser.py` | Defused XML preflight + actual feedparser RSS/Atom; explicit original publication time; conditional RSS ETag/304 |
| `news/providers.py` | Fixed NewsAPI, Finnhub and CryptoPanic v2 HTTP endpoints; header auth where supported; bounded integer pagination |
| `news/economic_calendar.py` | Reviewed local normalized JSON, configured JSON HTTPS and explicitly reviewed premium Finnhub calendar |
| `news/sentiment_analyzer.py` | Bounded English finance lexicon/negation; deterministic high/medium impact; unsupported language is not low risk |
| `news/exposure.py` | Configured logical currency exposures and title/ticker mapping; aliases never guessed; conservative unknown/global risk |
| `news/news_cache.py` | SQL dedup/conservative risk updates, confined immutable snapshots, append-only publication epochs and poll CAS |
| `news/evidence.py` | Per-symbol coverage projection, expiration/next-event boundary, latest committed journal verification |
| `news/alerts.py` | Sanitized, deduplicated pending owner notification outbox/TTL; acknowledged only by trusted owner sender |
| `news/news_manager.py` | Explicit async initialization/refresh/read/close, throttling, bounded concurrency, cancellation and optional advisory AI |
| `news/__init__.py` | Import-only package; no network/SDK/runtime side effects |

`core/settings.py`/`.env.example` add validated source review, bounds, entitlement
mode and calendar controls. `NewsWindow` now binds logical symbol, policy/code,
market provenance, immutable snapshot SHA, publication epoch, calendar start,
expiry and fixture flag. Signal review/finalization, risk authorization and final
pre-send revalidate managed evidence. Native-data entries and TP extensions cannot
use legacy unbound "known/safe" booleans. Existing synthetic/test-SDK diagnostic
DTOs remain supported only for those diagnostic markets, not MT5 authorization.

## Safe defaults and assumptions

- Paper/mock, demo flag true, live false, startup paused remain unchanged.
- All three licensed APIs default **disabled**; keys alone enable no HTTP.
- RSS URLs/policies are empty. Local calendar review and reviewed empty-calendar
  allowance default false. There is no fabricated usable sample feed/calendar.
- `NewsManager` never resumes/pauses owner state, resets counters/capital, places
  orders, grants stage/live permission, fetches an article URL or sends Telegram.
- Unknown, empty headline feeds, missing dates, stale/quiet feeds, delayed access,
  malformed/partial/oversized responses, quota/timeout/error envelopes, bad XML,
  incomplete currency/interval calendar coverage and an in-progress refresh all
  withhold safe new-entry proof. Headline and calendar requirements are independent.
- Header/file reread/304 does not rewrite original article publication, first-seen
  or calendar provider-issued time. Calendar age is measured from **produced_at**.
- Native-data paper/demo/live require current managed non-fixture news proof, even
  if someone disables the old entry news flag in a paper configuration. Disabling
  the producer's high-impact blocking policy never manufactures safe coverage.
- News blocks new entries/optional target extension, **never improved SL or closure**.
  Paused/killed protective management still works; fills/loss limits are not guaranteed.

## Provider contracts and entitlement caveats

| Adapter | Request/contract | Eligibility caveat |
|---|---|---|
| NewsAPI | `GET https://newsapi.org/v2/everything`, `X-Api-Key`, language en, bounded page/pageSize/date/query | `development` mode is diagnostic only. Owner must verify a production-entitled real-time plan before `production` + reviewed scope. Developer plan is delayed and development/testing-only. |
| Finnhub news | `GET https://finnhub.io/api/v1/news`, `X-Finnhub-Token`, configured general/forex/crypto categories, UTC integer epoch | Category/entitlement/coverage must be reviewed; a rolling response is not exhaustive world-news proof. |
| CryptoPanic | `GET https://cryptopanic.com/api/<plan>/v2/posts/`, mandated `auth_token` query, public/news/en/currencies and bounded integer page | Current API is plan-dependent; developer is diagnostic. Growth/Enterprise still require operator-verified entitlement/scope. v2 `instruments[].code` is parsed. Unsupported enterprise `size` is not sent. |
| RSS/Atom | Fixed owner-configured public HTTPS URL, original published date, bounded defused XML, ETag/Last-Modified | Syndication delay/sparse cadence/entitlement/source scope require review. Atom **updated alone is not original publication** and is rejected. |
| Finnhub calendar | Fixed `/api/v1/calendar/economic?from=&to=`, explicit premium scope review, configured provider timezone | Not assumed free. Unknown countries/impact/time or ambiguous/nonexistent DST times reject coverage; local date boundaries are checked too. |

NewsAPI/CryptoPanic plan labels and `reviewed/realtime` booleans are operator
**declarations**, not independent license/source/authenticity attestation. No
rolling API or RSS response proves all relevant breaking news was published.
Absent SLA/updates/data truth remains operational risk. No free feed is silently
pre-authorized as complete production coverage. Do not claim these tests validated
provider entitlement, genuine latency, country mappings or broker performance.

Official references consulted on 2026-10-03:

- [1](https://newsapi.org/docs/authentication) — header auth avoids URL-key logs.
- [2](https://newsapi.org/docs/endpoints) — supported v2 endpoints.
- [3](https://newsapi.org/pricing) — Developer delay/dev-only vs licensed production tiers.
- [4](https://finnhub.io/docs/api/market-news) — header/token auth and market-news schema.
- [5](https://finnhub.io/docs/api/economic-calendar) — premium economic-calendar access.
- [6](https://cryptopanic.com/developers/api/) — current v2 posts, instruments, integer pagination and plan-specific options.
- [7](https://cryptopanic.com/developers/api/about) — plan-dependent access/cache/limits; verify current account offerings yourself.

No source key, token-bearing next URL, provider error body or raw response is stored
in logs/audits/cache. CryptoPanic necessarily sends a token in its request query;
our httpx URL logger is redacted, but remote-server/proxy/APM logging is outside
this guarantee. Inspect/disable external request tracing. Known-secret redaction
is not a guarantee against novel/encoded credentials in arbitrary upstream text.

## Configuration and source review

Run identifiers only, with **zero HTTP/broker calls**:

```powershell
.\.venv\Scripts\python.exe -m scripts.inspect_news_sources
```

```bash
.venv/bin/python -m scripts.inspect_news_sources
```

RSS source IDs are `rss-` + first 16 SHA256 characters of the literal configured
URL. API IDs are `newsapi`, `finnhub:forex`, `finnhub:general`, `finnhub:crypto`,
`cryptopanic`. Inspect first, then review the actual entitled source. Example
**policy shape**, not a recommendation or proof for a real source:

```text
NEWS_SOURCE_COVERAGE_JSON={"finnhub:forex":{"symbols":["EURUSD","GBPUSD"],"currencies":["USD","EUR","GBP"],"reviewed":false,"realtime":false,"language":"en","poll_seconds":180,"max_publication_lag_seconds":3600}}
```

Only an owner-reviewed source with explicit enabled logical symbols/currencies,
real-time declaration and eligible access mode can contribute proof. Coverage must
include **all configured exposures** for the symbol, e.g. EUR **and** USD for EURUSD.
Unsupported language is diagnostic/unknown. Sparse or old feeds become unknown
once latest distinct original publication exceeds the reviewed cadence deadline.
Do not set realtime/reviewed just to get a green dashboard.

Empty `NEWS_REQUIRED_SOURCE_IDS_JSON` means all configured feeds relevant to that
symbol are mandatory; unscoped configured sources are conservatively unknown.
An explicit nonempty list selects mandatory feeds, but cannot certify absent IDs,
missing currency scopes or partial source replies. All observed severe stories can
veto, including those from optional sources. Multiple sources do not remove risks.

Defaults: poll 180s, headline fetch age 900s, calendar issue age 21600s, pre-event
30m/post-event 15m, severe headline hold 60m, response timeout 8s, whole refresh 30s,
3 concurrent fetches, at least 1s request spacing, 1 MiB HTTP/snapshot bounds,
200 items/source, 500 total stories, 3 bounded pages, at most 16 configured sources.
Adjust polling/quota bounds only after checking your actual license and cadence;
default polling is not a promise to fit a particular free/freemium quota.

Configured RSS/calendar origins must be public HTTPS, without credentials,
credential-query keys, redirects, localhost/private literal IPs or local host
suffixes. DNS is not pinned/rebinding-hardened. Owner configuration is trusted;
use deployment DNS/egress policy against private destinations. Never expose a
user-supplied URL fetch endpoint or enable untrusted plugins. Display article
URLs are sanitized and **never fetched**.

## Independent normalized calendar format

The safest adapter is an authorized normalized JSON snapshot, not a scraped HTML
page, headlines array or premium endpoint assumed free. Fields are exact:

```json
{
  "format": "reflex-calendar-v1",
  "source": "EXPLICIT SYNTHETIC FORMAT FIXTURE",
  "origin": "fixture",
  "produced_at": "2026-10-03T12:00:00Z",
  "covered_from": "2026-10-02T12:00:00Z",
  "covered_until": "2026-10-05T12:00:00Z",
  "currencies": ["USD", "EUR", "GBP"],
  "complete": true,
  "events": [
    {
      "id": "synthetic-example-only",
      "title": "SYNTHETIC FORMAT EVENT — NOT REAL ECONOMIC DATA",
      "currency": "USD",
      "starts_at": "2026-10-03T14:00:00Z",
      "ends_at": "2026-10-03T14:00:00Z",
      "impact": "high",
      "tentative": false
    }
  ]
}
```

This example's `origin=fixture` makes it **ineligible for native-data trading**.
Do not relabel it. Actual provider/owner-reviewed snapshots require truthful
complete range/currencies, UTC offsets, issued time and observed availability.
`produced_at` never becomes file mtime/poll time/304 time. Local files require
`CALENDAR_FILE_REVIEWED=true`; default path `data/news/calendar.json` is deliberately
not seeded. Files must be confined regular nonsymlink files; source content SHA
is rechecked against the current file at projection and final risk gates.

Calendar coverage must start at least post-window lookbehind before now and reach
at least the pre-window horizon beyond now. Green proof expires at covered_until
**minus pre-window**, not at the end of an uncovered upcoming interval. Unknown
impact/tentative events are severe; an all-day/tentative source must supply an
honest bounded entire uncertainty interval in starts_at/ends_at. No missing time
is skipped as harmless. Reviewed empty complete calendars are possible only with
`CALENDAR_ALLOW_EMPTY_REVIEWED=true`; an unmarked empty array is never evidence.

## Dedup, causality, risk updates and expiry

- Canonical title + original publication UTC day identifies syndicated stories;
  tracking/query credentials/source changes do not create fresh publication.
  Different-title syndication may remain separate (conservative extra blocking).
- SQL `news.time` is earliest original publication; `news.fetched_at` is earliest
  observed first-seen/availability, **not last poll**. Impact can only worsen in a
  duplicate, symbol exposure is conservatively unioned, and timestamps never move
  forward to manufacture fresh data. Poll freshness is separate in snapshot sources.
- A genuinely new severe content/exposure context gets its own append-only
  `news.story_risk_observed` time. First-seen/publication remain unchanged. Repeated
  identical content/context does not refresh this risk hold, including syndication.
  Delayed first discovery/new severity can therefore block even if published earlier.
- Recently discovered risk remains in a new snapshot when it drops out of a rolling
  feed. Expiry/quiet source still withhold coverage; old headlines never certify it.
- Sentiment [-1,1] is a deterministic advisory lexicon score, not directional trade
  advice, probability or risk authority. Positive/AI opinion cannot reduce impact,
  certify missing coverage or override a deterministic block. Optional explicit
  `advisory_sentiment(supervisor)` is separate from collection; no automatic LLM calls.

Snapshots are canonical immutable JSON at `data/news/snapshots/<sha256>.json`.
SQL append-only `news.snapshot_published` commits the current scoped epoch; a file
without that committed journal can grant no permission. Scope binds code, policy
and market source. SHA is integrity, **not authenticity or trading permission**.

Initialization and shutdown publish unknown. Refresh begins by durably revoking
green to an unknown refresh claim before any HTTP. Completion uses compare-and-swap
on that claim; late/cancelled/concurrent results cannot replace a newer epoch.
Failed/cancelled refresh does not restore old green. SQL/file threads cannot be
killed; cancellation waits for any commit then revokes only its resulting current
epoch. If storage fails, missing/corrupt/bounded files cannot prove safety.

Per-symbol `NewsWindow` hashes bind exact snapshot/epoch, logical symbol,
code/settings/market source and expiry. Expiry is the earliest headline age/cadence,
calendar issue/horizon or **next future high-risk pre-event start**. Crossing that
boundary invalidates old green without waiting for the next poll. Every new
publication epoch invalidates pending old proof, even with unchanged content.
This can conservatively skip a signal; reviews never rebind immutable approved
signals or force trades to meet the daily target.

Signal review/finalization and authorization/final pre-send requery the latest
journal inside durable entry transactions; managed TP review is rechecked too.
Newly committed breaking news therefore revokes previous green. The service cannot
see an upstream story before receipt or guarantee atomic remote feed/broker timing:
poll latency, unavailable providers, market gaps and in-flight SDK orders remain
risks. A veto cannot recall a broker order already submitted.

Snapshots are not automatically pruned/rebound: default 50000-file storage bound
halts proof creation when exceeded. Preserve snapshots/audit with DB/checkpoint
backups for investigation/historical causality; implement reviewed archive/retention
operations before long deployment. Do not delete referenced snapshots to manufacture
fresh state. This is a cache/audit source, not a complete historical market backtest.

## Explicit library composition

```python
# Existing trusted broker/execution runtime is already initialized, still paused.
from news.news_manager import NewsManager

collector = NewsManager(database, settings, clock, execution.profile)
await collector.initialize()  # unknown; no HTTP until refresh
await collector.refresh()  # explicit entitled read-only HTTP/file poll
windows = await collector.windows()  # bound logical symbols, no silent HTTP
reviewed = await signals.evaluate_many(reviewer=supervisor, news_by_symbol=windows)
# No automatic execute/resume here. Owner/risk/stage/account checks remain mandatory.
# PositionManager protective SL/close work is independent of new-entry news blocks.
await collector.close()  # publishes unknown, closes HTTP resources
```

These are real APIs, but this fragment assumes already constructed trusted services;
the shared authenticated lifecycle/scheduler is not supplied until Parts 9–10.
Use one collector per DB/scope in runtime composition; competing poll claims fail
closed and late replies cannot overwrite each other. Do not run it unattended yet.

## Offline usage and validation boundaries

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.smoke_news
.\.venv\Scripts\python.exe -m scripts.inspect_news_sources
```

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.smoke_news
.venv/bin/python -m scripts.inspect_news_sources
```

News smoke uses an isolated temp DB and local scripted HTTP, zero actual network,
zero real or simulated orders, no native SDK import, and no stage evidence. It
checks unknown startup, scope, currency/event boundaries, no 304 timestamp refresh,
latest-epoch revocation, breaking headlines, provider failures and no auto-resume.
`tests/news_helpers.py` includes hand-authored **native-tagged contract DTOs** to
exercise proof/risk branches; their source claims are not real native evidence,
provider validation or promotion permission. Existing native signal fixtures were
updated to publish this explicit managed test contract, never to weaken the gate.

Read `docs/VALIDATION.md` for actual full-suite and clean-extraction results. Remaining
unvalidated: entitled API plans/live feeds/calendars, true publication/completeness/
latency, broker-country/timezone contracts, native Windows/MT5 orders, PostgreSQL,
real security audit, Telegram/initData delivery, scheduler, historical backtests,
promotion and deployment. No profit/latency/coverage guarantee is made.

**Next: Part 9 — owner-only aiogram commands, secure initData authentication,
FastAPI Mini App backend and mobile dark frontend.**
