"""Trusted owner facade shared by Telegram and FastAPI; nothing auto-starts.

Inject an ALREADY initialized ExecutionEngine for resume/owned close. Database
reads remain usable without it. There are deliberately no order/open/apply/
reset-kill/risk-escalation/live-approval/model-execution endpoints here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from ai.suggestion_store import SuggestionStore
from app.async_tools import durable_call
from app.owner_actions import ActionScope, OwnerActionStore, StagedAction
from app.owner_identity import OwnerInterfaceError
from app.rate_limits import WindowLimiter
from app.read_model import OwnerReadModel
from core.models import BotState, OrderIntent, Trade
from core.security import sha256_json
from trading.ai_controls import broker_ceilings
from trading.execution import ExecutionEngine
from trading.risk_types import position_review_hash, source_code_hash
from trading.runtime_state import TERMINAL_INTENT_STATES, RuntimeControl
from trading.types import BrokerError, ResultStatus, SystemClock, TradingDisabled, UncertainExecution


class OwnerServices:
    def __init__(
        self,
        database,
        settings,
        *,
        clock=None,
        execution=None,
        suggestions=None,
        news=None,
        preview_only=False,
    ):
        if database.settings.safety_fingerprint() != settings.safety_fingerprint():
            raise ValueError("owner database/settings diverge")
        if execution is not None and (
            not isinstance(execution, ExecutionEngine)
            or execution.database is not database
            or execution.settings.safety_fingerprint() != settings.safety_fingerprint()
        ):
            raise ValueError("explicit matching execution engine required")
        if preview_only and (
            execution is not None
            or suggestions is not None
            or news is not None
            or settings.telegram_owner_id is not None
        ):
            raise ValueError("synthetic preview cannot attach owner credentials/runtime/providers")
        self.database, self.settings, self.execution = database, settings, execution
        self.clock = clock or (execution.clock if execution is not None else SystemClock())
        if execution is not None and self.clock is not execution.clock:
            raise ValueError("owner/execution must share the same clock")
        self.interface_session_id = str(uuid4())
        self.preview_only = preview_only
        code_root = (
            settings.project_root
            if (settings.project_root / "main.py").is_file()
            else Path(__file__).resolve().parents[1]
        )
        self._code_root, self._code_hash = code_root, source_code_hash(code_root)
        self.control = execution.control if execution else RuntimeControl(database, settings, self.clock)
        self.suggestions = suggestions or (
            SuggestionStore(database, settings, self.clock, execution.profile) if execution else None
        )
        if self.suggestions is not None and (
            self.suggestions.database is not database
            or self.suggestions.clock is not self.clock
            or self.suggestions.settings.safety_fingerprint() != settings.safety_fingerprint()
        ):
            raise ValueError("explicit matching proposal store required")
        self.news = news
        if news is not None and (
            news.database is not database
            or news.clock is not self.clock
            or news.settings.safety_fingerprint() != settings.safety_fingerprint()
        ):
            raise ValueError("explicit matching news manager required")
        self.actions = OwnerActionStore(database, settings, self.clock)
        self.views = OwnerReadModel(database, settings, self.clock, execution=execution)
        self.limiter = WindowLimiter(settings.api_max_rate_limit_keys)
        self._action_lock = asyncio.Lock()
        self.closing = False
        self.active_actions = set()

    def scope(self):
        profile = (
            self.execution.profile
            if self.execution
            else (self.suggestions.profile if self.suggestions else None)
        )
        return ActionScope(
            interface_session_id=self.interface_session_id,
            runtime_session_id=self.control.session_id,
            account_key=self.execution.account_key or "unbound" if self.execution else "unbound",
            config_hash=self.settings.safety_fingerprint(),
            code_hash=self._code_hash,
            model_sha256=profile.model_sha256 if profile else None,
            data_source=profile.data_source.value if profile else "unattached",
            mode=self.settings.mode.value,
        )

    def require_actor(self, actor):
        if self.preview_only:
            raise OwnerInterfaceError("synthetic_preview_is_read_only", 403)
        actor.require(self.settings, self.clock)

    def limit_actor(self, actor, *, safe_stop=False):
        self.require_actor(actor)
        self.limiter.require(
            str(actor.owner_id),
            now=self.clock.now().timestamp(),
            limit=self.settings.api_safe_stop_rate_per_minute
            if safe_stop
            else self.settings.api_rate_limit_per_minute,
            budget="safe_stop" if safe_stop else "ordinary",
        )

    def _source_current(self):
        if source_code_hash(self._code_root) != self._code_hash:
            raise OwnerInterfaceError("code_changed_restart_paused_and_review", 409)
        profile = (
            self.execution.profile
            if self.execution
            else (self.suggestions.profile if self.suggestions else None)
        )
        if profile is not None and profile.code_hash != self._code_hash:
            raise OwnerInterfaceError("runtime_profile_changed", 409)

    def _runtime_state(self):
        self._source_current()
        if self.execution is None:
            raise OwnerInterfaceError("execution_runtime_not_attached", 503)
        self.execution._ready()
        with self.database.session() as session:
            state = session.get(BotState, 1)
            self.control.check(state)
            return {"state_revision": state.revision}

    async def read(self, actor, section, *, limit=50, offset=0, level=None):
        self.require_actor(actor)
        functions = {
            "dashboard": self.views.dashboard,
            "positions": lambda **k: self.views.trades(positions_only=True, **k),
            "trades": self.views.trades,
            "signals": self.views.signals,
            "news": self.views.news,
            "suggestions": self.views.suggestions,
            "settings": self.views.settings_view,
            "logs": self.views.logs,
            "ai_journal": self.views.ai_journal,
            "ai_fallback": self._ai_fallback_view,
            "limits": self._limits_view,
            "alerts": lambda **k: self._alerts_view(level=level, **k),
            "ai_stats": self._ai_stats_view,
            "audit": self._audit_view,
        }
        if section not in functions:
            raise OwnerInterfaceError("section_not_found", 404)
        if level is not None and (
            section != "alerts" or level not in {"INFO", "WARNING", "ERROR", "CRITICAL"}
        ):
            raise OwnerInterfaceError("invalid_alert_level", 422)
        kwargs = (
            {}
            if section in {"dashboard", "settings", "ai_fallback", "limits", "ai_stats", "audit"}
            else {"limit": limit, "offset": offset}
        )
        result = await asyncio.to_thread(functions[section], **kwargs)
        if section == "settings":
            result["ai_fallback"] = await asyncio.to_thread(self._ai_fallback_view)
        if section == "dashboard":
            result["capabilities"] = {
                "pause": True,
                "kill": True,
                "resume": self.execution is not None,
                "close_owned": self.execution is not None,
                "decide_proposal": self.suggestions is not None,
                "acknowledge_recovery": self.execution is not None,
                "open_orders": False,
                "enable_live": False,
                "apply_proposals": False,
            }
        if section == "news" and self.news is not None:
            try:
                decisions = await self.news.decisions()  # Managed reads only; NO refresh/provider HTTP.
                result["coverage"] = {
                    "status": "managed_projection",
                    "symbols": {
                        symbol: {
                            "known": d.window.known,
                            "safe": d.window.safe,
                            "allowed": d.allowed,
                            "fixture_only": d.window.fixture_only,
                            "reasons": list(d.reasons),
                            "expires_at": d.window.expires_at.isoformat() if d.window.expires_at else None,
                        }
                        for symbol, d in decisions.items()
                    },
                    "not_entry_permission": True,
                }
                _, state = await asyncio.to_thread(self.news.cache.latest)
                calendar = state.get("calendar") if state else None
                if calendar:
                    result["calendar"] = [
                        {
                            "id": e["event_id"],
                            "title": self.views.text(e["title"], 200),
                            "currency": e["currency"],
                            "starts_at": e["starts_at"],
                            "impact": e["impact"],
                            "tentative": e["tentative"],
                        }
                        for e in calendar["events"][:50]
                    ]
            except Exception:
                result["coverage"] = {"status": "unknown", "reason": "managed_news_unavailable"}
                result["calendar"] = []
        return result

    # -- AI fallback / dynamic limits / alerts (owner reads; no broker access) ----------------
    def _ai_fallback_view(self):
        from trading.ai_controls import ai_status, effective_limits, fallback_status

        status = fallback_status(self.database, self.settings)
        status["ai"] = ai_status(self.database, self.clock)
        status["limits"] = effective_limits(self.database, self.settings, self.clock).as_dict()
        status["kill_switch_unaffected"] = True
        status["require_approval"] = bool(self.settings.ai_require_approval)
        status["rule_fallback_enabled"] = bool(self.settings.ai_rule_fallback_enabled)
        return status

    def _ai_stats_view(self):
        """Per-AI-label approvals, rejections, trades, win rate, net profit, confidence, failures."""
        from ai.decision_journal import ensure_journal_tables
        from ai.trade_attribution import ai_stats, history

        try:
            ensure_journal_tables(self.database)  # Additive/idempotent (adds provider_label if missing).
            stats = ai_stats(self.database)
            recent = history(self.database, limit=10)
        except Exception:
            stats, recent = {"labels": [], "error": "ai_stats_unavailable"}, []
        stats["configured"] = [
            {"label": e.label, "model": e.model, "role": e.role, "priority": e.priority, "enabled": e.enabled}
            for e in self.settings.ai_registry()
        ]
        stats["decision_mode"] = self.settings.ai_decision_mode
        stats["require_approval"] = self.settings.ai_require_approval
        stats["recent_trades"] = recent
        return stats

    def _audit_view(self):
        """Read-only trade audit (no writes; the daily job also syncs attribution and alerts)."""
        from ai.trade_audit import audit_trades

        try:
            return audit_trades(self.database, self.settings, self.clock, run_sync=False)
        except Exception:
            return {"checked": 0, "flags": [], "counts": {}, "clean": False, "error": "audit_unavailable"}

    def _limits_view(self):
        from trading.ai_controls import (
            DYNAMIC_BOUNDS,
            HARD_MAX_DAILY_TRADES,
            HARD_MAX_OPEN_POSITIONS,
            HARD_MAX_RISK_PERCENT,
            defaults,
            effective_limits,
            read_dynamic,
        )

        return {
            "effective": effective_limits(self.database, self.settings, self.clock).as_dict(),
            "defaults": defaults(self.settings).as_dict(),
            "ai_overrides": {k: str(v) for k, v in read_dynamic(self.database).items()},
            "ai_bounds": {k: [str(lo), str(hi)] for k, (lo, hi) in DYNAMIC_BOUNDS.items()},
            "hard_caps": {
                "max_daily_trades": HARD_MAX_DAILY_TRADES,
                "max_open_positions": HARD_MAX_OPEN_POSITIONS,
                "risk_percent_per_trade": str(HARD_MAX_RISK_PERCENT),
                "max_daily_loss_percent": str(self.settings.max_daily_loss_percent),
                "max_drawdown_percent": str(self.settings.max_drawdown_percent),
            },
            "owner_approval_above_change_percent": 50,
        }

    def _alerts_view(self, *, limit=100, offset=0, level=None):
        from app.alerts import list_alerts

        return list_alerts(self.database, limit=limit, offset=offset, level=level)

    @staticmethod
    def parameters(action, parameters):
        if type(parameters) is not dict:
            raise OwnerInterfaceError("invalid_action_parameters", 422)
        expected = {
            "close_position": {"ticket", "position_identifier"},
            "approve_suggestion": {"suggestion_id"},
            "reject_suggestion": {"suggestion_id"},
            "ack_alert": {"alert_id"},
        }
        if set(parameters) != expected.get(action, set()) or any(
            type(v) is not int or not 0 < v <= 2**63 - 1 for v in parameters.values()
        ):
            raise OwnerInterfaceError("invalid_action_parameters", 422)
        return dict(parameters)

    async def _prepare(self, actor, action, parameters, request_id):
        self._source_current()
        binding = {}
        if action in {"resume", "close_position", "close_all"}:
            binding = await asyncio.to_thread(self._runtime_state)
        if action in {"close_position", "close_all"}:
            positions = await self.execution.capture_owned_positions()
            if action == "close_position":
                positions = tuple(
                    p
                    for p in positions
                    if p.ticket == parameters["ticket"] and p.identifier == parameters["position_identifier"]
                )
            if not positions or len(positions) > broker_ceilings(self.settings).max_open_positions:
                raise OwnerInterfaceError("no_matching_owned_positions_or_capture_bound", 409)
            binding["positions"] = [
                {
                    "ticket": p.ticket,
                    "position_identifier": p.identifier,
                    "volume": str(p.volume),
                    "symbol": p.symbol,
                    "position_hash": position_review_hash(p),
                }
                for p in positions
            ]
            summary = (
                f"Close {len(positions)} captured bot-owned position(s); market costs/slippage apply. "
                + (
                    "Entries will be paused. Other/new/manual positions are NOT included. "
                    if action == "close_all"
                    else ""
                )
                + "Unknown or partial execution requires reconciliation, not retry."
            )
        elif action == "resume":
            summary = (
                "Resume new entries only if the same runtime/control revision and all existing risk gates "
                "still permit. "
                "This does NOT approve live trading."
            )
        elif action == "approve_suggestion":
            if self.suggestions is None:
                raise OwnerInterfaceError("proposal_store_not_attached", 503)
            suggestion = await durable_call(self.suggestions.get, parameters["suggestion_id"])
            if suggestion.status != "pending":
                raise OwnerInterfaceError("proposal_not_pending", 409)
            binding = {"suggestion_hash": sha256_json(suggestion.payload())}
            summary = (
                "Approve the stored proposal for later explicit review. Approval does NOT apply settings, "
                "change risk, start a model, or close any trade."
            )
        else:
            raise OwnerInterfaceError("action_not_supported", 422)
        # Capture revision AFTER explicit close preparation; resume has no reconciliation side effect.
        if action in {"close_position", "close_all"}:
            binding.update(await asyncio.to_thread(self._runtime_state))
        self.require_actor(actor)
        return await durable_call(
            self.actions.prepare, actor, self.scope(), action, parameters, request_id, binding, summary
        )

    @asynccontextmanager
    async def _guard_action(self, action):
        task = asyncio.current_task()
        self.active_actions.add(task)
        try:
            if self.closing and action not in {"pause", "kill"}:
                raise OwnerInterfaceError("runtime_stopping", 503)
            if action in {"pause", "kill"}:
                # Downward control must not queue behind slow closes/providers.
                yield
            else:
                async with self._action_lock:
                    if self.closing:
                        raise OwnerInterfaceError("runtime_stopping", 503)
                    yield
        finally:
            self.active_actions.discard(task)

    async def action(self, actor, action, parameters, request_id, confirmation_token=None):
        self.require_actor(actor)
        self.actions.request_id(request_id)
        parameters = self.parameters(action, parameters)
        async with self._guard_action(action):
            scope = self.scope()
            cached = await durable_call(self.actions.cached, actor, scope, action, parameters, request_id)
            if cached is not None:
                return cached
            if (
                action in {"resume", "close_position", "close_all", "approve_suggestion"}
                and confirmation_token is None
            ):
                try:
                    return await self._prepare(actor, action, parameters, request_id)
                except TradingDisabled:
                    raise OwnerInterfaceError("owner_runtime_capture_not_ready", 409) from None
                except BrokerError:
                    await self._halt("broker_unstable")
                    raise OwnerInterfaceError("owner_runtime_unavailable", 503) from None
            stage_task = asyncio.create_task(
                asyncio.to_thread(
                    self.actions.stage, actor, scope, action, parameters, request_id, confirmation_token
                )
            )
            try:
                staged = await asyncio.shield(stage_task)
            except asyncio.CancelledError:
                staged = await stage_task
                if isinstance(staged, StagedAction):
                    await durable_call(
                        self.actions.finish,
                        staged,
                        {"status": "uncertain", "reason": "request_canceled_do_not_retry"},
                    )
                raise
            if isinstance(staged, dict):
                return staged
            try:
                self.require_actor(actor)
                outcome = await self._effect(actor, staged)
            except asyncio.CancelledError:
                await self._halt("unknown_execution")
                await durable_call(
                    self.actions.finish,
                    staged,
                    {"status": "uncertain", "reason": "request_canceled_do_not_retry"},
                )
                raise
            except (UncertainExecution, BrokerError) as error:
                if isinstance(error, TradingDisabled) and staged.action not in {
                    "close_position",
                    "close_all",
                }:
                    outcome = {"status": "rejected", "reason": "existing_trading_gate_rejected"}
                else:
                    await self._halt("unknown_execution")
                    outcome = {
                        "status": "uncertain",
                        "reason": "execution_requires_reconciliation_do_not_retry",
                    }
            except OwnerInterfaceError as error:
                outcome = {"status": "rejected", "reason": error.code}
            except Exception:
                await self._halt("persistence_failure")
                outcome = {"status": "uncertain", "reason": "action_outcome_unknown_do_not_retry"}
            try:
                return await durable_call(self.actions.finish, staged, outcome)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._halt("persistence_failure")
                raise OwnerInterfaceError("action_pending_or_uncertain_do_not_retry", 409) from None

    async def _halt(self, reason):
        if self.execution is None:
            return
        try:
            await durable_call(self.control.halt, reason, account_key=self.execution.account_key or "unbound")
        except Exception:
            pass  # Lost storage cannot be repaired by retrying orders or logging raw failures.

    def _remaining(self):
        with self.database.session() as session:
            rows = session.scalars(
                select(Trade)
                .where(
                    Trade.account_key == self.execution.account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status.in_(("open", "unknown")),
                )
                .order_by(Trade.id)
                .limit(101)
            ).all()
            unsettled = session.scalar(
                select(OrderIntent.id)
                .where(
                    OrderIntent.account_key == self.execution.account_key,
                    OrderIntent.mode == self.settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
                .limit(1)
            )
            return {
                "remaining_owned_ledger": [
                    {
                        "ticket": r.ticket,
                        "position_identifier": r.position_identifier,
                        "symbol": r.symbol,
                        "direction": r.direction,
                        "volume": str(r.volume),
                        "status": r.status,
                    }
                    for r in rows[:100]
                ],
                "remaining_bound_exceeded": len(rows) > 100,
                "unsettled_intents": unsettled is not None,
                "broker_account_flat_claimed": False,
            }

    async def _effect(self, actor, staged):
        action, parameters = staged.action, staged.parameters
        if action in {"pause", "kill"}:
            await durable_call(self.control.pause if action == "pause" else self.control.kill, actor.owner_id)
            return {
                "status": "completed",
                "message": "New entries paused; in-flight SDK writes cannot be recalled."
                if action == "pause"
                else "Kill latch set; no new entries. This does not flatten the broker account.",
                "effect_applied": True,
            }
        if action == "acknowledge_recovery":
            # Owner reviewed the recorded halt. Clears last_error and stays PAUSED;
            # resume remains a separate fully-gated confirmation.
            quarantined = (
                self.execution is not None
                and self.execution.broker.health().get("writes_quarantined") is True
            )
            await durable_call(
                self.control.acknowledge_recovery,
                actor.owner_id,
                account_key=self.execution.account_key,
                broker_writes_quarantined=quarantined,
            )
            # The designed flat-baseline review: refuses unless the ledger is proven
            # flat with fresh complete history (raises TradingDisabled otherwise).
            await durable_call(
                self.control.review_flat_baseline,
                actor.owner_id,
                account_key=self.execution.account_key,
                confirm="REVIEW_SAMPLED_BASELINE",
            )
            return {
                "status": "completed",
                "effect_applied": True,
                "message": (
                    "Recovery reviewed; halt and observation gap cleared on the flat baseline. "
                    "Entries stay PAUSED until a fresh gated resume."
                ),
            }
        if action == "ai_reset":
            from ai.config_adjuster import revert_all

            reverted = await durable_call(revert_all, self.database, self.clock, owner_id=actor.owner_id)
            return {
                "status": "completed",
                "reverted_ai_adjustments": reverted,
                "effect_applied": True,
                "message": "AI config overlay reverted to owner settings; no trade was opened or closed.",
            }
        if action in {"ai_fallback_block", "ai_fallback_technical"}:
            from trading.ai_controls import set_fallback_mode

            mode = "BLOCK_ON_AI_FAILURE" if action == "ai_fallback_block" else "TECHNICAL_ONLY"
            status = await durable_call(
                set_fallback_mode, self.database, self.clock, mode, owner_id=actor.owner_id
            )
            return {
                "status": "completed",
                "effect_applied": True,
                "ai_fallback_mode": status["mode"],
                "message": (
                    "AI fallback: BLOCK_ON_AI_FAILURE - no new entries while the AI is unavailable."
                    if mode == "BLOCK_ON_AI_FAILURE"
                    else "AI fallback: TECHNICAL_ONLY - when the AI is unavailable, entries need the "
                    f"technical score >= {self.settings.ai_rule_fallback_min_score:g}. "
                    "Risk checks, news block and kill switch are unchanged."
                ),
            }
        if action in {"ack_alerts", "ack_alert"}:
            from app.alerts import acknowledge

            count = await durable_call(
                acknowledge,
                self.database,
                self.clock,
                owner_id=actor.owner_id,
                alert_id=parameters.get("alert_id"),
            )
            return {
                "status": "completed",
                "effect_applied": True,
                "acknowledged": count,
                "message": f"{count} alert(s) acknowledged.",
            }
        if self.closing:
            raise OwnerInterfaceError("runtime_stopping", 503)
        self._source_current()
        if action in {"approve_suggestion", "reject_suggestion"}:
            if self.suggestions is None:
                raise OwnerInterfaceError("proposal_store_not_attached", 503)
            if action == "approve_suggestion":
                current = await durable_call(self.suggestions.get, parameters["suggestion_id"])
                if sha256_json(current.payload()) != staged.binding.get("suggestion_hash"):
                    raise OwnerInterfaceError("proposal_changed_after_confirmation", 409)
            result = await durable_call(
                self.suggestions.decide,
                parameters["suggestion_id"],
                owner_id=actor.owner_id,
                approve=action == "approve_suggestion",
            )
            return {
                "status": "completed",
                "suggestion_id": result.suggestion_id,
                "proposal_status": result.status,
                "settings_applied": False,
                "trade_executed": False,
                "message": "Decision recorded only; no settings or trade application.",
            }
        await asyncio.to_thread(self._runtime_state)
        if action == "resume":
            await durable_call(
                self.control.resume,
                actor.owner_id,
                account_key=self.execution.account_key,
                expected_revision=staged.binding["state_revision"],
            )
            return {
                "status": "completed",
                "effect_applied": True,
                "message": (
                    "Entry resume recorded; existing per-decision gates still apply. No live approval issued."
                ),
            }
        if action in {"close_position", "close_all"}:
            if action == "close_all":
                await durable_call(self.control.pause, actor.owner_id)
            results = []
            status = "completed"
            for position in staged.binding["positions"]:
                key = sha256_json(
                    {
                        "format": "owner-close-v1",
                        "run_id": staged.run_id,
                        "ticket": position["ticket"],
                        "identifier": position["position_identifier"],
                    }
                )
                try:
                    result = await self.execution.close_owned(
                        position["ticket"],
                        position["position_identifier"],
                        idempotency_key=key,
                        expected_position_hash=position["position_hash"],
                    )
                except BrokerError:
                    # Earlier captures may already have closed. Preserve their
                    # outcomes and inspect remaining ledger, never claim atomicity.
                    results.append(
                        {
                            "ticket": position["ticket"],
                            "position_identifier": position["position_identifier"],
                            "status": "unknown_or_denied",
                            "filled_volume": None,
                        }
                    )
                    status = "uncertain"
                    break
                results.append(
                    {
                        "ticket": position["ticket"],
                        "position_identifier": position["position_identifier"],
                        "status": result.status.value,
                        "filled_volume": str(result.filled_volume),
                    }
                )
                if result.requires_reconciliation:
                    status = "uncertain"
                    break  # No attempt to hide uncertainty by continuing/retrying a batch.
                if result.status not in {ResultStatus.FILLED, ResultStatus.NO_CHANGE}:
                    status = "rejected"
                    break
            remaining = await asyncio.to_thread(self._remaining)
            if remaining["unsettled_intents"] or remaining["remaining_bound_exceeded"]:
                status = "uncertain"
            captured_ids = {p["position_identifier"] for p in staged.binding["positions"]}
            if status == "completed" and any(
                p["position_identifier"] in captured_ids for p in remaining["remaining_owned_ledger"]
            ):
                status = "uncertain"
            if status == "uncertain":
                await self._halt("unknown_execution")
            return {
                "status": status,
                "results": results,
                **remaining,
                "message": (
                    "Captured owned-close outcome recorded. Review remaining exposure; "
                    "no atomic flatten or retry guarantee."
                ),
            }
        raise OwnerInterfaceError("action_not_supported", 422)

    async def confirm_button(self, actor, token, *, cancel=False):
        self.require_actor(actor)
        if cancel:
            return await durable_call(self.actions.cancel, actor, self.scope(), token)
        description = await durable_call(self.actions.describe, actor, self.scope(), token)
        return await self.action(
            actor,
            description["action"],
            description["parameters"],
            description["request_id"],
            confirmation_token=token,
        )
