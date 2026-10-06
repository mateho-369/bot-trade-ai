"""Persistent single-runtime lease and shared fail-closed local/automatic controls."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

from core.database import Database
from core.local_operator import LocalOperator
from core.models import AuditLog, BotState, OrderIntent, RiskEvent, RiskState
from core.settings import Settings
from trading.types import Clock, TradingDisabled

TERMINAL_INTENT_STATES = {"reconciled", "rejected", "canceled"}


def _fresh_risk(risk: RiskState | None, settings: Settings, clock: Clock) -> bool:
    if risk is None:
        return False
    try:
        observed = datetime.fromisoformat(risk.metadata_json["last_observed_at"])
        return 0 <= (clock.now() - observed).total_seconds() <= settings.risk_observation_max_age_seconds
    except (KeyError, TypeError, ValueError):
        return False


def _autonomous_account_verified(risk: RiskState, settings: Settings) -> bool:
    metadata = risk.metadata_json
    kind, source = metadata.get("account_kind"), metadata.get("account_source")
    if settings.mt5_backend == "real":
        # Real terminal initialization verifies DEMO mode even when a paper ledger wraps it.
        return (kind == "demo" and source in {"mt5", "test_sdk"}) or (
            kind == "simulated" and source == "paper" and settings.paper_trading
        )
    return kind == "simulated" and source in {"synthetic", "test_sdk"}


def evaluate_resume_gates(session, settings: Settings, clock: Clock, *, account_key: str, state: BotState):
    """Shared fail-closed resume contract for local resume and autonomous recovery."""
    risk = session.scalar(
        select(RiskState).where(
            RiskState.account_key == account_key,
            RiskState.mode == settings.mode.value,
        )
    )
    unsettled = session.scalar(
        select(OrderIntent.id)
        .where(
            OrderIntent.account_key == account_key,
            OrderIntent.mode == settings.mode.value,
            OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
        )
        .limit(1)
    )
    reasons = []
    if state.kill_switch_active:
        reasons.append("kill_switch")
    if state.last_error:
        reasons.append("designated_halt")
    if risk is None:
        reasons.append("risk_baseline_missing")
    else:
        metadata = risk.metadata_json
        if not metadata.get("baseline_verified"):
            reasons.append("baseline_unverified")
        if not _fresh_risk(risk, settings, clock):
            reasons.append("risk_observation_stale")
        if (
            metadata.get("observation_gap")
            or metadata.get("unclassified_cash_flow")
            or metadata.get("unexplained_balance_change")
            or not metadata.get("balance_continuity_verified")
        ):
            reasons.append("risk_continuity_unverified")
        if risk.daily_loss_latched:
            reasons.append("daily_loss_latched")
        if risk.drawdown_latched:
            reasons.append("drawdown_latched")
        if settings.autonomous_demo and not _autonomous_account_verified(risk, settings):
            reasons.append("demo_account_unverified")
    if unsettled:
        reasons.append("unsettled_intent")
    return tuple(reasons)


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
        """Trusted runtime downward-only seam; no local-operator/reset/resume authority."""
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None or self.session_id is None or state.session_id != self.session_id:
                raise TradingDisabled("shutdown may pause only its own runtime session")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(session, "runtime.shutdown_requested", "runtime", {})

    @staticmethod
    def _local_operator(operator: LocalOperator) -> None:
        if not isinstance(operator, LocalOperator):
            raise TradingDisabled("local operator required")
        operator.require_current()

    def _fresh_risk(self, risk: RiskState | None) -> bool:
        return _fresh_risk(risk, self.settings, self.clock)

    def pause(self, operator: LocalOperator) -> None:
        self._local_operator(operator)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            if not state.kill_switch_active:
                state.desired_state = "paused"
            state.revision += 1
            self.database.add_audit(
                session, "runtime.local_paused", "local_operator", {"process_id": operator.process_id}
            )

    def kill(self, operator: LocalOperator) -> None:
        self._local_operator(operator)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None:
                raise TradingDisabled("missing control state")
            state.kill_switch_active, state.desired_state = True, "killed"
            state.revision += 1
            self.database.add_audit(
                session,
                "runtime.local_killed",
                "local_operator",
                {"process_id": operator.process_id, "protective_management_remains_enabled": True},
            )

    def resume(
        self,
        operator: LocalOperator,
        *,
        account_key: str,
        expected_revision: int | None = None,
    ) -> None:
        self._local_operator(operator)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise TradingDisabled("actual nonnegative expected control revision required")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            # Compare INSIDE the same serialized transaction as changing desired_state.
            # A later pause/kill/heartbeat cannot be undone by an older confirmation.
            if expected_revision is not None and state.revision != expected_revision:
                raise TradingDisabled("control changed after confirmation; prepare a fresh resume")
            if state.desired_state != "paused":
                raise TradingDisabled("only a paused runtime can be resumed")
            reasons = evaluate_resume_gates(
                session, self.settings, self.clock, account_key=account_key, state=state
            )
            if reasons:
                raise TradingDisabled("resume gates block entry: " + ", ".join(reasons))
            state.desired_state = "running"
            state.revision += 1
            self.database.add_audit(
                session,
                "runtime.local_resumed",
                "local_operator",
                {"process_id": operator.process_id, "account": account_key},
            )

    def auto_resume(
        self,
        *,
        account_key: str,
        components_ready: bool,
        ai_healthy: bool,
        trigger: str,
    ) -> bool:
        """Run the same gates for startup/retry and recoverable CRITICAL auto-pauses."""
        if not self.settings.autonomous_demo or not components_ready or not ai_healthy:
            return False
        if trigger not in {"startup", "news_ready", "signals_ready", "ai_ready", "retry"}:
            raise ValueError("unknown autonomous resume trigger")
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            self.check(state)
            if state.desired_state != "paused":
                return False
            if evaluate_resume_gates(
                session, self.settings, self.clock, account_key=account_key, state=state
            ):
                return False

            local_pause_actions = (
                "runtime.local_paused",
                "runtime.local_killed",
                "runtime.local_kill_reset_paused",
                "runtime.local_recovery_reviewed",
                "runtime.local_sampled_baseline_reviewed",
            )
            latest_local_pause = (
                session.scalar(select(func.max(AuditLog.id)).where(AuditLog.action.in_(local_pause_actions)))
                or 0
            )
            latest_local_resume = (
                session.scalar(
                    select(func.max(AuditLog.id)).where(AuditLog.action == "runtime.local_resumed")
                )
                or 0
            )
            # Aggregate across the complete audit history: unrelated/high-volume events cannot hide
            # a newer local pause or a reviewed action that intentionally leaves entries paused.
            if latest_local_pause > latest_local_resume:
                return False

            now = self.clock.now()
            critical_pause = session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.action == "runtime.auto_paused",
                    AuditLog.details["reason"].as_string() == "critical_alert",
                    AuditLog.details["account"].as_string() == account_key,
                )
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
            last_auto_resume = session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.action == "runtime.auto_resumed",
                    AuditLog.details["account"].as_string() == account_key,
                )
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
            recovering_critical = critical_pause is not None and (
                last_auto_resume is None or critical_pause.id > last_auto_resume.id
            )
            if recovering_critical and (now - critical_pause.time).total_seconds() < 15 * 60:
                return False

            day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            resumes_today = session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "runtime.auto_resumed",
                    AuditLog.time >= day_start,
                    AuditLog.details["account"].as_string() == account_key,
                )
            ).all()
            if len(resumes_today) >= self.settings.auto_resume_max_per_day:
                return False

            state.desired_state = "running"
            state.revision += 1
            audit = self.database.add_audit(
                session,
                "runtime.auto_resumed",
                "runtime",
                {
                    "session_id": self.session_id,
                    "account": account_key,
                    "trigger": trigger,
                    "reason": "critical_alert_recovery" if recovering_critical else "startup_or_retry",
                    "protective_management_remains_enabled": True,
                },
            )
            audit.time = now
            return True

    def acknowledge_recovery(
        self, operator: LocalOperator, *, account_key: str, broker_writes_quarantined: bool
    ) -> None:
        self._local_operator(operator)
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
                session,
                "runtime.local_recovery_reviewed",
                "local_operator",
                {"process_id": operator.process_id, "account": account_key},
            )

    def auto_pause(self, reason: str, *, account_key: str = "unbound") -> bool:
        """Pause new entries after a CRITICAL alert without changing latches or positions."""
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
            audit = self.database.add_audit(
                session,
                "runtime.auto_paused",
                "runtime",
                {"reason": reason, "account": account_key, "session_id": self.session_id},
            )
            audit.time = self.clock.now()
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

    def review_flat_baseline(self, operator: LocalOperator, *, account_key: str, confirm: str) -> None:
        """Explicit review of legacy/missed observations, ONLY with a flat proven ledger.

        Cannot reset a daily/drawdown latch or silently reconstruct unknown peaks.
        """
        self._local_operator(operator)
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
                "runtime.local_sampled_baseline_reviewed",
                "local_operator",
                {"process_id": operator.process_id, "account": account_key, "latches_reset": False},
            )

    def reset_kill(
        self, operator: LocalOperator, *, account_key: str, confirm: str, broker_writes_quarantined: bool
    ) -> None:
        self._local_operator(operator)
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
                session,
                "runtime.local_kill_reset_paused",
                "local_operator",
                {"process_id": operator.process_id, "account": account_key},
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


# Shared state implementation retained under the historical execution-facing name.
RuntimeState = RuntimeControl
