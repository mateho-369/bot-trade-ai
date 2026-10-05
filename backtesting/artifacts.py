"""Private fresh run directories, immutable input copies and bounded human/machine reports."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from backtesting.dataset import DatasetError
from core.security import canonical_json


def new_run_directory(path: Path) -> Path:
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise DatasetError("symlinked replay output forbidden")
    if path.exists():
        raise DatasetError("replay output must be new; NEVER reuse or reset an existing ledger")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700)
    return path


def write_json(path: Path, document):
    raw = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(raw)
    os.chmod(path, 0o600)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def write_jsonl(path: Path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for index, row in enumerate(rows):
            if index >= 1000000:
                raise DatasetError("artifact journal exceeds bounded row limit")
            handle.write(canonical_json(row) + "\n")
    os.chmod(path, 0o600)


def copy_inputs(dataset, directory):
    root = directory / "inputs"
    root.mkdir(mode=0o700)
    for name, raw in dataset.files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as handle:
            handle.write(raw)
        os.chmod(target, 0o600)


def report_markdown(report):
    metrics = report["metrics"]
    return (
        "# MT5 AI ReflexBot — historical replay\n\n"
        "**RESEARCH ONLY. No broker connection, live permission or automatic promotion.**\n\n"
        f"- Data origin: `{report['origin']['kind']}` — {report['quote_mode']}\n"
        f"- Review mode: `{report['review_mode']}`; artificial reviews: {report['artificial_reviews']}\n"
        f"- Replay interval: {report['started_at']} → {report['finished_at']}\n"
        f"- Simulated entries enabled: {report['simulated_execution_enabled']}\n"
        f"- ML filter enabled: {report['model_policy_enabled']}; {report['model_filter']}\n"
        f"- Frozen model SHA-256: {report['model_sha256']}\n"
        f"- Closed / open trades: {metrics['closed_trades']} / {metrics['open_trades']}\n"
        f"- Closed-trade net ({metrics['currency']}): {metrics['net_profit_account']}\n"
        f"- Profit factor: {metrics['profit_factor']} (undefined with no losses, not infinity)\n"
        f"- Sampled equity drawdown: {metrics['max_drawdown_percent']}%\n"
        f"- Unexplained missing M1 rows: {metrics['unexplained_gaps']}\n"
        f"- OHLC stop-first ambiguities: {report['ohlc_stop_first_ambiguities']}\n\n"
        "## Interpretation\n\n"
        "Costs include executable bid/ask, configured adverse slippage, commissions and elapsed swap. "
        "These are simulation assumptions, not a broker fee/fill attestation. Fixed linear contracts and "
        "declared session schedules require independent review. No market impact, latency distribution, "
        "queue priority, intratick drawdown or historical contract changes are proved.\n\n"
        "OHLC checks carried positions only after a bar is available, chooses SL when both SL and TP "
        "are touched, applies adverse gap/slippage, and never uses early high/low/close for an entry "
        "or trail. It cannot reconstruct intrabar trailing or exact fill timestamps.\n\n"
        "Enabled ML approvals require real private-registry inference from the immutable pre-entry vector, "
        "bound to one reconstructed pre-replay model and corpus. Model unavailability, expiry or a low "
        "probability vetoes approval and order revalidation. No fitting/switching occurs inside the replay. "
        "Declared timestamps and exact reconstruction are NOT historical availability or authentic source "
        "attestations. Selected-trade labels are not unbiased opportunity labels; probabilities are not "
        "proven calibrated. The import is NOT genuine owner authorization or production activation.\n\n"
        "Missing/stale news, absent/mismatched AI reviews and insufficient warmup veto entries. "
        "A zero-trade result is valid. Six trades/day is a target, never a minimum.\n\n"
        "See `report.json`, `signals.jsonl`, `operations.jsonl`, `equity.jsonl`, `trades.jsonl`, "
        "the isolated SQLite ledger and immutable `inputs/` copies.\n\n"
        "## Promotion\n\n"
        "This report is NOT `reflex-stage-v1` evidence. Editing a label is not qualification. "
        "An independently reviewed historical stage artifact is still required by the production StageGate; "
        "paper/demo additionally require actual native-source reconciled ledgers and continuous coverage. "
        "Live also needs a current explicit owner/session approval.\n"
    )
