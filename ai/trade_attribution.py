"""Who decided every trade: exact link between the AI decision journal and trade outcomes.

``trade_attribution`` is an ADDITIVE 1:1 side table keyed by ``trades.id`` (separate journal
metadata, like ``ai_decision_journal``). The core ``trades`` table and SCHEMA_VERSION stay
unchanged, so existing databases need no destructive migration and the core schema check keeps its
exact-column guarantee. Trades that existed before this table (or that have no matching approval)
are attributed ``unknown``.

Columns
-------
decided_by           AI label (``groq``, ``groq2``, ``a+b``), ``RULE_FALLBACK`` or ``unknown``
ai_model / ai_confidence / approval_journal_id / signal_id   from the approving journal row
trailing_by          label of the AI whose trailing action was executed, else ``MECHANICAL``
close_by             AI label when an AI close was executed, else the broker/owner close reason
demo_fast_track      the order was sent under DEMO_FAST_TRACK (never promotion evidence)

The link is exact: ``AIFirstLayer.link_execution`` stamps the filled position id on the approving
``entry`` journal row; ``sync`` joins ``trades.position_identifier`` to that row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, inspect, select
from sqlalchemy.orm import Mapped, mapped_column

from ai.decision_journal import AIDecisionJournal, JournalBase
from core.models import AuditLog, OrderIntent, Trade, UTCDateTime

UNKNOWN = "unknown"
RULE_FALLBACK = "RULE_FALLBACK"
MECHANICAL = "MECHANICAL"
APPROVED_ACTIONS = ("approved_to_risk_engine",)
NOTIFY_WINDOW = timedelta(hours=1)  # Older (historic) trades are attributed silently.


class TradeAttribution(JournalBase):
    __tablename__ = "trade_attribution"
    trade_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    position_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False, default=UNKNOWN, index=True)
    ai_model: Mapped[str | None] = mapped_column(String(128))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    approval_journal_id: Mapped[int | None] = mapped_column(Integer)
    signal_id: Mapped[int | None] = mapped_column(Integer)
    trailing_by: Mapped[str] = mapped_column(String(64), nullable=False, default=MECHANICAL)
    close_by: Mapped[str | None] = mapped_column(String(64))
    demo_fast_track: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    entry_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    close_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


def ensure_attribution_tables(database) -> None:
    if "trade_attribution" not in set(inspect(database.engine).get_table_names()):
        TradeAttribution.__table__.create(database.engine, checkfirst=True)


def approval_row(session, position_id: int | None):
    """The executed ENTRY journal row that approved this position (exact link), or None."""
    if position_id is None:
        return None
    return session.scalar(
        select(AIDecisionJournal)
        .where(
            AIDecisionJournal.kind == "entry",
            AIDecisionJournal.position_id == position_id,
            AIDecisionJournal.executed.is_(True),
        )
        .order_by(AIDecisionJournal.id.desc())
        .limit(1)
    )


def decided_label(row) -> str:
    if row is None:
        return UNKNOWN
    if row.source == "rule_fallback" or row.provider_label == RULE_FALLBACK:
        return RULE_FALLBACK
    if row.source in {"ai", "cache"}:
        return row.provider_label or UNKNOWN
    return UNKNOWN


def _ai_actions(session, position_id: int | None):
    if position_id is None:
        return []
    return session.scalars(
        select(AIDecisionJournal)
        .where(
            AIDecisionJournal.position_id == position_id,
            AIDecisionJournal.kind.in_(("trailing", "position")),
            AIDecisionJournal.source.in_(("ai", "cache")),
            AIDecisionJournal.executed.is_(True),
        )
        .order_by(AIDecisionJournal.id)
    ).all()


def view(attr: TradeAttribution, trade: Trade) -> dict:
    return {
        "trade_id": trade.id,
        "symbol": trade.symbol,
        "side": trade.direction,
        "volume": str(trade.volume),
        "status": trade.status,
        "decided_by": attr.decided_by,
        "ai_model": attr.ai_model,
        "ai_confidence": attr.ai_confidence,
        "approval_journal_id": attr.approval_journal_id,
        "trailing_by": attr.trailing_by,
        "close_by": attr.close_by,
        "close_reason": trade.close_reason,
        "profit": str(trade.profit),
        "currency": trade.currency,
        "demo_fast_track": attr.demo_fast_track,
    }


def sync(database, clock, *, notifier=None, account_key: str | None = None, limit: int = 500) -> dict:
    """Idempotent: create/refresh attribution rows; notify entry/close ONCE (recent trades only)."""
    ensure_attribution_tables(database)
    now = clock.now()
    created = updated = notified = 0
    messages: list[tuple[str, dict]] = []
    with database.session() as session:
        query = select(Trade).order_by(Trade.id.desc()).limit(limit)
        if account_key is not None:
            query = query.where(Trade.account_key == account_key)
        for trade in session.scalars(query).all():
            attr = session.get(TradeAttribution, trade.id)
            if attr is None:
                attr = TradeAttribution(
                    trade_id=trade.id,
                    account_key=trade.account_key,
                    position_id=trade.position_identifier,
                    symbol=trade.symbol[:64],
                    direction=trade.direction,
                    decided_by=UNKNOWN,
                    trailing_by=MECHANICAL,
                    updated_at=now,
                )
                intent = session.get(OrderIntent, trade.order_intent_id)
                attr.demo_fast_track = bool(
                    intent is not None and (intent.request or {}).get("demo_fast_track")
                )
                session.add(attr)
                created += 1
            before = (attr.decided_by, attr.trailing_by, attr.close_by)
            if attr.decided_by == UNKNOWN:
                row = approval_row(session, trade.position_identifier)
                if row is not None:
                    attr.decided_by = decided_label(row)
                    attr.ai_model = row.model
                    attr.ai_confidence = row.confidence
                    attr.approval_journal_id = row.id
                    attr.signal_id = row.signal_id
            actions = _ai_actions(session, trade.position_identifier)
            trailing = [a for a in actions if a.kind == "trailing"]
            if trailing:
                attr.trailing_by = trailing[-1].provider_label or UNKNOWN
            if trade.close_time is not None and attr.close_by is None:
                closer = [
                    a for a in actions if "close" in (a.action or "") or "close" in (a.final_action or "")
                ]
                attr.close_by = (
                    (closer[-1].provider_label or UNKNOWN)
                    if closer
                    else ((trade.close_reason or MECHANICAL)[:64])
                )
            if (attr.decided_by, attr.trailing_by, attr.close_by) != before:
                attr.updated_at = now
                updated += 1
            recent_open = trade.open_time is not None and now - trade.open_time <= NOTIFY_WINDOW
            if not attr.entry_notified:
                attr.entry_notified = True
                if recent_open:
                    messages.append(("opened", view(attr, trade)))
            if trade.close_time is not None and not attr.close_notified:
                attr.close_notified = True
                if now - trade.close_time <= NOTIFY_WINDOW:
                    messages.append(("closed", view(attr, trade)))
    if notifier is not None:
        for kind, item in messages:
            hook = getattr(notifier, "trade_opened" if kind == "opened" else "trade_closed", None)
            if hook is not None:
                hook(item)
                notified += 1
    return {"created": created, "updated": updated, "notified": notified}


def history(database, *, limit: int = 20) -> list[dict]:
    with database.session() as session:
        rows = session.execute(
            select(TradeAttribution, Trade)
            .join(Trade, Trade.id == TradeAttribution.trade_id)
            .order_by(Trade.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
        return [view(attr, trade) for attr, trade in rows]


def ai_stats(database, *, since: datetime | None = None) -> dict:
    """Per-label approvals/rejections/trades/win rate/net profit/avg confidence/failures."""
    ensure_attribution_tables(database)
    labels: dict[str, dict] = {}

    def item(label: str) -> dict:
        return labels.setdefault(
            label,
            {
                "label": label,
                "approvals": 0,
                "rejections": 0,
                "trades": 0,
                "closed": 0,
                "wins": 0,
                "net_profit": Decimal("0"),
                "confidence_sum": 0.0,
                "confidence_n": 0,
                "failures": 0,
            },
        )

    with database.session() as session:
        query = select(AIDecisionJournal).where(
            AIDecisionJournal.kind == "entry", AIDecisionJournal.source.in_(("ai", "cache", "rule_fallback"))
        )
        if since is not None:
            query = query.where(AIDecisionJournal.time >= since)
        for row in session.scalars(query).all():
            entry = item(decided_label(row))
            approved = (row.final_action or "") in APPROVED_ACTIONS or (row.final_action or "").startswith(
                "execution_"
            )
            if approved:
                entry["approvals"] += 1
                if row.confidence is not None:
                    entry["confidence_sum"] += float(row.confidence)
                    entry["confidence_n"] += 1
            else:
                entry["rejections"] += 1
        trades = select(TradeAttribution, Trade).join(Trade, Trade.id == TradeAttribution.trade_id)
        if since is not None:
            trades = trades.where(Trade.open_time >= since)
        for attr, trade in session.execute(trades).all():
            entry = item(attr.decided_by)
            entry["trades"] += 1
            if trade.close_time is not None:
                entry["closed"] += 1
                entry["wins"] += trade.profit > 0
                entry["net_profit"] += trade.profit
        failures = select(AuditLog).where(AuditLog.action == "ai.provider_failure")
        if since is not None:
            failures = failures.where(AuditLog.time >= since)
        for row in session.scalars(failures).all():
            label = (row.details or {}).get("label")
            if isinstance(label, str) and label:
                item(label[:64])["failures"] += 1
    result = []
    for label in sorted(labels):
        entry = labels[label]
        result.append(
            {
                "label": label,
                "approvals": entry["approvals"],
                "rejections": entry["rejections"],
                "trades": entry["trades"],
                "closed": entry["closed"],
                "win_rate": None if not entry["closed"] else round(entry["wins"] / entry["closed"] * 100, 1),
                "net_profit": str(entry["net_profit"]),
                "avg_confidence": None
                if not entry["confidence_n"]
                else round(entry["confidence_sum"] / entry["confidence_n"], 1),
                "failures": entry["failures"],
            }
        )
    return {"labels": result, "since": since.isoformat() if since else None}


__all__ = [
    "MECHANICAL",
    "RULE_FALLBACK",
    "UNKNOWN",
    "TradeAttribution",
    "ai_stats",
    "approval_row",
    "decided_label",
    "ensure_attribution_tables",
    "history",
    "sync",
]
