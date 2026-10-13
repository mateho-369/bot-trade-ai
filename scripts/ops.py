"""Current-local-operator runtime controls; no server, broker, or prompt is started here."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.process_guard import (
    ProcessLock,
    atomic_json,
    operator_stop_path,
    operator_stop_requested,
    read_json,
    request_operator_stop,
)
from app.reporter import Reporter
from core.database import Database
from core.local_operator import LocalOperator
from core.models import AccountSnapshot, BotState, OrderIntent, RiskState
from core.security import sha256_json
from core.settings import OperatingMode, Settings, live_trading_requested
from trading.runtime_state import TERMINAL_INTENT_STATES, RuntimeControl, _fresh_risk
from trading.types import SourceKind, SystemClock, TradingDisabled


def _settings(env_file: Path) -> Settings:
    path = env_file.expanduser().resolve()
    if not path.is_file():
        raise TradingDisabled("reviewed private environment file required")
    if live_trading_requested(path):
        raise TradingDisabled("LIVE_TRADING=true is refused in this build")
    return Settings(_env_file=path, project_root=path.parent)


def _read_health(settings) -> dict | None:
    try:
        data = read_json(settings.resolve_path(settings.runtime_health_file))
        updated = datetime.fromisoformat(data["updated_at"])
        if updated.tzinfo is None or updated.utcoffset() != timezone.utc.utcoffset(updated):
            return None
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if not -5 <= age <= settings.watchdog_stale_seconds:
            return None
        return data
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _account_key(session, mode: str) -> str:
    value = session.scalar(
        select(AccountSnapshot.account_key)
        .where(AccountSnapshot.mode == mode)
        .order_by(AccountSnapshot.time.desc(), AccountSnapshot.id.desc())
        .limit(1)
    )
    if not isinstance(value, str) or not value:
        raise TradingDisabled("no durable account snapshot is available for the configured mode")
    return value


def _suggestion_store(settings, database):
    """Compose the local proposal validator without connecting a broker or provider."""
    from ai.model_registry import ModelRegistry
    from ai.suggestion_store import SuggestionStore
    from trading.risk_types import RuntimeProfile

    if settings.mt5_backend == "mock":
        source = SourceKind.SYNTHETIC
    elif settings.mode == OperatingMode.PAPER:
        source = SourceKind.PAPER
    else:
        source = SourceKind.MT5
    profile = RuntimeProfile.current(settings, source)
    if settings.model_filter_enabled:
        profile = ModelRegistry(database, settings, SystemClock(), profile).runtime_profile()
    return SuggestionStore(database, settings, SystemClock(), profile)


def _suggestion_summary(suggestion) -> dict:
    return {
        "suggestion_id": suggestion.suggestion_id,
        "status": suggestion.status,
        "kind": suggestion.kind,
        "expires_at": suggestion.expires_at.isoformat(),
    }


def _suggestion_review(suggestion) -> dict:
    payload = suggestion.payload()
    result = {
        **_suggestion_summary(suggestion),
        "parameters": payload["parameters"],
        "reason": payload["reason"],
        "risk_level": payload["risk_level"],
    }
    if suggestion.status == "pending":
        digest = sha256_json(payload)
        result["approve_confirmation"] = f"APPROVE_PROPOSAL_{suggestion.suggestion_id}_{digest}"
        result["reject_confirmation"] = f"REJECT_PROPOSAL_{suggestion.suggestion_id}_{digest}"
    return result


def _runtime_control(settings, database, clock):
    control = RuntimeControl(database, settings, clock)
    with database.session() as session:
        state = session.get(BotState, 1)
        if state is None:
            raise TradingDisabled("persistent control state is missing")
        control.session_id = state.session_id
        return control, state


def _require_live_readiness(settings, state, health) -> None:
    if health is None:
        raise TradingDisabled("fresh local runtime health is required")
    if (
        health.get("status") != "ready"
        or health.get("session_id") != state.session_id
        or health.get("config_hash") != settings.safety_fingerprint()
        or health.get("reconciled") is not True
        or health.get("components_ready") is not True
        or health.get("writes_quarantined") is not False
    ):
        raise TradingDisabled("runtime is not reconciled and fully ready")
    if settings.autonomous_demo and health.get("ai_healthy") is not True:
        raise TradingDisabled("AUTONOMOUS_DEMO requires a successful AI health check")
    if operator_stop_requested(settings):
        raise TradingDisabled("persistent local stop request is active")


def _status(database, settings) -> dict:
    health = _read_health(settings)
    with database.session() as session:
        state = session.get(BotState, 1)
        if state is None:
            raise TradingDisabled("persistent control state is missing")
        account_key = session.scalar(
            select(AccountSnapshot.account_key)
            .where(AccountSnapshot.mode == settings.mode.value)
            .order_by(AccountSnapshot.time.desc(), AccountSnapshot.id.desc())
            .limit(1)
        )
        risk = (
            session.scalar(
                select(RiskState).where(
                    RiskState.account_key == account_key,
                    RiskState.mode == settings.mode.value,
                )
            )
            if account_key
            else None
        )
        unsettled = (
            session.scalar(
                select(func.count())
                .select_from(OrderIntent)
                .where(
                    OrderIntent.account_key == account_key,
                    OrderIntent.mode == settings.mode.value,
                    OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
                )
            )
            if account_key
            else 0
        )
        lease_age = None
        if state.heartbeat is not None:
            lease_age = round(
                (datetime.now(timezone.utc) - state.heartbeat.astimezone(timezone.utc)).total_seconds(), 1
            )
        return {
            "mode": settings.mode.value,
            "desired_state": state.desired_state,
            "kill_switch_active": state.kill_switch_active,
            "halt_reason": state.last_error,
            "revision": state.revision,
            "runtime_lease_age_seconds": lease_age,
            "account_bound": bool(account_key),
            "risk_baseline_verified": bool(risk and risk.metadata_json.get("baseline_verified")),
            "daily_loss_latched": bool(risk and risk.daily_loss_latched),
            "drawdown_latched": bool(risk and risk.drawdown_latched),
            "risk_observation_fresh": bool(risk and _fresh_risk(risk, settings, SystemClock())),
            "unsettled_intents": int(unsettled or 0),
            "operator_stop_requested": operator_stop_requested(settings),
            "health": health.get("status", "missing_or_stale") if health else "missing_or_stale",
            "ai_healthy": health.get("ai_healthy") is True if health else False,
            "not_live_authorization": True,
        }


def _emit_report(settings, key: str, details: dict | None = None) -> None:
    async def send():
        reporter = Reporter(settings)
        try:
            await asyncio.wait_for(
                reporter.publish(reporter.text(key), kind="operator." + key, details=details), timeout=6
            )
        finally:
            await reporter.close()

    try:
        asyncio.run(send())
    except Exception:
        # The durable transition succeeds independently of local/remote reporting.
        pass


def _clear_stop(settings) -> None:
    path = operator_stop_path(settings)
    with ProcessLock(settings.resolve_path(settings.watchdog_lock_file)):
        with ProcessLock(settings.resolve_path(settings.runtime_lock_file)):
            atomic_json(path, {"stop": False})  # Keep the operator-stop.json evidence in place.


def _count_unsettled(session, account_key: str, mode: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(OrderIntent)
            .where(
                OrderIntent.account_key == account_key,
                OrderIntent.mode == mode,
                OrderIntent.state.not_in(TERMINAL_INTENT_STATES),
            )
        )
        or 0
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local runtime operations only; no network listener, broker call, or interactive confirmation"
        )
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "status",
        "pause",
        "resume",
        "kill",
        "reset-kill",
        "recover",
        "review-baseline",
        "stop",
        "clear-stop",
    ):
        command = sub.add_parser(name)
        if name in {"reset-kill", "recover", "review-baseline"}:
            command.add_argument("--confirm", required=True)
    proposals = sub.add_parser("proposals", help="list a bounded set of integrity-checked AI proposals")
    proposals.add_argument("--status", choices=("pending", "approved", "rejected", "applied", "expired"))
    proposals.add_argument("--limit", type=int, default=25)

    proposal_show = sub.add_parser(
        "proposal-show", help="inspect one pending proposal and its hash-bound decision"
    )
    proposal_show.add_argument("suggestion_id", type=int)
    proposal_decide = sub.add_parser(
        "proposal-decide", help="record a local decision; never applies settings or trades"
    )
    proposal_decide.add_argument("suggestion_id", type=int)
    proposal_decide.add_argument("--decision", required=True, choices=("approve", "reject"))
    proposal_decide.add_argument("--confirm", required=True)

    fallback = sub.add_parser("ai-fallback", help="reviewed local change to AI outage policy only")
    fallback.add_argument("--mode", required=True, choices=("BLOCK_ON_AI_FAILURE", "TECHNICAL_ONLY"))
    reset_ai = sub.add_parser("reset-ai", help="revert AI overlays; never clears risk/capital latches")
    reset_ai.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    database = None
    try:
        settings = _settings(args.env_file)
        operator = LocalOperator.current()
        if args.command == "clear-stop":
            operator.require_current()
            _clear_stop(settings)
            _emit_report(settings, "operator_stop_cleared")
            print(json.dumps({"status": "cleared_only", "runtime_starts_paused": True}, sort_keys=True))
            return 0
        if args.command == "stop":
            operator.require_current()
            request_operator_stop(settings)
            _emit_report(settings, "operator_stop")
            print(json.dumps({"status": "stop_requested", "force_killed": False}, sort_keys=True))
            return 0

        database = Database(settings)
        database.verify_schema()  # Existing state only; never initialize or migrate here.
        if args.command in {"proposals", "proposal-show", "proposal-decide"}:
            operator.require_current()
            store = _suggestion_store(settings, database)
            if args.command == "proposals":
                rows = store.list(status=args.status, limit=args.limit)
                print(
                    json.dumps(
                        {"proposals": [_suggestion_summary(row) for row in rows], "read_only": True},
                        sort_keys=True,
                    )
                )
                return 0
            suggestion = store.get(args.suggestion_id)
            if args.command == "proposal-show":
                print(json.dumps(_suggestion_review(suggestion), sort_keys=True))
                return 0
            if suggestion.status != "pending":
                raise TradingDisabled("only a current pending proposal can be decided")
            payload_hash = sha256_json(suggestion.payload())
            expected = f"{args.decision.upper()}_PROPOSAL_{suggestion.suggestion_id}_{payload_hash}"
            if args.confirm != expected:
                raise TradingDisabled("exact current proposal review acknowledgement required")
            result = store.decide(
                suggestion.suggestion_id,
                operator=operator,
                approve=args.decision == "approve",
                expected_payload_hash=payload_hash,
            )
            _emit_report(
                settings,
                "operator_proposal_decision",
                {"suggestion_id": result.suggestion_id, "decision": result.status},
            )
            print(
                json.dumps(
                    {
                        "suggestion_id": result.suggestion_id,
                        "status": result.status,
                        "settings_applied": False,
                        "trade_executed": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "status":
            print(json.dumps(_status(database, settings), sort_keys=True))
            return 0
        if args.command == "ai-fallback":
            from trading.ai_controls import set_fallback_mode

            result = set_fallback_mode(database, SystemClock(), args.mode, operator=operator)
            _emit_report(settings, "operator_ai_fallback", {"mode": result["mode"]})
            print(json.dumps({"status": "ok", "mode": result["mode"]}, sort_keys=True))
            return 0
        if args.command == "reset-ai":
            if args.confirm != "RESET_AI_TO_REVIEWED_DEFAULTS":
                raise TradingDisabled("exact AI reset acknowledgement required")
            from ai.config_adjuster import revert_all

            count = revert_all(database, SystemClock(), operator=operator)
            _emit_report(settings, "operator_ai_reset", {"overlays_reverted": count})
            print(json.dumps({"status": "ok", "overlays_reverted": count}, sort_keys=True))
            return 0

        control, state = _runtime_control(settings, database, SystemClock())
        if args.command == "pause":
            control.pause(operator)
            _emit_report(settings, "operator_pause")
        elif args.command == "kill":
            control.kill(operator)
            _emit_report(settings, "operator_kill")
        elif args.command == "resume":
            health = _read_health(settings)
            _require_live_readiness(settings, state, health)
            with database.session() as session:
                account_key = _account_key(session, settings.mode.value)
            control.resume(
                operator,
                account_key=account_key,
                expected_revision=state.revision,
            )
            _emit_report(settings, "operator_resumed")
        elif args.command == "reset-kill":
            if args.confirm != "RESET_KILL_AND_KEEP_PAUSED":
                raise TradingDisabled("exact kill-reset acknowledgement required")
            health = _read_health(settings)
            _require_live_readiness(settings, state, health)
            with database.session() as session:
                account_key = _account_key(session, settings.mode.value)
            control.reset_kill(
                operator,
                account_key=account_key,
                confirm=args.confirm,
                broker_writes_quarantined=health["writes_quarantined"],
            )
            _emit_report(settings, "operator_kill_reset")
        elif args.command == "recover":
            if args.confirm != "ACKNOWLEDGE_RECOVERY_KEEP_PAUSED":
                raise TradingDisabled("exact recovery acknowledgement required")
            health = _read_health(settings)
            _require_live_readiness(settings, state, health)
            with database.session() as session:
                account_key = _account_key(session, settings.mode.value)
                if _count_unsettled(session, account_key, settings.mode.value):
                    raise TradingDisabled("unsettled intents prevent recovery review")
            control.acknowledge_recovery(
                operator,
                account_key=account_key,
                broker_writes_quarantined=health["writes_quarantined"],
            )
            _emit_report(settings, "operator_recovery_reviewed")
        elif args.command == "review-baseline":
            if args.confirm != "REVIEW_SAMPLED_BASELINE":
                raise TradingDisabled("exact sampled-baseline acknowledgement required")
            health = _read_health(settings)
            _require_live_readiness(settings, state, health)
            with database.session() as session:
                account_key = _account_key(session, settings.mode.value)
            control.review_flat_baseline(operator, account_key=account_key, confirm=args.confirm)
            _emit_report(settings, "operator_baseline_reviewed")
        else:  # pragma: no cover - argparse constrains command values.
            raise TradingDisabled("unknown local operation")
        print(json.dumps({"status": "ok", "command": args.command}, sort_keys=True))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "refused", "error_kind": type(error).__name__, "raw_error_printed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
