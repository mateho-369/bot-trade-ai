"""Persistent single-runtime lease and owner controls; transport auth lives in Part 9.

owner_id MUST come from verified Telegram/initData identity, never request JSON.
Synthetic diagnostics explicitly configure a test owner; no default owner exists.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from core.database import Database
from core.models import BotState, OrderIntent, RiskEvent, RiskState
from core.settings import Settings
from trading.types import Clock, TradingDisabled

TERMINAL_INTENT_STATES = {"reconciled", "rejected", "canceled"}


class RuntimeControl:
    def __init__(self, database: Database, settings: Settings, clock: Clock):
        self.database, self.settings, self.clock = database, settings, clock
        self.session_id: str | None = None

    def claim(self) -> str:
        self.database.verify_schema()
        now = self.clock.now()
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("persistent control state is missing")
            if (
                state.session_id
                and state.heartbeat
                and (now - state.heartbeat).total_seconds() < self.settings.runtime_lease_seconds
            ):
                raise TradingDisabled("another runtime still owns the persisted lease")
            state.session_id = str(uuid4())
            state.desired_state = "killed" if state.kill_switch_active else "paused"
            state.heartbeat, state.last_config_hash = now, self.settings.safety_fingerprint()
            state.revision += 1
            # Preserve last_error, kill, drawdown, daily limits and pending intents.
            self.database.add_audit(
                session, "runtime.claimed_paused", "runtime", {"session_id": state.session_id}
            )
            self.session_id = state.session_id
        return self.session_id

    def check(self, state: BotState | None) -> None:
        if (
            state is None
            or self.session_id is None
            or state.session_id != self.session_id
            or state.last_config_hash != self.settings.safety_fingerprint()
            or state.heartbeat is None
            or not -2
            <= (self.clock.now() - state.heartbeat).total_seconds()
            < self.settings.runtime_lease_seconds
        ):
            raise TradingDisabled("runtime lease/session/configuration is absent or expired")

    def heartbeat(self) -> None:
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            state.heartbeat = self.clock.now()
            state.revision += 1

    def release(self) -> None:
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state and state.session_id == self.session_id:
                state.desired_state = "killed" if state.kill_switch_active else "paused"
                state.heartbeat, state.session_id = None, None
                state.revision += 1
                self.database.add_audit(session, "runtime.released", "runtime", {})
        self.session_id = None

    def pause_for_shutdown(self) -> None:
        """Trusted runtime downward-only seam; no owner/reset/resume authority."""
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None or self.session_id is None or state.session_id != self.session_id:
                raise TradingDisabled("shutdown may pause only its own runtime session")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(session, "runtime.shutdown_requested", "runtime", {})

    def _owner(self, owner_id: int) -> None:
        if (
            self.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("authenticated configured owner required")

    def _fresh_risk(self, risk: RiskState | None) -> bool:
        try:
            return (
                risk is not None
                and 0
                <= (
                    self.clock.now() - datetime.fromisoformat(risk.metadata_json["last_observed_at"])
                ).total_seconds()
                <= self.settings.risk_observation_max_age_seconds
            )
        except (KeyError, TypeError, ValueError):
            return False

    def pause(self, owner_id: int) -> None:
        self._owner(owner_id)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(session, "owner.paused", "owner", {"owner_id": owner_id})

    def kill(self, owner_id: int) -> None:
        self._owner(owner_id)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            state.kill_switch_active, state.desired_state = True, "killed"
            state.revision += 1
            self.database.add_audit(
                session,
                "owner.killed",
                "owner",
                {"owner_id": owner_id, "protective_management_remains_enabled": True},
            )

    def resume(self, owner_id: int, *, account_key: str, expected_revision: int | None = None) -> None:
        self._owner(owner_id)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise TradingDisabled("actual nonnegative expected control revision required")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            # Compare INSIDE the same serialized transaction as changing desired_state.
            # A later pause/kill/heartbeat cannot be undone by an older confirmation.
            if expected_revision is not None and state.revision != expected_revision:
                raise TradingDisabled("control changed after confirmation; prepare a fresh resume")
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            unsettled = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
            ).all()
            # Unsent staged intents must also be canceled/expired explicitly before resume.
            if (
                state.kill_switch_active
                or state.last_error
                or risk is None
                or not risk.metadata_json.get("baseline_verified")
                or not self._fresh_risk(risk)
                or risk.metadata_json.get("observation_gap")
                or risk.metadata_json.get("unclassified_cash_flow")
                or risk.metadata_json.get("unexplained_balance_change")
                or not risk.metadata_json.get("balance_continuity_verified")
                or risk.drawdown_latched
                or risk.daily_loss_latched
                or unsettled
            ):
                raise TradingDisabled("kill/loss/recovery/baseline/unsettled-intent gate prevents resume")
            state.desired_state = "running"
            state.revision += 1
            self.database.add_audit(
                session, "owner.resumed_entries", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def acknowledge_recovery(
        self, owner_id: int, *, account_key: str, broker_writes_quarantined: bool
    ) -> None:
        self._owner(owner_id)
        if broker_writes_quarantined:
            raise TradingDisabled("fresh reconciled broker runtime required; reconnect is insufficient")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            unresolved = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if unresolved or state.kill_switch_active:
                raise TradingDisabled("recovery cannot erase unresolved executions or a kill latch")
            state.last_error, state.desired_state = None, "paused"
            state.revision += 1
            self.database.add_audit(
                session, "owner.recovery_reviewed", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def auto_pause(self, reason: str, *, account_key: str = "unbound") -> bool:
        """Pause NEW entries after a CRITICAL alert (Alert Center). Not a halt: it never sets
        ``last_error``, never clears kill/loss latches and never touches positions; the owner
        resumes with the normal fully-gated /resume. Returns True when the state changed."""
        if reason not in {"critical_alert"}:
            raise ValueError("unknown auto-pause reason identifier")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if state.desired_state != "running":
                return False
            state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(
                session, "runtime.auto_paused", "runtime", {"reason": reason, "account": account_key}
            )
            return True

    def halt(self, reason: str, *, account_key: str = "unbound") -> None:
        # Fixed identifiers only; never accept provider/SDK error bodies here.
        if reason not in {
            "unknown_execution",
            "broker_unstable",
            "ledger_mismatch",
            "persistence_failure",
            "risk_observation_gap",
        }:
            raise ValueError("unknown halt reason identifier")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.last_error = reason
            state.revision += 1
            session.add(
                RiskEvent(
                    time=self.clock.now(),
                    event=reason,
                    details={"session_id": self.session_id},
                    mode=self.settings.mode.value,
                    account_key=account_key,
                )
            )
            self.database.add_audit(
                session, "runtime.halted", "runtime", {"reason": reason, "account": account_key}
            )

    def review_flat_baseline(self, owner_id: int, *, account_key: str, confirm: str) -> None:
        """Explicit review of legacy/missed observations, ONLY with a flat proven ledger.

        Cannot reset a daily/drawdown latch or silently reconstruct unknown peaks.
        """
        self._owner(owner_id)
        if confirm != "REVIEW_SAMPLED_BASELINE":
            raise TradingDisabled("explicit sampled-baseline acknowledgement required")
        with self.database.locked_session() as session:
            self.check(session.get(BotState, 1))
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            from core.models import Trade

            active = session.scalar(
                select(Trade.id)
                .where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status.in_(("open", "unknown")),
                )
                .limit(1)
            )
            pending = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if (
                risk is None
                or active
                or pending
                or risk.daily_loss_latched
                or risk.drawdown_latched
                or risk.metadata_json.get("observed_position_count", -1) != 0
            ):
                raise TradingDisabled("baseline review needs flat/reconciled exposure and no loss latch")
            meta = dict(risk.metadata_json)
            complete = meta.get("history_complete_through")
            if (
                not complete
                or not 0
                <= (self.clock.now() - datetime.fromisoformat(complete)).total_seconds()
                <= self.settings.risk_observation_max_age_seconds
                or meta.get("unclassified_cash_flow")
                or not meta.get("balance_continuity_verified")
            ):
                raise TradingDisabled("fresh complete journal and known cash flows required for review")
            meta.update(
                baseline_verified=True,
                observation_gap=False,
                unexplained_balance_change=False,
                migration_review_required=False,
                sampled_peak_only=True,
                baseline_reviewed_at=self.clock.now().isoformat(),
            )
            risk.metadata_json = meta
            self.database.add_audit(
                session,
                "owner.sampled_baseline_reviewed",
                "owner",
                {"owner_id": owner_id, "account": account_key, "latches_reset": False},
            )

    def reset_kill(
        self, owner_id: int, *, account_key: str, confirm: str, broker_writes_quarantined: bool
    ) -> None:
        self._owner(owner_id)
        if confirm != "RESET_KILL_AND_KEEP_PAUSED" or broker_writes_quarantined:
            raise TradingDisabled("explicit fresh-runtime kill-reset review required")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            risk = session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key, RiskState.mode == self.settings.mode.value
                )
            )
            unresolved = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            if (
                state.last_error
                or risk is None
                or not risk.metadata_json.get("baseline_verified")
                or not self._fresh_risk(risk)
                or unresolved
                or risk.daily_loss_latched
                or risk.drawdown_latched
                or risk.metadata_json.get("observation_gap")
                or risk.metadata_json.get("unclassified_cash_flow")
                or risk.metadata_json.get("unexplained_balance_change")
                or not risk.metadata_json.get("balance_continuity_verified")
            ):
                raise TradingDisabled("kill reset cannot override risk/unknown-execution gates")
            state.kill_switch_active, state.desired_state = False, "paused"
            state.revision += 1
            self.database.add_audit(
                session, "owner.kill_reset_paused", "owner", {"owner_id": owner_id, "account": account_key}
            )

    def cancel_expired_unsent(self) -> int:
        count = 0
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            rows = session.scalars(
                select(OrderIntent).where(
                    OrderIntent.state == "prepared", OrderIntent.expires_at <= self.clock.now()
                )
            ).all()
            for row in rows:
                if row.request.get("authorized") is not True:
                    row.state = "canceled"
                    count += 1
            if count:
                self.database.add_audit(session, "intent.unsent_expired", "runtime", {"count": count})
        return count
