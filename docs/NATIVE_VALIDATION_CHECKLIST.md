# Actual Windows/native validation checklist — NOT executed by Part 12

Use this on the actual dedicated interactive Windows x64 host with real operator review.
Empty checklist fields are NOT placeholders for passing evidence; they are unverified
requirements. `scripts.readiness` cannot attest them and never approves deployment/trading.
No genuine credential, dataset, native host or permission was supplied for this delivery.

## A. Trusted local installation

- [ ] Verify the independently trusted ZIP SHA and current manifest; inspect actual source.
- [ ] Private local NTFS source/data/.env/venv/backups/logs; examine real ACL inheritance,
      effective read/write/delete rights and owner/group—not Unix chmod assumptions.
- [ ] Patch OS/Python; private ordinary limited user, no SYSTEM/service/Session 0/S4U;
      interactive user actually logged on. RDP disconnect policy tested separately from logoff.
- [ ] Review direct/transitive/native wheel hashes, platform-specific lock, pip consistency,
      actual-package imports and vulnerability audit; no private pip-index token in history/logs.
- [ ] Validate broker-vendor terminal signature/path/distribution and licensed SDK. File
      existence alone is not vendor identity, login, account type or trading permission.

## B. Native read-only connection — only after explicit actual-owner permission

- [ ] Review `scripts.check_mt5_readonly` first; its explicit operator invocation initializes
      native MT5 and is NOT part of offline readiness execution here. Do not confuse it
      with the no-connection Part 12 tool or call it silently from preflight.
- [ ] Verify intended account/server/kind/currency/cent/USDC handling from genuine SDK data.
- [ ] Verify configured logical/native symbol aliases and actual digits/point/tick grids,
      tick-value asymmetry, contract profit/margin model, min/max/step lot rules,
      stop/freeze levels, spread/fees/swap/margin/leverage/trading sessions.
- [ ] Verify all signed fresh FX conversion legs and funding/proceeds direction, no USD
      relabeling or synthetic symbol contracts; no source/proof tag manufactures a quote.
- [ ] Test read timeout/reconnect and real closed candle/tick timestamps without restamping;
      no async Telegram blocking or concurrent unsafe SDK queueing.
- [ ] Confirm no actual write occurs without durable owner/risk/stage/source/expiry gates.
      Native adapter default remains DenyAllWrites; read-only tests do not authorize orders.

## C. Owner/AI/news/security

- [ ] Genuine private Telegram owner launch and HMAC initData TTL/duplicate/non-owner tests;
      do not use TEST_ONLY token/bearer fixtures as authentic identity.
- [ ] Actual HTTPS/TLS/trusted host/proxy headers and edge limits; raw backend private;
      source-root secrets private; no frontend/token/password/bearer/request-body logging.
- [ ] Actual entitled provider/schema/timeout/fallback behavior and causal archived contexts;
      live-data/calendar coverage/first-seen/revisions/impact/exposure independently reviewed.
- [ ] Selected causal model artifact/registry/code/config scope, original owned reconciled
      labels and purged/embargoed holdout/walk-forward; no filter bypass or fabricated confidence.
- [ ] Owner pause/kill/confirm/close scope and at-most-once uncertain notices tested safely
      in authentic paper/demo context; never assume a close-all is atomic account flatten.

## D. Durability/deployment/operations

- [ ] Existing schema 2/full verifier/migration history; no fresh DB or latch/capital reset
      to recover from loss, an uncertain acknowledgement or a failed stage.
- [ ] Matching DB/checkpoint/model/news/candles and WAL-aware stopped coherent backups;
      actual restore/ACL/hash/generation/lease recovery test, not online DB-only copy.
- [ ] Actual OS lock/SQL lease single-runtime exclusion across processes/users; source/env
      change stop, PAUSED restart, graceful accepted-write/native Future drain.
- [ ] Confirmed-exit-only watchdog, 30 s checks and durable launch budget; stale alive child
      alerts/stops gracefully, never force kill/replacement or blind native order retry.
- [ ] VBS minimized terminal/hidden supervisor, actual logged-on Task Scheduler AtLogOn /
      Interactive / Limited / IgnoreNew/no-password configuration; no Session 0 service or
      “run whether logged on or not.” Read scripts then -WhatIf; no task registered here.
- [ ] Real-platform PS1/VBS/executable behavior, Windows shutdown/update/reboot/sleep/RDP,
      clock/disk/power/connectivity degradation, performance/soak and failure/recovery tests.
- [ ] Optional PostgreSQL role restrictions/audit privileges/real-server tests and compiled
      executable evidence identity, if used. Neither was native-validated in this release.

## E. Authentic chronological strategy/stage review

- [ ] Independent licensed genuine history/cost/fill sensitivity/no-lookahead/source review;
      supplied synthetic replay/ML/auth/proof fixtures cannot qualify.
- [ ] Original current model/config/code/context bindings and robustness/selection evidence;
      baseline replay without a causal approved registry cannot be claimed ML-filtered.
- [ ] Actual native-data paper ≥14 completed days, ≥100 authentic reconciled closed trades,
      finite PF ≥1.1, sampled drawdown ≤5%, complete cash/credit-adjusted coverage/proof legs.
- [ ] Actual native DEMO account and chronological matching paper/demo artifacts and same
      quality/duration requirements; signed fresh-owner exact-digest stopped/flat import.
- [ ] Only after complete authentic backtest→paper→demo + platform/security review, actual
      owner may separately explicitly configure/prepare/confirm small-live approval under
      default 0.1% risk cap/current session/evidence. Startup/reconnect/import remains PAUSED.

Maximum twelve trades/day is a cap; six/day is a target, never forced. $5 and 30/60/90%
locks are configurable net estimates, not guarantees. Gaps/costs/spread/FX/freeze rules
can defeat nominal SL/lock cash. No martingale/grid/doubling/withdrawals/password changes,
unapproved risk escalation, automatic promotion/model selection/proposal apply or resume.

Record genuine results privately with source/account/artifact hashes and original time;
never retrofit TEST_ONLY tags, native SQL or signed fixture bearers into provenance.
Passing an offline checkbox/document is not owner permission or a genuine broker fact.
