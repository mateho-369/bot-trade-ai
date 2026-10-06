# Offline backtest bundle audit

`python -m scripts.audit_backtest` checks a completed immutable research bundle using bounded local file
reads and a captured SQLite image. It does not start a broker/provider/runtime, load a private `.env`, open
a network listener, or change the source bundle/database.

```powershell
.\.venv\Scripts\python.exe -B -m scripts.audit_backtest --run data\backtests\<reviewed-run>
```

The report is a consistency audit of research artifacts: hashes/lengths, JSONL bounds, metrics-to-trade
bindings, saved intents/deals, checkpoint identity and stopped/released research state. It does not
reconstruct the market or prove complete/unbiased data, fee/fill realism, predictive skill, profitability,
Windows behavior, broker execution or stage eligibility. A green result is not permission to trade.

Synthetic/mock quotes and scripted AI reviews are explicitly test provenance. Do not use a research result
as `reflex-stage-v1` evidence. Keep existing account state, WAL/journal files, stop markers, latches and
uncertain intents intact; an audit refusal is not a reason to delete/reseed them.

No audit result has an operator control path. Local runtime actions remain in `python -m scripts.ops`;
`python -m scripts.live_view` reads only local health/report files.
