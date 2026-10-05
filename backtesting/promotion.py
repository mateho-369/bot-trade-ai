"""Owner-authenticated stage artifact import + ledger-derived paper/demo exports.

Research replay output is NEVER relabeled or inserted as promotion evidence.
These trusted local functions instantiate no broker/provider/daemon. A native-source
label is not independently authenticated: the existing intent/deal/coverage proofs
are rechecked by StageGate, and the owner must review data provenance separately.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from ai.model_registry import ModelRegistry
from backtesting.artifacts import write_json
from backtesting.contracts import utc_time
from backtesting.dataset import file_bytes, strict_json_bytes
from backtesting.metrics import EquityPoint, summarize
from core.models import AccountSnapshot, BotState, BrokerDeal, DeploymentEvidence, OrderIntent, Trade
from core.security import sha256_json
from core.settings import OperatingMode
from telegram_bot.miniapp_auth import validate_init_data
from trading.risk_types import RuntimeProfile
from trading.stage_gate import StageGate
from trading.types import ZERO, SourceKind, TradingDisabled, valid_key


def native_profile(database, settings, clock):
    if settings.mt5_backend != "real" or settings.mode == OperatingMode.BACKTEST:
        raise TradingDisabled(
            "stage qualification requires native-source configuration, never historical/mock"
        )
    profile = RuntimeProfile.current(settings, SourceKind.MT5)
    if settings.model_filter_enabled:
        profile = ModelRegistry(database, settings, clock, profile).runtime_profile()
    return profile


def evidence_from(report, *, path, digest, owner_id, created_at):
    try:
        if report["format"] != "reflex-stage-v1" or report["stage"] not in {"backtest", "paper", "demo"}:
            raise ValueError
        if report.get("promotion_eligible") is False or report.get("research_only") is True:
            raise ValueError
        if not isinstance(report["metrics"], dict) or not isinstance(
            report.get("account_key"), (str, type(None))
        ):
            raise ValueError
        for name in ("code_hash", "model_sha256", "strategy_config_hash"):
            valid_key(report[name])
        return DeploymentEvidence(
            stage=report["stage"],
            created_at=created_at,
            started_at=utc_time(report["started_at"]),
            finished_at=utc_time(report["finished_at"]),
            strategy_config_hash=report["strategy_config_hash"],
            code_hash=report["code_hash"],
            model_sha256=report["model_sha256"],
            account_key=report.get("account_key"),
            metrics_json=report["metrics"],
            artifact_path=str(path),
            artifact_sha256=digest,
            owner_reviewed_by=owner_id,
            passed=True,
            revoked=False,
        )
    except (KeyError, TypeError, ValueError):
        raise TradingDisabled(
            "not a reviewed production stage report; research reports cannot be promoted"
        ) from None


def import_reviewed_stage(
    database, settings, clock, *, report_path: Path, confirm_sha256: str, owner_init_data: str
) -> int:
    """Fresh signed owner identity + literal file digest + stopped/flat state; append evidence only."""
    identity = validate_init_data(owner_init_data, settings, clock)
    valid_key(confirm_sha256)
    path = Path(report_path)
    if not path.is_absolute():
        path = settings.project_root / path
    raw = file_bytes(path, root=settings.project_root, limit=1048576)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != confirm_sha256:
        raise TradingDisabled("owner confirmation does not match the stage file bytes")
    report = strict_json_bytes(raw, limit=1048576)
    relative = path.absolute().relative_to(settings.project_root.resolve())
    evidence = evidence_from(
        report, path=relative, digest=digest, owner_id=identity.owner_id, created_at=clock.now()
    )
    profile = native_profile(database, settings, clock)
    if evidence.stage == "backtest":
        # Check every component before StageGate resolves it; no symlink-parent laundering.
        dataset = settings.project_root / Path(report.get("dataset_path", "missing-stage-dataset"))
        file_bytes(dataset, root=settings.project_root, limit=268435456)
    gate = StageGate(database, settings, clock, profile)
    with database.locked_session() as session:
        state = session.get(BotState, 1)
        if state is None or state.session_id is not None or state.desired_state != "paused":
            raise TradingDisabled(
                "stage review requires a stopped, paused runtime; import never pauses/resumes it"
            )
        if session.scalar(select(Trade.id).where(Trade.status.in_(("open", "unknown"))).limit(1)) is not None:
            raise TradingDisabled("stage review requires a reconciled flat ledger")
        if (
            session.scalar(
                select(OrderIntent.id)
                .where(OrderIntent.state.not_in(("reconciled", "rejected", "canceled")))
                .limit(1)
            )
            is not None
        ):
            raise TradingDisabled("unsettled intents prevent stage review")
        gate._validate_evidence(evidence, evidence.stage, session)
        existing = session.scalar(
            select(DeploymentEvidence).where(
                DeploymentEvidence.artifact_sha256 == digest,
                DeploymentEvidence.stage == evidence.stage,
                DeploymentEvidence.owner_reviewed_by == identity.owner_id,
                DeploymentEvidence.passed.is_(True),
                DeploymentEvidence.revoked.is_(False),
            )
        )
        if existing is not None:
            return existing.id
        session.add(evidence)
        session.flush()
        database.add_audit(
            session,
            "owner.stage_evidence_reviewed",
            "owner",
            {
                "stage": evidence.stage,
                "evidence_id": evidence.id,
                "report_sha256": digest,
                "owner_id": identity.owner_id,
                "authentication": "fresh_telegram_initdata_hmac",
                "grants_resume": False,
                "grants_live": False,
            },
        )
        return evidence.id


def export_ledger_stage(
    database,
    settings,
    clock,
    *,
    stage: str,
    account_key: str,
    started_at: datetime,
    finished_at: datetime,
    output: Path,
):
    """Export qualifying native paper/demo ledgers; not owner approval or live permission."""
    if stage not in {"paper", "demo"} or not isinstance(account_key, str) or len(account_key) > 160:
        raise TradingDisabled("paper/demo and an exact private account scope are required")
    start, end = utc_time(started_at), utc_time(finished_at)
    if not start < end <= clock.now():
        raise TradingDisabled("stage dates must be completed, aware and nonfuture")
    profile = native_profile(database, settings, clock)
    with database.session() as session:
        trades = session.scalars(
            select(Trade)
            .where(
                Trade.account_key == account_key,
                Trade.mode == stage,
                Trade.status == "closed",
                Trade.open_time >= start,
                Trade.close_time <= end,
            )
            .order_by(Trade.close_time, Trade.id)
        ).all()
        if len(trades) > 200000 or not trades:
            raise TradingDisabled("empty or oversized stage ledger")
        rows = [
            {
                "status": "closed",
                "currency": row.currency,
                "net_profit_account": str(row.profit),
                "commission_account": str(row.commission),
                "swap_account": str(row.swap),
                "open_time": row.open_time.isoformat(),
                "close_time": row.close_time.isoformat(),
            }
            for row in trades
        ]
        points, prior, gaps = [], start, 0
        for snap in session.scalars(
            select(AccountSnapshot)
            .where(
                AccountSnapshot.account_key == account_key,
                AccountSnapshot.mode == stage,
                AccountSnapshot.time >= start,
                AccountSnapshot.time <= end,
            )
            .order_by(AccountSnapshot.time, AccountSnapshot.id)
        ):
            if len(points) >= 1000000:
                raise TradingDisabled("stage account journal exceeds its bound")
            credit = Decimal(snap.metadata_json.get("credit", "0"))
            points.append(EquityPoint(snap.time, snap.balance, snap.equity, credit, snap.cash_flow_total))
            gaps += int((snap.time - prior).total_seconds() > settings.risk_observation_max_age_seconds * 4)
            prior = snap.time
        gaps += int(
            not points or (end - prior).total_seconds() > settings.risk_observation_max_age_seconds * 4
        )
        metrics = summarize(
            rows, points, currency=trades[0].currency, timezone=settings.trading_day_timezone, gaps=gaps
        )
        charges = ZERO
        for deal in session.scalars(
            select(BrokerDeal).where(
                BrokerDeal.account_key == account_key,
                BrokerDeal.mode == stage,
                BrokerDeal.time >= start,
                BrokerDeal.time <= end,
                BrokerDeal.position_identifier.is_(None),
            )
        ):
            if deal.type not in {"balance", "credit"}:
                charges += deal.profit + deal.commission + deal.swap + deal.fee
        gains, losses = (
            Decimal(metrics["gross_net_wins_account"]),
            Decimal(metrics["gross_net_losses_account"]),
        )
        losses += max(ZERO, -charges)
        metrics["profit_factor"] = str(gains / losses) if losses > ZERO else None
        metrics["unallocated_charges_account"] = str(charges)
        report = {
            "format": "reflex-stage-v1",
            "stage": stage,
            "source": "mt5_real_data" if stage == "paper" else "mt5_demo",
            "strategy_config_hash": settings.strategy_fingerprint(),
            "code_hash": profile.code_hash,
            "model_sha256": profile.model_sha256,
            "started_at": start.isoformat(),
            "finished_at": end.isoformat(),
            "account_key": account_key,
            "metrics": metrics,
            "dataset_sha256": sha256_json(
                {
                    "trades": [(row.id, row.order_intent_id, str(row.profit)) for row in trades],
                    "equity": [point.to_dict() for point in points],
                }
            ),
            "owner_review_required": True,
            "grants_live": False,
            "grants_resume": False,
        }
        candidate = evidence_from(
            report,
            path="unused-export.json",
            digest="0" * 64,
            owner_id=settings.telegram_owner_id,
            created_at=clock.now(),
        )
        gate = StageGate(database, settings, clock, profile)
        # Metrics and native intent/deal/continuous account proofs are not optional.
        gate._verify_stage_ledger(session, candidate, stage)
        if (
            gaps != 0
            or (end - start).total_seconds()
            < (settings.stage_min_paper_days if stage == "paper" else settings.stage_min_demo_days) * 86400
        ):
            raise TradingDisabled("stage duration or unexplained coverage gaps fail the promotion policy")
        if (
            candidate.metrics_json["profit_factor"] is None
            or Decimal(candidate.metrics_json["max_drawdown_percent"]) > settings.stage_max_drawdown_percent
        ):
            raise TradingDisabled("finite profit factor and drawdown policy required")
    # Private data artifact; no DB insertion, activation, resume or network operation.
    output = Path(output).absolute()
    if not output.is_relative_to(settings.project_root.resolve()):
        raise TradingDisabled("stage export must remain inside the configured project")
    if any(item.is_symlink() for item in (output, *output.parents)):
        raise TradingDisabled("symlinked stage export forbidden")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = write_json(output, report)
    return {
        "path": str(output.relative_to(settings.project_root.resolve())),
        **info,
        "owner_review_required": True,
        "evidence_inserted": False,
        "live_enabled": False,
    }
