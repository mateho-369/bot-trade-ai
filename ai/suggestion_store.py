"""Bounded immutable AI proposals. Local approval is NOT application or a broker permit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from ai.local_operator_guard import require_local_operator, require_stopped_flat
from ai.schemas import decimal_text
from core.database import Database
from core.local_operator import LocalOperator
from core.models import AISuggestion
from core.security import canonical_json, sanitize_text, sha256_json
from core.settings import Settings
from trading.risk_types import RuntimeProfile
from trading.types import Clock, TradingDisabled, valid_key

KINDS = {"reduce_risk", "rebalance_weights", "close_position"}


@dataclass(frozen=True, slots=True)
class StoredSuggestion:
    suggestion_id: int
    status: str
    kind: str
    payload_json: str
    expires_at: datetime

    def payload(self):
        return json.loads(self.payload_json)


class SuggestionStore:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile

    def _validate_parameters(self, kind, payload):
        try:
            if kind == "reduce_risk":
                if set(payload) != {"risk_percent"}:
                    raise ValueError
                value = decimal_text(payload["risk_percent"])
                if value is None or not 0 < value < self.settings.effective_risk_percent:
                    raise ValueError
            elif kind == "rebalance_weights":
                if set(payload) != {"weights"} or set(payload["weights"]) != set(
                    self.settings.strategy_weights
                ):
                    raise ValueError
                values = {k: decimal_text(v) for k, v in payload["weights"].items()}
                if (
                    any(v is None or not 0 <= v <= 1 for v in values.values())
                    or sum(values.values()) != 1
                    or values == self.settings.strategy_weights
                    or any(
                        abs(values[k] - old) > self.settings.max_strategy_weight_step
                        or old == 0
                        and values[k] != 0
                        for k, old in self.settings.strategy_weights.items()
                    )
                    or sum(v > 0 for v in values.values()) < self.settings.strategy_min_agreeing
                ):
                    raise ValueError
            elif kind == "ai_config_adjustment":
                from ai.config_adjuster import validate_value

                if set(payload) != {"parameter", "value"} or not isinstance(payload["parameter"], str):
                    raise ValueError
                if validate_value(self.settings, payload["parameter"], payload["value"]) != payload["value"]:
                    raise ValueError  # Only canonical, already-bounded values are stored.
            elif kind == "close_position":
                if set(payload) != {"trade_id", "position_identifier", "position_hash", "fraction"}:
                    raise ValueError
                if any(
                    type(payload[k]) is not int or payload[k] <= 0
                    for k in ("trade_id", "position_identifier")
                ):
                    raise ValueError
                valid_key(payload["position_hash"])
                value = decimal_text(payload["fraction"])
                if value is None or not 0 < value <= 1:
                    raise ValueError
            else:
                raise ValueError
            if len(canonical_json(payload).encode()) > 2048:
                raise ValueError
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise TradingDisabled("suggestion parameters exceed local-operator-safe bounded policy") from None

    def _load(self, session, suggestion_id):
        if type(suggestion_id) is not int or suggestion_id <= 0:
            raise TradingDisabled("positive suggestion ID required")
        row = session.get(AISuggestion, suggestion_id)
        if row is None:
            raise TradingDisabled("suggestion missing")
        try:
            data = dict(row.suggestion)
            digest = data.pop("digest")
            if (
                set(data)
                != {
                    "format",
                    "kind",
                    "parameters",
                    "created_at",
                    "expires_at",
                    "base_hash",
                    "code_hash",
                    "model_sha256",
                    "source",
                    "request_hash",
                    "data_evidence_hash",
                    "reason",
                    "risk_level",
                    "unique_key",
                }
                or digest != sha256_json(data)
                or data["format"] != "reflex-ai-suggestion-v1"
                or data["base_hash"] != row.based_on_config_hash
                or data["base_hash"] != self.settings.safety_fingerprint()
                or data["code_hash"] != self.profile.code_hash
                or data["model_sha256"] != self.profile.model_sha256
                or data["source"] != self.profile.data_source.value
                or data["kind"] != row.type
                or data["created_at"] != row.time.isoformat()
                or data["reason"] != row.reason
                or data["risk_level"] != row.risk_level
            ):
                raise ValueError
            valid_key(data["request_hash"])
            valid_key(data["unique_key"])
            if data["data_evidence_hash"] is not None:
                valid_key(data["data_evidence_hash"])
            expiry = datetime.fromisoformat(data["expires_at"])
            if not row.time < expiry <= row.time + timedelta(seconds=self.settings.ai_suggestion_ttl_seconds):
                raise ValueError
            self._validate_parameters(row.type, data["parameters"])
            if row.status in {"approved", "applied", "rejected"} and (
                row.decided_by != LocalOperator.current().operator_id
                or row.decided_at is None
                or not row.time <= row.decided_at < expiry
            ):
                raise ValueError
            return row, data, expiry
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise TradingDisabled("suggestion integrity/profile binding changed") from None

    def _result(self, row, data, expiry):
        return StoredSuggestion(row.id, row.status, row.type, canonical_json(data), expiry)

    def create(
        self,
        kind: str,
        parameters: dict,
        *,
        reason: str,
        request_hash: str,
        data_evidence_hash: str | None = None,
        ttl_seconds: int | None = None,
    ) -> StoredSuggestion:
        self._validate_parameters(kind, parameters)
        valid_key(request_hash)
        if data_evidence_hash is not None:
            valid_key(data_evidence_hash)
        ttl = ttl_seconds if ttl_seconds is not None else self.settings.ai_suggestion_ttl_seconds
        if type(ttl) is not int or not 1 <= ttl <= self.settings.ai_suggestion_ttl_seconds:
            raise TradingDisabled("invalid suggestion TTL")
        reason = sanitize_text(reason, self.database.secrets).strip()[:600]
        if not reason or any(ord(c) < 32 or ord(c) == 127 for c in reason):
            raise TradingDisabled("bounded reason required")
        now = self.clock.now()
        unique = sha256_json(
            {
                "kind": kind,
                "parameters": parameters,
                "request_hash": request_hash,
                "base_hash": self.settings.safety_fingerprint(),
                "code_hash": self.profile.code_hash,
                "model_sha256": self.profile.model_sha256,
                "data_evidence_hash": data_evidence_hash,
            }
        )
        with self.database.locked_session() as session:
            existing = session.scalars(
                select(AISuggestion).where(
                    AISuggestion.type == kind,
                    AISuggestion.based_on_config_hash == self.settings.safety_fingerprint(),
                    AISuggestion.suggestion["unique_key"].as_string() == unique,
                )
            ).all()
            if len(existing) > 1:
                raise TradingDisabled("ambiguous suggestion duplicate state")
            for row in existing:
                if row.suggestion.get("unique_key") == unique:
                    loaded, data, expiry = self._load(session, row.id)
                    return self._result(loaded, data, expiry)
            data = {
                "format": "reflex-ai-suggestion-v1",
                "kind": kind,
                "parameters": json.loads(canonical_json(parameters)),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
                "base_hash": self.settings.safety_fingerprint(),
                "code_hash": self.profile.code_hash,
                "model_sha256": self.profile.model_sha256,
                "source": self.profile.data_source.value,
                "request_hash": request_hash,
                "data_evidence_hash": data_evidence_hash,
                "reason": reason,
                "risk_level": "medium" if kind in {"rebalance_weights", "ai_config_adjustment"} else "low",
                "unique_key": unique,
            }
            row = AISuggestion(
                type=kind,
                suggestion={**data, "digest": sha256_json(data)},
                reason=reason,
                risk_level=data["risk_level"],
                status="pending",
                time=now,
                based_on_config_hash=self.settings.safety_fingerprint(),
            )
            session.add(row)
            session.flush()
            self.database.add_audit(
                session,
                "ai.suggestion_stored_pending",
                "ai",
                {"suggestion_id": row.id, "kind": kind, "local_operator_application_required": True},
            )
            return self._result(row, data, datetime.fromisoformat(data["expires_at"]))

    def get(self, suggestion_id: int):
        with self.database.locked_session() as session:
            row, data, expiry = self._load(session, suggestion_id)
            if row.status in {"pending", "approved"} and self.clock.now() >= expiry:
                row.status = "expired"
                self.database.add_audit(session, "ai.suggestion_expired", "ai", {"suggestion_id": row.id})
            return self._result(row, data, expiry)

    def list(self, *, status: str | None = None, limit: int = 25) -> tuple[StoredSuggestion, ...]:
        """Return bounded, integrity-checked summaries without writing decisions or settings."""
        allowed = {"pending", "approved", "rejected", "applied", "expired"}
        if status is not None and status not in allowed:
            raise TradingDisabled("unsupported proposal status")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise TradingDisabled("proposal listing limit is outside the local bound")
        query = select(AISuggestion).order_by(AISuggestion.time.desc(), AISuggestion.id.desc()).limit(limit)
        now = self.clock.now()
        results = []
        with self.database.session() as session:
            for candidate in session.scalars(query).all():
                row, data, expiry = self._load(session, candidate.id)
                effective_status = (
                    "expired" if row.status in {"pending", "approved"} and now >= expiry else row.status
                )
                if status is None or effective_status == status:
                    results.append(
                        StoredSuggestion(row.id, effective_status, row.type, canonical_json(data), expiry)
                    )
        return tuple(results)

    def decide(
        self,
        suggestion_id: int,
        *,
        operator: LocalOperator,
        approve: bool,
        expected_payload_hash: str | None = None,
    ):
        operator_id = require_local_operator(operator)
        if type(approve) is not bool:
            raise TradingDisabled("boolean local-operator decision required")
        if expected_payload_hash is not None:
            valid_key(expected_payload_hash)
        with self.database.locked_session() as session:
            row, data, expiry = self._load(session, suggestion_id)
            if expected_payload_hash is not None and sha256_json(data) != expected_payload_hash:
                raise TradingDisabled("proposal changed after local review; fetch it again")
            desired = "approved" if approve else "rejected"
            if row.status == desired:
                return self._result(row, data, expiry)
            if row.status != "pending" or self.clock.now() >= expiry:
                raise TradingDisabled("proposal is decided/expired; cannot change the original decision")
            row.status, row.decided_by, row.decided_at = desired, operator_id, self.clock.now()
            self.database.add_audit(
                session,
                "local_operator.suggestion_decided",
                "local_operator",
                {"suggestion_id": row.id, "decision": desired, "operator_id": operator_id},
            )
            return self._result(row, data, expiry)

    def apply(self, suggestion_id: int, *, operator: LocalOperator) -> Settings:
        """Return a new frozen settings projection for EXPLICIT stopped recomposition.

        Records the override for review; never edits .env, reloads/starts a runtime,
        resets a checkpoint/risk ledger or executes close_position proposals.
        """
        operator_id = require_local_operator(operator)
        with self.database.locked_session() as session:
            state = require_stopped_flat(session)
            if (
                state.settings_overrides
                and state.settings_overrides.get("new_hash") != self.settings.safety_fingerprint()
            ):
                raise TradingDisabled("recompose with the prior projected settings before another change")
            row, data, expiry = self._load(session, suggestion_id)
            if row.status != "approved" or row.decided_by != operator_id or self.clock.now() >= expiry:
                raise TradingDisabled("current unexpired local-operator-approved proposal required")
            if row.type == "close_position":
                raise TradingDisabled("position proposals need authenticated fresh ownership/close handler")
            if row.type == "ai_config_adjustment":
                raise TradingDisabled("AI config adjustments apply only to the bounded AI overlay")
            values = self.settings.model_dump(mode="python")
            values["project_root"] = self.settings.project_root
            if row.type == "reduce_risk":
                risk = Decimal(data["parameters"]["risk_percent"])
                patch = {
                    "max_risk_percent_per_trade": str(risk),
                    "live_max_risk_percent_per_trade": str(
                        min(risk, self.settings.live_max_risk_percent_per_trade)
                    ),
                }
            else:
                patch = {"strategy_weights": data["parameters"]["weights"]}
            values.update(patch)
            changed = Settings(_env_file=None, **values)  # Full validation, never unchecked model_copy.
            state.settings_overrides = {
                "format": "reflex-settings-projection-v1",
                "base_hash": data["base_hash"],
                "new_hash": changed.safety_fingerprint(),
                "changes": patch,
                "suggestion_id": row.id,
                "applied_at": self.clock.now().isoformat(),
            }
            state.revision += 1
            row.status, row.applied_at = "applied", self.clock.now()
            self.database.add_audit(
                session,
                "local_operator.suggestion_projected_stopped",
                "local_operator",
                {
                    "suggestion_id": row.id,
                    "operator_id": operator_id,
                    "new_hash": changed.safety_fingerprint(),
                    "runtime_started": False,
                    "stage_approvals_invalidated": True,
                },
            )
            return changed
