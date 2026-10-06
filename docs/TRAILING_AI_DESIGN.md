# AI-adaptive trailing stop: lock-first design

Files: `trading/ai_adaptive_trailing.py` (AI layer), `trading/trailing_engine.py` (mechanical tiers
plus the never-loosen guard), `trading/position_manager.py` (`adaptive=` hook) and
`tests/test_ai_adaptive_trailing.py`.

It runs only on the paper engine and mock broker by default: `LIVE_TRADING=false`,
`PAPER_TRADING=true`, `MT5_BACKEND=mock`, and native adapters use `DenyAllWrites`.

## Sequence (every position cycle)

```
profit ≥ 30 % of target
  1. MECHANICAL LOCK 30 %   TrailingEngine.plan → ExecutionEngine.protect_sl   (instant, no AI)
  2. CONFIRM                status ∈ {FILLED, ACCEPTED, NO_CHANGE} and fresh position.sl ≥ lock
                            not confirmed → STOP here (AI is never consulted for this tier)
  3. ASK AI (≤ 2 s)         context: price, distance to next tier, M5/M15/H1 trend strength,
                            RSI, ATR, volatility, news risk, minutes to session close
  4. EXECUTE (never looser) hold_to_60 | hold_to_90 | close_now | tighten_lock
profit ≥ 60 %  → lock 60 % → AI: hold_to_90 | close_now | tighten_lock
profit ≥ 90 %  → lock 90 % → AI: extend_tp_to_120 | close_now | tighten_lock
```

| Situation | Behaviour |
|---|---|
| AI slower than `AI_TRAILING_TIMEOUT_SECONDS` (2 s), unreachable, circuit open | Mechanical 30→60→90 continues; WARNING `AI unavailable, using mechanical trailing` |
| Invalid JSON, schema violation, decision not allowed at this tier, confidence < `AI_CONFIDENCE_THRESHOLD` | Same mechanical fallback (journal `final_action=mechanical_continue`) |
| `hold_to_90` | No AI call at 60 (tier lock still placed mechanically); a reversal is caught by the 30/60 stop already in place |
| `close_now` | `close_owned` → the stop already protects the locked profit; journal `closed_with_locked_profit` |
| `tighten_lock` | Stop moves 50 % of the way from the lock toward price; `assert_never_loosens` runs before sending |
| `extend_tp_to_120` | Only with `ALLOW_TP_EXTENSION=true` and the existing bound `PositionReview` checks; otherwise `extension_not_permitted_lock_kept` |
| AI overlay `skip_trailing_levels` | Skips only the AI **consultation** at that tier. The mechanical lock is never skipped |

## Invariants (asserted by tests)

1. The stop for a tier is locked (and durably recorded) **before** the AI is called.
2. AI unavailable → mechanical trailing continues, and nothing is closed because the AI is missing.
3. `close_now` at 60 % closes with the locked profit.
4. `hold_to_90` holds until 90 % or until a reversal hits the existing lock.
5. Invalid JSON → mechanical fallback.
6. An attempt to remove or loosen a lock raises `LockRegressionError` (BUY stop may only rise, SELL
   stop may only fall). The rejection is asserted and nothing is sent.

The AI can never override risk limits, change `max_open_positions`, touch local pause/kill state, or remove a
lock. Protective work keeps running while entries are paused or killed.

## Journal (`ai_decision_journal`, kind = `trailing`)

`time, position_id, threshold_reached, source (ai|mechanical), action (ai_decision), confidence,
reason, final_action, model, input_summary (context), outcome_usd/outcome_account (attached when the
trade closes by the learning loop)`. A threshold that is already journaled is not consulted again after
a restart.

Final actions: `hold_to_60`, `hold_to_90`, `hold`, `mechanical_continue`,
`closed_with_locked_profit`, `lock_tightened`, `tighten_not_possible_lock_kept`,
`tp_extended_to_120`, `extension_not_permitted_lock_kept`, `extension_not_available_lock_kept`,
`ai_action_failed`.

## Settings

```
AI_ADAPTIVE_TRAILING_ENABLED=true   # false = pure mechanical 30/60/90 (reviewed Part 4 behaviour)
AI_TRAILING_TIMEOUT_SECONDS=2       # 0.5–5
AI_CONFIDENCE_THRESHOLD=70
ALLOW_TP_EXTENSION=false            # extend_tp_to_120 is refused while false
```

## Try it (mock/paper, offline)

```
python -m pytest -q tests/test_ai_adaptive_trailing.py
python -m scripts.smoke_ai_first                  # 30 lock → AI hold → 60 lock → AI close_now
python -m scripts.smoke_ai_first --provider rule  # AI down → mechanical 30 → 60, position kept
```
