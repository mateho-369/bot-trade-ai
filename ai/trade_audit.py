"""Trade audit: every trade must match exactly one valid AI approval in the decision journal.

Flags
-----
NO_AI_APPROVAL        no executed entry journal row is linked to the trade's position
RULE_FALLBACK_TRADE   the trade was opened by the rule fallback (never an AI decision)
UNKNOWN_DECIDER       attribution could not name the AI (pre-attribution or unlabelled rows)
JOURNAL_MISMATCH      the linked approval disagrees with the trade (side, symbol, confidence below
                      threshold, approval after the open, or label differs from the attribution)
TRADE_AFTER_AI_WAIT   the latest AI entry decision for the symbol before the open was a wait/veto

Read-only: the audit never changes trades or journal rows (``sync`` only fills attribution).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ai.decision_journal import AIDecisionJournal
from ai.trade_attribution import RULE_FALLBACK, UNKNOWN, TradeAttribution, approval_row, decided_label, sync
from core.models import Trade

FLAGS = (
    "NO_AI_APPROVAL",
    "RULE_FALLBACK_TRADE",
    "UNKNOWN_DECIDER",
    "JOURNAL_MISMATCH",
    "TRADE_AFTER_AI_WAIT",
)
APPROVING = ("approved_to_risk_engine",)


def _symbol_match(journal_symbol: str | None, trade_symbol: str) -> bool:
    """Journal rows carry the logical symbol; trades the broker-native one (suffixes allowed)."""
    if not journal_symbol:
        return False
    a, b = journal_symbol.upper(), trade_symbol.upper()
    return a == b or b.startswith(a) or a.startswith(b)


def _approved(row) -> bool:
    final = row.final_action or ""
    return final in APPROVING or final.startswith("execution_")


def audit_trades(database, settings, clock, *, limit: int = 1000, run_sync: bool = True) -> dict:
    if run_sync:
        sync(database, clock)
    threshold = float(settings.ai_confidence_threshold)
    flags: list[dict] = []
    checked = 0
    with database.session() as session:
        trades = session.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all()
        for trade in trades:
            checked += 1
            attr = session.get(TradeAttribution, trade.id)
            row = approval_row(session, trade.position_identifier)

            def flag(code: str, detail: str, trade=trade) -> None:
                flags.append(
                    {"trade_id": trade.id, "symbol": trade.symbol, "flag": code, "detail": detail[:160]}
                )

            if row is None:
                flag("NO_AI_APPROVAL", "no executed entry journal row linked to this position")
            label = decided_label(row)
            decided = attr.decided_by if attr is not None else label  # Read-only runs need no sync.
            if label == RULE_FALLBACK or decided == RULE_FALLBACK:
                flag("RULE_FALLBACK_TRADE", "opened by the rule fallback, not an AI decision")
            elif decided == UNKNOWN or label == UNKNOWN:
                flag("UNKNOWN_DECIDER", "the deciding AI label is unknown")
            if row is not None:
                problems = []
                if row.action != "open_" + trade.direction:
                    problems.append(f"journal action {row.action} vs trade {trade.direction}")
                if not _symbol_match(row.symbol, trade.symbol):
                    problems.append("symbol differs")
                if label != RULE_FALLBACK and (row.confidence is None or row.confidence < threshold):
                    problems.append("confidence below threshold")
                if not _approved(row):
                    problems.append("journal row not approved")
                if trade.open_time is not None and row.time > trade.open_time + timedelta(seconds=5):
                    problems.append("approval recorded after the open")
                if attr is not None and attr.decided_by not in {UNKNOWN, label}:
                    problems.append("attribution label differs from journal")
                if problems:
                    flag("JOURNAL_MISMATCH", "; ".join(problems))
            if trade.open_time is not None:
                previous = session.scalars(
                    select(AIDecisionJournal)
                    .where(
                        AIDecisionJournal.kind == "entry",
                        AIDecisionJournal.source.in_(("ai", "cache")),
                        AIDecisionJournal.time <= trade.open_time,
                    )
                    .order_by(AIDecisionJournal.id.desc())
                    .limit(50)
                ).all()
                latest = next((r for r in previous if _symbol_match(r.symbol, trade.symbol)), None)
                if latest is not None and (row is None or latest.id != row.id) and not _approved(latest):
                    if row is None or latest.id > row.id:
                        flag("TRADE_AFTER_AI_WAIT", f"latest AI decision #{latest.id} was {latest.action}")
    counts = {code: sum(f["flag"] == code for f in flags) for code in FLAGS}
    return {"checked": checked, "flags": flags, "counts": counts, "clean": not flags}


def format_report(report: dict, *, limit: int = 15) -> str:
    if report["clean"]:
        return (
            f"Trade audit: CLEAN · {report['checked']} trades checked, every trade has a valid AI approval."
        )
    lines = [
        f"Trade audit: {len(report['flags'])} FLAG(S) · {report['checked']} trades checked",
        " · ".join(f"{code} {n}" for code, n in report["counts"].items() if n),
    ]
    for item in report["flags"][:limit]:
        lines.append(f"#{item['trade_id']} {item['symbol']} {item['flag']}: {item['detail']}")
    return "\n".join(lines)[:3500]


__all__ = ["FLAGS", "audit_trades", "format_report"]
