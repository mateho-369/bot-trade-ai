"""Single-use confirmation journal and durable idempotency saga (schema 2).

owner_action = challenge; owner_action_run = deterministic unique run key.
These NEVER issue/consume StageGate live_session approvals. Only nonce hashes
are stored. SQL staging precedes effects; pending/uncertain runs are not retried.
Remote effects and SQL completion cannot form one atomic transaction.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from app.owner_identity import OwnerIdentity, OwnerInterfaceError
from core.models import AuditLog, BotState, OwnerApproval
from core.security import sha256_json

CONFIRMED_ACTIONS = {"resume", "close_position", "close_all", "approve_suggestion"}
DIRECT_ACTIONS = {
    "pause",
    "kill",
    "reject_suggestion",
    "ai_reset",
    "ai_fallback_block",
    "ai_fallback_technical",
    "ack_alerts",
    "ack_alert",
}
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
OUTCOMES = {"completed", "rejected", "uncertain"}


@dataclass(frozen=True, slots=True)
class ActionScope:
    interface_session_id: str
    runtime_session_id: str | None
    account_key: str
    config_hash: str
    code_hash: str
    model_sha256: str | None
    data_source: str
    mode: str

    def data(self):
        return asdict(self)

    @property
    def digest(self):
        return sha256_json(self.data())


@dataclass(frozen=True, slots=True)
class StagedAction:
    run_id: str
    action: str
    parameters: dict
    binding: dict
    request_id: str


class OwnerActionStore:
    def __init__(self, database, settings, clock):
        self.database, self.settings, self.clock = database, settings, clock

    @staticmethod
    def request_id(value):
        try:
            if type(value) is not str or len(value) != 36 or str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise OwnerInterfaceError("invalid_request_id", 422) from None
        return value

    @staticmethod
    def nonce_hash(token):
        if type(token) is not str or not TOKEN.fullmatch(token):
            raise OwnerInterfaceError("confirmation_invalid", 409)
        return hashlib.sha256(("owner-confirm-v1:" + token).encode()).hexdigest()

    def _key(self, actor, request_id):
        self.request_id(request_id)
        return sha256_json({"format": "owner-run-key-v1", "owner": actor.owner_id, "request_id": request_id})

    def _wire_hash(self, actor, action, parameters):
        return sha256_json(
            {
                "owner": actor.owner_id,
                "transport": actor.transport,
                "action": action,
                "parameters": parameters,
            }
        )

    def _audit(self, session, action, details):
        row = self.database.add_audit(session, action, "owner_interface", details)
        row.time = self.clock.now()
        session.flush()
        return row

    @staticmethod
    def _record(session, row, action):
        records = [session.get(AuditLog, i) for i in row.evidence_ids]
        matches = [
            r
            for r in records
            if r is not None
            and r.action == action
            and r.details.get("approval_id", r.details.get("run_id")) == row.id
        ]
        if len(matches) != 1:
            raise OwnerInterfaceError("action_journal_invalid", 409)
        return matches[0].details

    def _existing(self, session, actor, scope, action, parameters, request_id):
        row = session.scalar(
            select(OwnerApproval).where(OwnerApproval.nonce_hash == self._key(actor, request_id))
        )
        if row is None:
            return None
        record = self._record(session, row, "owner.action_staged")
        if (
            row.purpose != "owner_action_run"
            or row.owner_id != actor.owner_id
            or row.request_hash != self._wire_hash(actor, action, parameters)
            or {
                k: v
                for k, v in record.get("scope", {}).items()
                if k not in {"interface_session_id", "runtime_session_id"}
            }
            != {
                k: v
                for k, v in scope.data().items()
                if k not in {"interface_session_id", "runtime_session_id"}
            }
            or row.config_hash != scope.config_hash
            or row.account_key != scope.account_key
        ):
            raise OwnerInterfaceError("idempotency_payload_conflict", 409)
        outcomes = [session.get(AuditLog, i) for i in row.evidence_ids[1:]]
        outcomes = [
            r
            for r in outcomes
            if r is not None
            and r.action in {"owner.action_completed", "owner.action_rejected", "owner.action_uncertain"}
            and r.details.get("run_id") == row.id
        ]
        if len(outcomes) == 1:
            return {
                **outcomes[0].details["outcome"],
                "replayed": True,
                "historical_runtime": record.get("scope") != scope.data(),
            }
        if outcomes:
            raise OwnerInterfaceError("action_journal_invalid", 409)
        raise OwnerInterfaceError("action_pending_or_uncertain_do_not_retry", 409)

    def cached(self, actor: OwnerIdentity, scope: ActionScope, action, parameters, request_id):
        actor.require(self.settings, self.clock)
        with self.database.locked_session() as session:
            return self._existing(session, actor, scope, action, parameters, request_id)

    def _expire(self, session, owner_id):
        rows = session.scalars(
            select(OwnerApproval)
            .where(
                OwnerApproval.purpose == "owner_action",
                OwnerApproval.owner_id == owner_id,
                OwnerApproval.status == "pending",
                OwnerApproval.expires_at <= self.clock.now(),
            )
            .order_by(OwnerApproval.time)
            .limit(129)
        ).all()
        for row in rows:
            row.status = "expired"
        # Bound storage operations; old abandoned challenges confer no permission.
        if len(rows) > 128:
            raise OwnerInterfaceError("confirmation_backlog_requires_review", 409)

    def prepare(self, actor, scope, action, parameters, request_id, binding, summary):
        actor.require(self.settings, self.clock)
        if action not in CONFIRMED_ACTIONS:
            raise OwnerInterfaceError("action_not_confirmable", 422)
        with self.database.locked_session() as session:
            cached = self._existing(session, actor, scope, action, parameters, request_id)
            if cached is not None:
                return cached
            self._expire(session, actor.owner_id)
            pending = session.scalars(
                select(OwnerApproval)
                .where(
                    OwnerApproval.purpose == "owner_action",
                    OwnerApproval.owner_id == actor.owner_id,
                    OwnerApproval.status == "pending",
                    OwnerApproval.expires_at > self.clock.now(),
                )
                .limit(self.settings.owner_max_pending_confirmations)
            ).all()
            if len(pending) >= self.settings.owner_max_pending_confirmations:
                raise OwnerInterfaceError("too_many_pending_confirmations", 429)
            token = secrets.token_urlsafe(32)
            expires = min(
                actor.expires_at,
                self.clock.now() + timedelta(seconds=self.settings.owner_confirmation_ttl_seconds),
            )
            row = OwnerApproval(
                purpose="owner_action",
                request_hash=self._wire_hash(actor, action, parameters),
                nonce_hash=self.nonce_hash(token),
                time=self.clock.now(),
                expires_at=expires,
                status="pending",
                owner_id=actor.owner_id,
                session_id=scope.interface_session_id,
                account_key=scope.account_key,
                config_hash=scope.config_hash,
                evidence_ids=[],
            )
            session.add(row)
            session.flush()
            record = self._audit(
                session,
                "owner.confirmation_prepared",
                {
                    "approval_id": row.id,
                    "scope": scope.data(),
                    "transport": actor.transport,
                    "credential_hash": actor.credential_hash,
                    "action": action,
                    "parameters": parameters,
                    "request_id": request_id,
                    "binding": binding,
                    "summary": summary,
                    "expires_at": expires.isoformat(),
                },
            )
            row.evidence_ids = [record.id]
            return {
                "status": "confirmation_required",
                "request_id": request_id,
                "action": action,
                "confirmation_token": token,
                "expires_at": expires.isoformat(),
                "summary": summary,
                "effect_applied": False,
                "replayed": False,
            }

    def describe(self, actor, scope, token):
        """Only an authenticated same-transport owner can resolve a button token."""
        actor.require(self.settings, self.clock)
        with self.database.locked_session() as session:
            row = session.scalar(
                select(OwnerApproval).where(OwnerApproval.nonce_hash == self.nonce_hash(token))
            )
            if row is None or row.purpose != "owner_action":
                raise OwnerInterfaceError("confirmation_invalid", 409)
            record = self._record(session, row, "owner.confirmation_prepared")
            self._validate(row, record, actor, scope)
            return {
                "action": record["action"],
                "parameters": record["parameters"],
                "request_id": record["request_id"],
            }

    def _validate(self, row, record, actor, scope):
        if (
            row.owner_id != actor.owner_id
            or row.config_hash != scope.config_hash
            or row.session_id != scope.interface_session_id
            or row.account_key != scope.account_key
            or record.get("scope") != scope.data()
            or record.get("transport") != actor.transport
            or record.get("credential_hash") != actor.credential_hash
        ):
            raise OwnerInterfaceError("confirmation_scope_changed", 409)
        if not row.time <= self.clock.now() < row.expires_at:
            raise OwnerInterfaceError("confirmation_expired", 409)

    def cancel(self, actor, scope, token):
        actor.require(self.settings, self.clock)
        with self.database.locked_session() as session:
            row = session.scalar(
                select(OwnerApproval).where(OwnerApproval.nonce_hash == self.nonce_hash(token))
            )
            if row is None or row.purpose != "owner_action":
                raise OwnerInterfaceError("confirmation_invalid", 409)
            record = self._record(session, row, "owner.confirmation_prepared")
            self._validate(row, record, actor, scope)
            if row.status != "pending":
                raise OwnerInterfaceError("confirmation_already_decided", 409)
            row.status, row.decided_at = "rejected", self.clock.now()
            self._audit(session, "owner.confirmation_canceled", {"approval_id": row.id})
        return {"status": "canceled", "effect_applied": False}

    def stage(self, actor, scope, action, parameters, request_id, token=None):
        actor.require(self.settings, self.clock)
        if action not in CONFIRMED_ACTIONS | DIRECT_ACTIONS:
            raise OwnerInterfaceError("action_not_supported", 422)
        with self.database.locked_session() as session:
            cached = self._existing(session, actor, scope, action, parameters, request_id)
            if cached is not None:
                return cached
            binding = {}
            row = None
            if action in CONFIRMED_ACTIONS:
                row = session.scalar(
                    select(OwnerApproval).where(OwnerApproval.nonce_hash == self.nonce_hash(token))
                )
                if row is None or row.purpose != "owner_action":
                    raise OwnerInterfaceError("confirmation_invalid", 409)
                record = self._record(session, row, "owner.confirmation_prepared")
                self._validate(row, record, actor, scope)
                if (
                    row.status != "pending"
                    or record["action"] != action
                    or record["parameters"] != parameters
                    or record["request_id"] != request_id
                    or row.request_hash != self._wire_hash(actor, action, parameters)
                ):
                    raise OwnerInterfaceError("confirmation_payload_changed_or_consumed", 409)
                binding = record["binding"]
                state = session.get(BotState, 1)
                if action == "resume" and (
                    state is None
                    or state.revision != binding.get("state_revision")
                    or state.session_id != scope.runtime_session_id
                ):
                    raise OwnerInterfaceError("resume_state_changed_prepare_again", 409)
                row.status, row.decided_at = "consumed", self.clock.now()
            elif token is not None:
                raise OwnerInterfaceError("confirmation_not_expected", 422)
            run = OwnerApproval(
                purpose="owner_action_run",
                request_hash=self._wire_hash(actor, action, parameters),
                nonce_hash=self._key(actor, request_id),
                time=self.clock.now(),
                expires_at=actor.expires_at,
                status="consumed",
                owner_id=actor.owner_id,
                session_id=scope.interface_session_id,
                account_key=scope.account_key,
                config_hash=scope.config_hash,
                decided_at=self.clock.now(),
                evidence_ids=[],
            )
            session.add(run)
            session.flush()
            event = self._audit(
                session,
                "owner.action_staged",
                {
                    "run_id": run.id,
                    "confirmation_id": row.id if row else None,
                    "scope": scope.data(),
                    "action": action,
                    "parameters": parameters,
                    "request_id": request_id,
                    "binding": binding,
                    "remote_effect_may_follow": True,
                },
            )
            run.evidence_ids = [event.id]
            return StagedAction(run.id, action, parameters, binding, request_id)

    def finish(self, staged: StagedAction, outcome: dict):
        if outcome.get("status") not in OUTCOMES:
            raise ValueError("fixed terminal action outcome required")
        with self.database.locked_session() as session:
            row = session.get(OwnerApproval, staged.run_id)
            if row is None or row.purpose != "owner_action_run" or len(row.evidence_ids) != 1:
                raise OwnerInterfaceError("action_outcome_already_recorded_or_missing", 409)
            completed = {
                **outcome,
                "request_id": staged.request_id,
                "action": staged.action,
                "replayed": False,
            }
            event = self._audit(
                session, "owner.action_" + outcome["status"], {"run_id": row.id, "outcome": completed}
            )
            row.evidence_ids = [*row.evidence_ids, event.id]
            row.status = "rejected" if outcome["status"] == "rejected" else "consumed"
            return completed
