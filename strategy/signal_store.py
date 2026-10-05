"""Locked, single-bar publication/review. Historical inputs are immutable, never refreshed."""

from __future__ import annotations

import math
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy import select

from core.database import Database
from core.models import Signal
from core.security import canonical_json, sha256_json
from core.settings import OperatingMode, Settings
from strategy.base_strategy import (
    ROUTER_ID,
    SIGNAL_FORMAT,
    AIEntryReview,
    FeatureBundle,
    SignalResult,
    TechnicalDecision,
)
from strategy.news_filter import NewsFilter
from trading.risk_types import DecisionContext, NewsWindow, RuntimeProfile
from trading.types import BrokerError, Clock, Side, SourceKind, TradingDisabled


class SignalStore:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        self.news = NewsFilter(settings, clock, database, profile)

    def _binding(self, row: Signal):
        payload = row.features_json
        immutable = (
            "signal_format",
            "source",
            "logical_symbol",
            "symbol",
            "mode",
            "strategy",
            "timeframe",
            "observed_at",
            "bar_time",
            "bar_closed_at",
            "config_hash",
            "strategy_config_hash",
            "code_hash",
            "model_sha256",
            "history_hash",
            "symbol_info_hash",
            "stop_price",
            "atr",
            "bar_close_price",
            "feature_snapshot",
            "technical",
        )
        if not isinstance(payload, dict) or any(key not in payload for key in immutable):
            raise TradingDisabled("incomplete persisted signal proposal")
        if payload.get("proposal_hash") != sha256_json({key: payload[key] for key in immutable}):
            raise TradingDisabled("persisted signal proposal integrity changed")
        if (
            payload["symbol"] != row.symbol
            or payload["timeframe"] != row.timeframe
            or payload["strategy"] != row.strategy
            or payload["mode"] != row.mode
            or payload["config_hash"] != row.config_hash
            or payload["observed_at"] != row.time.isoformat()
            or payload["bar_time"] != row.bar_time.isoformat()
            or payload["technical"].get("direction") != row.direction
            or payload["technical"].get("score") != row.score
        ):
            raise TradingDisabled("persisted signal columns changed relative to proposal")
        if (
            row.mode != self.settings.mode.value
            or row.config_hash != self.settings.safety_fingerprint()
            or row.strategy != ROUTER_ID
            or row.timeframe != self.settings.primary_timeframe
            or payload.get("signal_format") != SIGNAL_FORMAT
            or payload.get("code_hash") != self.profile.code_hash
            or payload.get("model_sha256") != self.profile.model_sha256
            or payload.get("source") != self.profile.data_source.value
        ):
            raise TradingDisabled("signal source/code/model/configuration does not bind this runtime")

    def _historical_model_gate(self, payload, *, require_pass=True):
        """No probability DTO bypass: re-read the actual scoped registry/artifact and fixed snapshot."""
        if self.profile.data_source != SourceKind.HISTORICAL or not self.settings.model_filter_enabled:
            return None
        if self.settings.mode != OperatingMode.BACKTEST:
            raise TradingDisabled("historical learning approval is BACKTEST-only")
        from ai.feature_engineering import FEATURE_SCHEMA_HASH, FeatureEngineering
        from ai.model_registry import ModelRegistry

        vector = FeatureEngineering.from_snapshot(payload["feature_snapshot"], payload["technical"])
        probability, binding_digest = ModelRegistry(
            self.database, self.settings, self.clock, self.profile
        ).replay_inference(vector)
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise TradingDisabled("invalid_learning_probability")
        accepted = probability >= self.settings.model_min_probability
        if require_pass and not accepted:
            raise TradingDisabled("learning_filter_veto")
        return {
            "format": "reflex-replay-model-gate-v1",
            "proposal_hash": payload["proposal_hash"],
            "model_sha256": self.profile.model_sha256,
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
            "feature_vector_sha256": sha256_json(vector.to_dict()),
            "replay_binding_sha256": binding_digest,
            "source": self.profile.data_source.value,
            "code_hash": self.profile.code_hash,
            "policy_hash": self.settings.strategy_fingerprint(),
            "config_hash": self.settings.safety_fingerprint(),
            "threshold": self.settings.model_min_probability,
            "probability": probability,
            "accepted": accepted,
        }

    def approved_context(self, row: Signal) -> DecisionContext:
        """Verify finalization integrity without refreshing its time or authorization."""
        self._binding(row)
        payload = row.features_json
        try:
            model_gate = self._historical_model_gate(payload)
            context = DecisionContext.from_dict(payload["decision_context"])
            if model_gate is not None and (
                payload.get("model_gate") != model_gate
                or context.features.get("model_gate") != model_gate
                or payload.get("model_gate_digest") != sha256_json(model_gate)
            ):
                raise ValueError
            review = AIEntryReview.from_dict(payload["ai_review"])
            expected_risk = (
                review.risk_percent
                if review.risk_percent is not None
                and review.risk_percent < self.settings.effective_risk_percent
                else None
            )
            if (
                row.final_decision != "approved"
                or context.digest != payload["decision_digest"]
                or context.signal_id != row.id
                or context.source != self.profile.data_source
                or context.observed_at != row.time
                or context.bar_closed_at != features_closed_at(payload)
                or context.signal_score != row.score
                or context.ai_confidence != row.ai_score
                or review.confidence != row.ai_score
                or review.decision != "approve"
                or review.confidence < self.settings.ai_confidence_threshold
                or review.source != self.profile.data_source
                or review.code_hash != self.profile.code_hash
                or review.model_sha256 != self.profile.model_sha256
                or review.proposal_hash != payload["proposal_hash"]
                or review.news_hash != payload["news_hash"]
                or context.news.evidence_hash != payload["news_hash"]
                or context.features.get("review_digest") != review.digest
                or context.features.get("proposal_hash") != payload["proposal_hash"]
                or context.features.get("history_hash") != payload["history_hash"]
                or context.features.get("feature_snapshot") != payload["feature_snapshot"]
                or context.features.get("technical") != payload["technical"]
                or context.risk_percent != expected_risk
                or review.risk_percent is not None
                and review.risk_percent > self.settings.effective_risk_percent
                or expected_risk is not None
                and not self.settings.auto_reduce_risk
                or review.provider == "test"
                and self.profile.data_source != SourceKind.SYNTHETIC
                or review.provider == "replay"
                and not (
                    self.settings.mode == OperatingMode.BACKTEST
                    and self.profile.data_source == SourceKind.HISTORICAL
                    and (not self.settings.model_filter_enabled or model_gate is not None)
                )
            ):
                raise ValueError
            return context
        except (BrokerError, KeyError, TypeError, ValueError, ArithmeticError):
            raise TradingDisabled("stored reviewed signal finalization integrity changed") from None

    def _result(self, row: Signal) -> SignalResult:
        payload = row.features_json
        reasons = tuple(row.reason.split(",")) if row.reason else ()
        context = None
        if row.final_decision == "approved":
            context = self.approved_context(row)
        return SignalResult(
            row.id,
            row.final_decision,
            row.symbol,
            SourceKind(payload["source"]),
            Side(row.direction) if row.direction != "wait" else None,
            row.score,
            payload.get("proposal_hash"),
            Decimal(payload["stop_price"]) if payload.get("stop_price") else None,
            reasons,
            canonical_json(payload),
            context,
        )

    def _get(self, session, signal_id: int) -> Signal:
        if type(signal_id) is not int or signal_id <= 0:
            raise TradingDisabled("positive persisted signal ID is required")
        row = session.get(Signal, signal_id)
        if row is None:
            raise TradingDisabled("persisted signal is missing")
        self._binding(row)
        return row

    def get(self, signal_id: int) -> SignalResult:
        with self.database.session() as session:
            return self._result(self._get(session, signal_id))

    def record(
        self,
        features: FeatureBundle,
        technical: TechnicalDecision,
        *,
        stop_price: Decimal | None,
        vetoes: tuple[str, ...] = (),
    ) -> SignalResult:
        cfg, profile = self.settings, self.profile
        if features.source != profile.data_source:
            raise TradingDisabled("market analysis cannot relabel its provenance")
        reasons = vetoes or technical.reasons
        state = "rejected" if vetoes else "pending" if technical.side is not None else "wait"
        payload = {
            "signal_format": SIGNAL_FORMAT,
            "source": features.source.value,
            "logical_symbol": features.logical_symbol,
            "symbol": features.symbol,
            "mode": cfg.mode.value,
            "strategy": ROUTER_ID,
            "timeframe": cfg.primary_timeframe,
            "observed_at": features.observed_at.isoformat(),
            "bar_time": features.primary.bar_time.isoformat(),
            "bar_closed_at": features.primary.closed_at.isoformat(),
            "config_hash": cfg.safety_fingerprint(),
            "strategy_config_hash": cfg.strategy_fingerprint(),
            "code_hash": profile.code_hash,
            "model_sha256": profile.model_sha256,
            "history_hash": features.history_hash,
            "symbol_info_hash": sha256_json(
                {
                    key: value
                    for key, value in asdict(features.info).items()
                    if key not in {"tick_value_profit", "tick_value_loss", "visible"}
                }
            ),
            "stop_price": str(stop_price) if stop_price is not None else None,
            "atr": str(features.primary.value("atr")),
            "bar_close_price": str(features.primary.value("close")),
            "feature_snapshot": features.to_dict(),
            "technical": technical.to_dict(),
        }
        payload["proposal_hash"] = sha256_json(payload)
        if len(canonical_json(payload).encode()) > 16384:
            raise TradingDisabled("technical proposal exceeds 16 KiB")
        with self.database.locked_session() as session:
            old = session.scalar(
                select(Signal).where(
                    Signal.mode == cfg.mode.value,
                    Signal.symbol == features.symbol,
                    Signal.timeframe == cfg.primary_timeframe,
                    Signal.bar_time == features.primary.bar_time,
                    Signal.strategy == ROUTER_ID,
                    Signal.config_hash == cfg.safety_fingerprint(),
                )
            )
            if old is not None:
                binding = (
                    "signal_format",
                    "source",
                    "history_hash",
                    "symbol_info_hash",
                    "code_hash",
                    "model_sha256",
                )
                if any(old.features_json.get(key) != payload[key] for key in binding):
                    # A revised input cannot refresh confidence or create another opportunity.
                    old.final_decision, old.reason = "revoked", "history_or_profile_revision"
                    self.database.add_audit(
                        session, "signal.revised_input_revoked", "signals", {"signal_id": old.id}
                    )
                elif (
                    old.final_decision == "pending"
                    and (self.clock.now() - old.time).total_seconds() > cfg.order_max_age_seconds
                ):
                    old.final_decision, old.reason = "expired", "expired_review_window"
                    self.database.add_audit(
                        session, "signal.pending_expired", "signals", {"signal_id": old.id}
                    )
                self.database.add_audit(
                    session,
                    "signal.duplicate_observed",
                    "signals",
                    {"signal_id": old.id, "state": old.final_decision},
                )
                # An old profile cannot be returned as a usable decision under the new runtime.
                if any(
                    old.features_json.get(key) != payload[key]
                    for key in ("source", "code_hash", "model_sha256")
                ):
                    return SignalResult(
                        old.id,
                        "revoked",
                        features.symbol,
                        features.source,
                        None,
                        0,
                        None,
                        None,
                        ("history_or_profile_revision",),
                    )
                return self._result(old)
            row = Signal(
                time=features.observed_at,
                bar_time=features.primary.bar_time,
                mode=cfg.mode.value,
                symbol=features.symbol,
                timeframe=cfg.primary_timeframe,
                strategy=ROUTER_ID,
                direction=technical.side.value if technical.side else "wait",
                score=technical.score,
                ai_score=0,
                final_decision=state,
                reason=",".join(reasons),
                config_hash=cfg.safety_fingerprint(),
                features_json=payload,
            )
            session.add(row)
            session.flush()
            self.database.add_audit(
                session,
                "signal.published",
                "signals",
                {
                    "signal_id": row.id,
                    "symbol": row.symbol,
                    "direction": row.direction,
                    "state": state,
                    "score": row.score,
                    "source": features.source.value,
                    "proposal_hash": payload["proposal_hash"],
                    "reasons": list(reasons),
                },
            )
            return self._result(row)

    def finalize(self, signal_id: int, *, review: AIEntryReview | None, news: NewsWindow) -> SignalResult:
        cfg, now = self.settings, self.clock.now()
        with self.database.locked_session() as session:
            row = self._get(session, signal_id)
            if row.final_decision != "pending":
                self.database.add_audit(
                    session,
                    "signal.finalization_duplicate",
                    "signals",
                    {"signal_id": row.id, "state": row.final_decision},
                )
                return self._result(row)
            payload, reasons = dict(row.features_json), []
            model_gate = None
            if self.profile.data_source == SourceKind.HISTORICAL and cfg.model_filter_enabled:
                try:
                    model_gate = self._historical_model_gate(payload, require_pass=False)
                    if not model_gate["accepted"]:
                        reasons.append("learning_filter_veto_or_unavailable")
                    payload.update(model_gate=model_gate, model_gate_digest=sha256_json(model_gate))
                except (BrokerError, ValueError, TypeError, KeyError, ArithmeticError):
                    reasons.append("learning_filter_veto_or_unavailable")
            if not -2 <= (now - row.time).total_seconds() <= cfg.order_max_age_seconds:
                reasons.append("expired_review_window")
            news_result = self.news.evaluate(payload["logical_symbol"], news, session=session)
            reasons.extend(news_result.reasons)
            valid_review = isinstance(review, AIEntryReview)
            if not valid_review:
                reasons.append("ai_unavailable_or_invalid")
            else:
                if (
                    review.source != self.profile.data_source
                    or review.proposal_hash != payload["proposal_hash"]
                    or review.code_hash != self.profile.code_hash
                    or review.model_sha256 != self.profile.model_sha256
                    or not isinstance(news, NewsWindow)
                    or review.news_hash != news.evidence_hash
                    or not -2 <= (now - review.observed_at).total_seconds() <= cfg.order_max_age_seconds
                    or (review.observed_at - row.time).total_seconds() < -2
                    or review.provider == "test"
                    and self.profile.data_source != SourceKind.SYNTHETIC
                    or review.provider == "replay"
                    and not (
                        cfg.mode == OperatingMode.BACKTEST
                        and self.profile.data_source == SourceKind.HISTORICAL
                        and (not cfg.model_filter_enabled or model_gate is not None)
                    )
                ):
                    reasons.append("unbound_or_stale_ai_review")
                if review.decision != "approve":
                    reasons.append("ai_veto_or_wait")
                if review.confidence < cfg.ai_confidence_threshold:
                    reasons.append("low_ai_confidence")
                if review.risk_percent is not None:
                    if review.risk_percent > cfg.effective_risk_percent:
                        reasons.append("ai_risk_escalation")
                    elif review.risk_percent < cfg.effective_risk_percent and not cfg.auto_reduce_risk:
                        reasons.append("unapproved_auto_risk_change")
            if (
                row.score < cfg.min_signal_score
                or row.direction == "wait"
                or payload.get("stop_price") is None
            ):
                reasons.append("low_or_missing_technical_candidate")
            if reasons:
                row.final_decision = "expired" if "expired_review_window" in reasons else "rejected"
                row.features_json = payload
                row.reason, row.ai_score = (
                    ",".join(dict.fromkeys(reasons)),
                    review.confidence if valid_review else 0,
                )
            else:
                reduced = (
                    review.risk_percent
                    if review.risk_percent is not None and review.risk_percent < cfg.effective_risk_percent
                    else None
                )
                context = DecisionContext(
                    row.time,
                    features_closed_at(payload),
                    self.profile.data_source,
                    row.score,
                    review.confidence,
                    news,
                    row.id,
                    reduced,
                    {
                        "signal_format": SIGNAL_FORMAT,
                        "proposal_hash": payload["proposal_hash"],
                        "history_hash": payload["history_hash"],
                        "review_digest": review.digest,
                        "feature_snapshot": payload["feature_snapshot"],
                        "technical": payload["technical"],
                        **({"model_gate": model_gate} if model_gate is not None else {}),
                    },
                )
                if model_gate is not None:
                    payload.update(model_gate=model_gate, model_gate_digest=sha256_json(model_gate))
                payload.update(
                    news_hash=news.evidence_hash,
                    ai_review=review.to_dict(),
                    decision_context=context.to_dict(),
                    decision_digest=context.digest,
                )
                row.features_json, row.ai_score, row.final_decision, row.reason = (
                    payload,
                    review.confidence,
                    "approved",
                    "technical_ai_news_approved",
                )
            self.database.add_audit(
                session,
                "signal.reviewed",
                "signals",
                {
                    "signal_id": row.id,
                    "state": row.final_decision,
                    "ai_score": row.ai_score,
                    "reasons": row.reason.split(","),
                    "context_digest": payload.get("decision_digest"),
                },
            )
            return self._result(row)

    def revoke(self, signal_id: int) -> SignalResult:
        """Trusted internal invalidation; never expose without owner auth in Part 9."""
        with self.database.locked_session() as session:
            row = self._get(session, signal_id)
            row.final_decision, row.reason = "revoked", "explicit_signal_invalidation"
            self.database.add_audit(session, "signal.revoked", "signals", {"signal_id": row.id})
            return self._result(row)

    def data_failure(self, symbol: str, reason: str) -> SignalResult:
        self.database.audit(
            "signal.data_unavailable",
            "signals",
            {"symbol": symbol, "reason": reason, "source": self.profile.data_source.value},
        )
        return SignalResult(None, "blocked", symbol, self.profile.data_source, None, 0, None, None, (reason,))


def features_closed_at(payload: dict):
    from datetime import datetime

    return datetime.fromisoformat(payload["bar_closed_at"])
