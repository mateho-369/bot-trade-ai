"""Bounded, allowlisted SQL projections. Absolutely NO broker/provider calls.

Money strings are account currency unless explicitly labelled USD. Missing or
stale observations are shown as unknown, never zero or a fabricated live P&L.
GET does not expire proposals, acknowledge alerts, reconcile paper, or log writes.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from core.models import (
    AccountSnapshot,
    AISuggestion,
    AuditLog,
    BotState,
    News,
    OrderIntent,
    RiskState,
    Signal,
    Trade,
)
from core.security import sanitize_text
from trading.runtime_state import TERMINAL_INTENT_STATES


class OwnerReadModel:
    def __init__(self, database, settings, clock, *, execution=None):
        self.database, self.settings, self.clock, self.execution = database, settings, clock, execution

    @property
    def account_key(self):
        return self.execution.account_key if self.execution is not None else None

    def text(self, value, maximum=240):
        clean = sanitize_text(value, self.database.secrets)
        return " ".join(clean.split())[:maximum]

    @staticmethod
    def number(value):
        return (
            value
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
            else None
        )

    def meta(self):
        return {
            "generated_at": self.clock.now().isoformat(),
            "mode": self.settings.mode.value,
            "source": "database_projection",
            "broker_read": False,
            "fixture_only": False,
            "runtime_attached": self.execution is not None,
            "not_stage_evidence": True,
        }

    @staticmethod
    def bounds(limit, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise ValueError("bounded pagination required")

    def _trades_query(self):
        query = select(Trade).where(Trade.mode == self.settings.mode.value)
        if self.account_key is not None:
            query = query.where(Trade.account_key == self.account_key)
        return query

    def _trade(self, row):
        meta = row.features_json.get("execution", {})
        owned = (
            self.execution is not None
            and row.account_key == self.account_key
            and row.status == "open"
            and row.ticket is not None
            and row.position_identifier is not None
            and meta.get("version") == 1
            and meta.get("magic") == self.settings.mt5_magic_number
        )
        return {
            "id": row.id,
            "ticket": row.ticket,
            "position_identifier": row.position_identifier,
            "symbol": self.text(row.symbol, 64),
            "direction": row.direction,
            "volume": str(row.volume),
            "entry_price": str(row.entry_price),
            "sl": str(row.sl),
            "tp": str(row.tp),
            "currency": self.text(row.currency, 8),
            "opened_at": row.open_time.isoformat(),
            "closed_at": row.close_time.isoformat() if row.close_time else None,
            "profit_account": str(row.profit),
            "profit_usd": str(row.profit_usd) if meta.get("profit_usd_verified") is True else None,
            "floating_pnl": None,
            "floating_pnl_status": "not_observed_by_this_interface",
            "strategy": self.text(row.strategy, 64),
            "status": row.status,
            "lock_level": self.number(row.profit_lock_level),
            "target_usd": str(row.target_profit_usd),
            "owned_close_candidate": bool(owned),
            "close_revalidated_at_send": True,
            "config_current": row.config_hash == self.settings.safety_fingerprint(),
        }

    def trades(self, *, limit=50, offset=0, positions_only=False):
        self.bounds(limit, offset)
        query = self._trades_query()
        if positions_only:
            query = query.where(Trade.status.in_(("open", "unknown")))
        with self.database.session() as session:
            rows = session.scalars(query.order_by(Trade.id.desc()).offset(offset).limit(limit)).all()
            items = [self._trade(r) for r in rows]
        attribution = self._attribution([item["id"] for item in items])
        for item in items:
            who = attribution.get(item["id"], {})
            item["decided_by"] = who.get("decided_by", "unknown")
            item["trailing_by"] = who.get("trailing_by", "MECHANICAL")
            item["result"] = (
                "open"
                if item["closed_at"] is None
                else ("win" if Decimal(item["profit_account"]) > 0 else "loss/flat")
            )
        return {"meta": self.meta(), "items": items, "limit": limit, "offset": offset}

    def _attribution(self, trade_ids):
        """Who-decided labels (trade_attribution); a missing table/row reads as unknown."""
        if not trade_ids:
            return {}
        try:
            from ai.trade_attribution import TradeAttribution

            with self.database.session() as session:
                rows = session.scalars(
                    select(TradeAttribution).where(TradeAttribution.trade_id.in_(trade_ids))
                ).all()
                return {r.trade_id: {"decided_by": r.decided_by, "trailing_by": r.trailing_by} for r in rows}
        except Exception:
            return {}

    def dashboard(self):
        now = self.clock.now()
        with self.database.session() as session:
            state = session.get(BotState, 1)
            snapshot = None
            risk = None
            snapshots = []
            if self.account_key is not None:
                snapshots = session.scalars(
                    select(AccountSnapshot)
                    .where(
                        AccountSnapshot.account_key == self.account_key,
                        AccountSnapshot.mode == self.settings.mode.value,
                    )
                    .order_by(AccountSnapshot.id.desc())
                    .limit(48)
                ).all()
                snapshot = snapshots[0] if snapshots else None
                risk = session.scalar(
                    select(RiskState).where(
                        RiskState.account_key == self.account_key, RiskState.mode == self.settings.mode.value
                    )
                )
            heartbeat_age = (now - state.heartbeat).total_seconds() if state and state.heartbeat else None
            lease_current = bool(
                self.execution is not None
                and state is not None
                and state.session_id == self.execution.control.session_id
                and state.last_config_hash == self.settings.safety_fingerprint()
                and heartbeat_age is not None
                and -2 <= heartbeat_age < self.settings.runtime_lease_seconds
            )
            age = (now - snapshot.time).total_seconds() if snapshot else None
            snapshot_current = bool(
                snapshot is not None
                and self.execution is not None
                and lease_current
                and age is not None
                and 0 <= age <= self.settings.risk_observation_max_age_seconds
                and snapshot.metadata_json.get("code_hash") == self.execution.profile.code_hash
                and snapshot.metadata_json.get("model_sha256") == self.execution.profile.model_sha256
                and snapshot.metadata_json.get("source") == self.execution.profile.data_source.value
            )
            day_current = bool(risk is not None and risk.day == now.date())
            try:
                risk_age = (
                    now - datetime.fromisoformat(risk.metadata_json["last_observed_at"])
                ).total_seconds()
                risk_current = 0 <= risk_age <= self.settings.risk_observation_max_age_seconds
            except (AttributeError, KeyError, TypeError, ValueError):
                risk_current = False
            positions = session.scalars(
                self._trades_query()
                .where(Trade.status.in_(("open", "unknown")))
                .order_by(Trade.id.desc())
                .limit(101)
            ).all()
            unsettled = []
            if self.account_key:
                unsettled = session.scalars(
                    select(OrderIntent.id)
                    .where(
                        OrderIntent.account_key == self.account_key,
                        OrderIntent.mode == self.settings.mode.value,
                        OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                    )
                    .limit(101)
                ).all()
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            closed = session.scalars(
                self._trades_query()
                .where(
                    Trade.status == "closed",
                    Trade.close_time >= since,
                    Trade.close_time <= now,
                )
                .order_by(Trade.id.desc())
                .limit(101)
            ).all()
            currency = snapshot.currency if snapshot else self.settings.account_currency
            currencies_valid = all(r.currency == currency for r in closed)
            kpis = {
                "currency": currency,
                "balance": str(snapshot.balance) if snapshot else None,
                "equity": str(snapshot.equity) if snapshot else None,
                "margin": str(snapshot.margin) if snapshot else None,
                "free_margin": str(snapshot.free_margin) if snapshot else None,
                "observation_at": snapshot.time.isoformat() if snapshot else None,
                "observation_age_seconds": age,
                "observation_current": bool(snapshot_current),
                "realized_today_account": str(risk.net_realized_today)
                if risk
                and day_current
                and risk_current
                and snapshot_current
                and risk.metadata_json.get("currency") == currency
                else None,
                "today_currency_verified": bool(
                    risk
                    and day_current
                    and risk_current
                    and snapshot_current
                    and risk.metadata_json.get("currency") == currency
                ),
                "accepted_entries_today": risk.accepted_entries_today if day_current else None,
                "closed_today": len(closed) if len(closed) <= 100 else None,
                "wins_today": sum(r.profit > Decimal(0) for r in closed)
                if currencies_valid and len(closed) <= 100
                else None,
                "open_count": sum(r.status == "open" for r in positions) if len(positions) <= 100 else None,
                "unknown_count": sum(r.status == "unknown" for r in positions)
                if len(positions) <= 100
                else None,
                "daily_loss_latched": risk.daily_loss_latched if risk else None,
                "drawdown_latched": risk.drawdown_latched if risk else None,
                "reserved_risk_usd": str(risk.reserved_risk_usd) if risk else None,
            }
            return {
                "meta": self.meta(),
                "runtime": {
                    "desired_state": state.desired_state if state else "unknown",
                    "kill_switch_active": state.kill_switch_active if state else None,
                    "revision": state.revision if state else None,
                    "lease_current": lease_current,
                    "heartbeat_age_seconds": heartbeat_age,
                    "recovery_required": bool(state is None or state.last_error or unsettled),
                    "last_error": self.text(state.last_error) if state and state.last_error else None,
                    "unsettled_intents": len(unsettled) if len(unsettled) <= 100 else None,
                    "entries_permitted_by_dashboard": False,
                },
                "kpis": kpis,
                "positions": [self._trade(r) for r in positions[:6]],
                "equity_curve": [
                    {"time": r.time.isoformat(), "equity": str(r.equity)}
                    for r in reversed(snapshots)
                    if r.currency == currency
                ],
                "targets": {
                    "daily_target": self.settings.min_daily_trades_target,
                    "daily_maximum": self.settings.max_daily_trades,
                    "trade_target_usd": str(self.settings.target_profit_usd_per_trade),
                },
                "safety": [
                    "No minimum trade obligation",
                    "No dashboard permission to trade",
                    "Stops / protective maintenance remain under existing execution authority",
                    "Resume is NOT live approval; close-all is NOT an atomic broker flatten",
                ],
            }

    def signals(self, *, limit=50, offset=0):
        self.bounds(limit, offset)
        with self.database.session() as session:
            rows = session.scalars(
                select(Signal)
                .where(Signal.mode == self.settings.mode.value)
                .order_by(Signal.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "meta": self.meta(),
                "items": [
                    {
                        "id": r.id,
                        "time": r.time.isoformat(),
                        "bar_time": r.bar_time.isoformat(),
                        "symbol": self.text(r.symbol, 64),
                        "timeframe": r.timeframe,
                        "strategy": self.text(r.strategy),
                        "direction": r.direction,
                        "score": self.number(r.score),
                        "ai_score": self.number(r.ai_score),
                        "final_decision": r.final_decision,
                        "config_current": r.config_hash == self.settings.safety_fingerprint(),
                    }
                    for r in rows
                ],
                "limit": limit,
                "offset": offset,
            }

    def news(self, *, limit=50, offset=0):
        self.bounds(limit, offset)
        with self.database.session() as session:
            rows = session.scalars(
                select(News).order_by(News.time.desc(), News.id.desc()).offset(offset).limit(limit)
            ).all()
            return {
                "meta": self.meta(),
                "coverage": {"status": "unknown", "reason": "news_runtime_not_attached"},
                "calendar": [],
                "items": [
                    {
                        "id": r.id,
                        "published_at": r.time.isoformat(),
                        "first_seen_at": r.fetched_at.isoformat(),
                        "source": self.text(r.source, 64),
                        "title": self.text(r.title, 300),
                        "summary": self.text(r.summary, 500),
                        "impact": r.impact,
                        "sentiment": self.number(r.sentiment),
                        "symbols": [self.text(s, 64) for s in r.symbols[:30]],
                    }
                    for r in rows
                ],
                "limit": limit,
                "offset": offset,
            }

    def suggestions(self, *, limit=50, offset=0):
        self.bounds(limit, offset)
        with self.database.session() as session:
            rows = session.scalars(
                select(AISuggestion).order_by(AISuggestion.id.desc()).offset(offset).limit(limit)
            ).all()
            items = []
            for row in rows:
                data = row.suggestion if type(row.suggestion) is dict else {}
                try:
                    expires = datetime.fromisoformat(data["expires_at"])
                    current = expires.tzinfo is not None and self.clock.now() < expires
                except (KeyError, TypeError, ValueError):
                    current = False
                status = "expired" if row.status in {"pending", "approved"} and not current else row.status
                # Do not expose raw model payloads/arbitrary nested markup/credentials.
                parameters = data.get("parameters", {})
                safe = {}
                if type(parameters) is dict:
                    for key in ("risk_percent", "trade_id", "position_identifier", "fraction", "parameter"):
                        if key in parameters and type(parameters[key]) in {str, int}:
                            safe[key] = self.text(parameters[key], 64)
                    if "value" in parameters and type(parameters["value"]) in {str, int}:
                        safe["value"] = self.text(parameters["value"], 64)
                    elif type(parameters.get("value")) is list:
                        safe["value"] = self.text(",".join(map(str, parameters["value"][:30])), 200)
                    weights = parameters.get("weights")
                    if type(weights) is dict:
                        safe["weights"] = {
                            self.text(k, 64): self.text(v, 32) for k, v in list(weights.items())[:4]
                        }
                items.append(
                    {
                        "id": row.id,
                        "type": row.type,
                        "status": status,
                        "stored_status": row.status,
                        "risk_level": row.risk_level,
                        "reason": self.text(row.reason, 600),
                        "parameters": safe,
                        "expires_at": expires.isoformat() if current else None,
                        "config_current": row.based_on_config_hash == self.settings.safety_fingerprint(),
                        "approval_is_application": False,
                        "approval_executes_trade": False,
                    }
                )
            return {"meta": self.meta(), "items": items, "limit": limit, "offset": offset}

    def settings_view(self):
        cfg = self.settings
        return {
            "meta": self.meta(),
            "editable": False,
            "values": {
                "mode": cfg.mode.value,
                "demo_mode": cfg.demo_mode,
                "paper_trading": cfg.paper_trading,
                "live_trading": cfg.live_trading,
                "symbols": list(cfg.symbols),
                "max_risk_percent_per_trade": str(cfg.effective_risk_percent),
                "max_daily_loss_percent": str(cfg.max_daily_loss_percent),
                "max_drawdown_percent": str(cfg.max_drawdown_percent),
                "max_open_positions": cfg.max_open_positions,
                "max_daily_trades": cfg.max_daily_trades,
                "min_daily_trades_target": cfg.min_daily_trades_target,
                "min_signal_score": cfg.min_signal_score,
                "ai_confidence_threshold": cfg.ai_confidence_threshold,
                "target_net_profit_usd": str(cfg.target_profit_usd_per_trade),
                "trailing_levels": [list(pair) for pair in cfg.trailing_levels],
                "atr_trailing_enabled": cfg.atr_trailing_enabled,
                "allow_tp_extension": cfg.allow_tp_extension,
                "strategy_weights": {k: str(v) for k, v in cfg.strategy_weights.items()},
                "news_required_for_entry": cfg.news_required_for_entry,
                "initdata_ttl_seconds": cfg.telegram_initdata_max_age_seconds,
                "confirmation_ttl_seconds": cfg.owner_confirmation_ttl_seconds,
            },
            "policy": "Settings are read-only. Approval does not apply proposals or alter .env/live/risk.",
        }

    def ai_journal(self, *, limit=50, offset=0):
        """AI Decision Journal + bounded config overlay history (read-only, sanitized)."""
        from sqlalchemy import inspect

        from ai.decision_journal import AIConfigOverlay, AIDecisionJournal

        self.bounds(limit, offset)
        tables = set(inspect(self.database.engine).get_table_names())
        if "ai_decision_journal" not in tables:
            return {
                "meta": self.meta(),
                "items": [],
                "adjustments": [],
                "summary": {},
                "limit": limit,
                "offset": offset,
            }
        with self.database.session() as session:
            rows = session.scalars(
                select(AIDecisionJournal).order_by(AIDecisionJournal.id.desc()).offset(offset).limit(limit)
            ).all()
            items = [
                {
                    "id": r.id,
                    "time": r.time.isoformat(),
                    "kind": self.text(r.kind, 16),
                    "symbol": self.text(r.symbol, 32) if r.symbol else None,
                    "position_id": r.position_id,
                    "threshold": r.threshold_reached,
                    "source": self.text(r.source, 16),
                    "model": self.text(r.model, 64) if r.model else None,
                    "action": self.text(r.action, 32),
                    "confidence": self.number(r.confidence),
                    "reason": self.text(r.reason, 300),
                    "executed": bool(r.executed),
                    "final_action": self.text(r.final_action, 64) if r.final_action else None,
                    "rejection_reason": self.text(r.rejection_reason, 128) if r.rejection_reason else None,
                    "outcome_usd": self.text(r.outcome_usd, 40) if r.outcome_usd else None,
                }
                for r in rows
            ]
            adjustments = []
            if "config_history" in tables:
                adjustments = [
                    {
                        "id": a.id,
                        "time": a.time.isoformat(),
                        "parameter": self.text(a.parameter, 32),
                        "value": self.text(
                            a.value if not isinstance(a.value, (list, dict)) else str(a.value), 120
                        ),
                        "classification": self.text(a.classification, 8),
                        "status": self.text(a.status, 12),
                        "suggestion_id": a.suggestion_id,
                        "previous": self.text(
                            a.previous if not isinstance(a.previous, (list, dict)) else str(a.previous), 120
                        )
                        if a.previous is not None
                        else None,
                        "reason": self.text(a.reason, 200),
                    }
                    for a in session.scalars(
                        select(AIConfigOverlay).order_by(AIConfigOverlay.id.desc()).limit(20)
                    ).all()
                ]
        decided = [i for i in items if i["kind"] == "entry"]
        summary = {
            "decisions": len(items),
            "ai_answers": sum(i["source"] in {"ai", "cache"} for i in items),
            "rule_fallbacks": sum(i["source"] == "rule_fallback" for i in items),
            "ai_blocked": sum(i["source"] == "ai_blocked" for i in items),
            "entries_approved": sum(i["final_action"] == "approved_to_risk_engine" for i in decided),
            "with_outcome": sum(i["outcome_usd"] is not None for i in items),
        }
        return {
            "meta": self.meta(),
            "items": items,
            "adjustments": adjustments,
            "summary": summary,
            "limit": limit,
            "offset": offset,
        }

    def logs(self, *, limit=50, offset=0):
        self.bounds(limit, offset)
        with self.database.session() as session:
            rows = session.scalars(
                select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(limit)
            ).all()
            return {
                "meta": self.meta(),
                "items": [
                    {
                        "id": r.id,
                        "time": r.time.isoformat(),
                        "action": self.text(r.action, 64),
                        "source": self.text(r.source, 64),
                    }
                    for r in rows
                ],
                "details_omitted_for_secret_safety": True,
                "limit": limit,
                "offset": offset,
            }
