# AI decision safety and local reporting

## Decision boundary

The AI-first layer is advisory. It can add an entry veto, request a bounded risk reduction, review a
position or produce an inactive learning candidate. It cannot call the broker, resume the runtime,
clear a kill/loss/drawdown latch, disable SL/TP, bypass news/spread/stage gates or authorize a LIVE trade.
`AI_REQUIRE_APPROVAL=true` is the default; timeout, invalid JSON, missing key, open circuit or unhealthy AI
means no valid approval and therefore no new entry. A valid wait/reject/low-confidence answer is final.

Every decision is schema-validated, time/config/code/data-bound and journaled. Provider retries, timeout,
response size, concurrency and circuit-breaker behavior are bounded. A valid response from one provider
is not replaced merely because another provider might be more permissive.

## Outage policy

`BLOCK_ON_AI_FAILURE` is the default and safest behavior. It blocks new entries while the AI cannot
answer; existing broker-side protection and mechanical trailing continue while the runtime is active.
`TECHNICAL_ONLY` is a local operator policy choice, not an AI decision. Use only after review:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env ai-fallback --mode BLOCK_ON_AI_FAILURE
```

The technical alternative still cannot trade when `AI_REQUIRE_APPROVAL=true`, and it does not bypass
risk, news, spread, stage, pause/kill or broker gates. Missing/invalid AI health blocks AUTONOMOUS_DEMO
resume. The fallback mode is stored/audited locally; there is no Telegram or Mini App control route.

## Bounded configuration and proposals

AI dynamic limits stay within the reviewed hard caps. Minor bounded adjustments may affect only the
runtime overlay; increases also require the configured market/news conditions. Major, structural,
weight or symbol changes remain pending for a separate explicit local review and never edit `.env`.
Projection into settings requires the reviewed stopped/flat typed flow, invalidates prior stage evidence
through its configuration hash, and does not start the runtime. Position-close suggestions cannot directly
execute a close. The AI cannot adjust the kill switch or any hard risk limit.

To restore reviewed AI overlays without touching risk/capital latches:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops --env-file .env reset-ai --confirm RESET_AI_TO_REVIEWED_DEFAULTS
```

## Position management and learning

Mechanical stop-lock improvement runs before optional AI trailing review. AI may hold, close early or
request a tighter legal stop; it cannot loosen/remove a verified stop or extend TP unless separately
configured. Invalid/slow AI returns to the mechanical path. Broker-side SL/TP remains the position's
protection if the runtime stops; local trailing does not.

Learning uses reconciled closed-trade labels, bounded feature sets, causal time separation and purged
validation. A trained model is an inactive candidate; it is not activated/promoted by an AI answer or a
local report. Synthetic smoke rows are tests only and never strategy or stage evidence.

## Reporting

AI decisions and circuit events are mirrored to console and local report files. An optional `Reporter`
may send bounded outbound Telegram `sendMessage` HTTPS reports; no inbound updates, commands, polling,
webhooks, keyboards or Mini App exist. Khmer wording/key parity is covered by tests; see `KHMER_REVIEW.md`.
