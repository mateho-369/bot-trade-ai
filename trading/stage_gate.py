"""Artifact-bound promotion and owner live confirmation. No reports are fabricated."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import Database
from core.models import (
    AccountSnapshot,
    BotState,
    BrokerDeal,
    DeploymentEvidence,
    OrderIntent,
    OwnerApproval,
    Trade,
)
from core.security import canonical_json, sha256_json
from core.settings import OperatingMode, Settings
from trading.risk_types import RuntimeProfile
from trading.types import AccountInfo, AccountKind, Clock, SourceKind, TradingDisabled, valid_key


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate artifact key")
        result[key] = value
    return result


def read_report(path, limit: int = 1048576, *, expected_sha256: str | None = None) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise TradingDisabled("stage artifact missing/oversized/symlinked")

    def bad_constant(value):
        raise ValueError("nonfinite stage JSON")

    try:
        raw = path.read_bytes()
        if len(raw) > limit or (
            expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError("report digest/size")
        report = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=bad_constant)
        if not isinstance(report, dict):
            raise ValueError("object required")

        # Bound recursive data as well as input bytes.
        def walk(value, depth=0):
            if depth > 16:
                raise ValueError("deep stage JSON")
            if isinstance(value, dict):
                for child in value.values():
                    walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)

        walk(report)
        canonical_json(report)
        return report
    except Exception:
        raise TradingDisabled("invalid stage artifact; raw contents suppressed") from None


@dataclass(frozen=True, slots=True)
class LiveChallenge:
    approval_id: str
    nonce: str = field(repr=False)
    request_hash: str
    expires_at: datetime


class StageGate:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile

    def _validate_evidence(self, evidence: DeploymentEvidence, stage: str, session: Session) -> None:
        cfg, profile, now = self.settings, self.profile, self.clock.now()
        if (
            evidence.stage != stage
            or not evidence.passed
            or evidence.revoked
            or cfg.telegram_owner_id is None
            or evidence.owner_reviewed_by != cfg.telegram_owner_id
            or evidence.strategy_config_hash != cfg.strategy_fingerprint()
            or evidence.code_hash != profile.code_hash
            or evidence.model_sha256 != profile.model_sha256
        ):
            raise TradingDisabled("stage evidence policy/model/owner binding is invalid")
        if not evidence.started_at < evidence.finished_at <= now or evidence.created_at > now + timedelta(
            seconds=2
        ):
            raise TradingDisabled("stage evidence dates are incomplete/future")
        path = cfg.resolve_path(evidence.artifact_path)
        report = read_report(path, expected_sha256=evidence.artifact_sha256)
        expected = {
            "format": "reflex-stage-v1",
            "stage": stage,
            "strategy_config_hash": cfg.strategy_fingerprint(),
            "code_hash": profile.code_hash,
            "model_sha256": profile.model_sha256,
            "started_at": evidence.started_at.isoformat(),
            "finished_at": evidence.finished_at.isoformat(),
            "account_key": evidence.account_key,
            "metrics": evidence.metrics_json,
        }
        if any(report.get(key) != value for key, value in expected.items()):
            raise TradingDisabled("stage report does not bind its database record")
        required_source = {"backtest": "historical_real", "paper": "mt5_real_data", "demo": "mt5_demo"}[stage]
        if report.get("source") != required_source:
            raise TradingDisabled("synthetic/test data cannot qualify for broker promotion")
        try:
            valid_key(report.get("dataset_sha256"))
        except Exception:
            raise TradingDisabled("stage dataset digest is invalid") from None
        if stage == "backtest":
            dataset = cfg.resolve_path(report.get("dataset_path", "missing-stage-dataset"))
            if dataset.is_symlink() or not dataset.is_file() or dataset.stat().st_size > 268435456:
                raise TradingDisabled("reviewed historical dataset is missing/oversized/symlinked")
            digest = hashlib.sha256()
            with dataset.open("rb") as handle:
                for block in iter(lambda: handle.read(1048576), b""):
                    digest.update(block)
            if digest.hexdigest() != report["dataset_sha256"]:
                raise TradingDisabled("historical dataset integrity mismatch")
        metrics = report["metrics"]
        try:
            count = metrics["closed_trades"]
            factor = Decimal(str(metrics["profit_factor"]))
            drawdown = Decimal(str(metrics["max_drawdown_percent"]))
            gaps = metrics["unexplained_gaps"]
            if type(count) is not int or count < cfg.stage_min_trades or type(gaps) is not int or gaps != 0:
                raise ValueError("insufficient trades/coverage")
            if (
                not factor.is_finite()
                or factor < cfg.stage_min_profit_factor
                or not drawdown.is_finite()
                or not 0 <= drawdown <= cfg.stage_max_drawdown_percent
            ):
                raise ValueError("risk/performance gate")
            if metrics.get("costs_included") is not True or metrics.get("lookahead_free") is not True:
                raise ValueError("costs/chronology not evaluated")
            minimum = {"backtest": 0, "paper": cfg.stage_min_paper_days, "demo": cfg.stage_min_demo_days}[
                stage
            ]
            if (evidence.finished_at - evidence.started_at).total_seconds() < minimum * 86400:
                raise ValueError("stage too short")
        except Exception:
            raise TradingDisabled("stage metrics do not meet the reviewed promotion policy") from None
        if stage in {"paper", "demo"}:
            self._verify_stage_ledger(session, evidence, stage)

    def _verify_stage_ledger(self, session: Session, evidence: DeploymentEvidence, stage: str) -> None:
        """Artifact assertions alone cannot turn 5 synthetic trades into promotion."""
        cfg = self.settings
        if not evidence.account_key:
            raise TradingDisabled("market stage lacks its actual account scope")
        trades = session.scalars(
            select(Trade).where(
                Trade.account_key == evidence.account_key,
                Trade.mode == stage,
                Trade.status == "closed",
                Trade.open_time >= evidence.started_at,
                Trade.close_time <= evidence.finished_at,
            )
        ).all()
        valid = []
        for trade in trades:
            meta = trade.features_json.get("execution", {})
            if (
                meta.get("data_source") == "mt5"
                and meta.get("code_hash") == self.profile.code_hash
                and meta.get("model_sha256") == self.profile.model_sha256
                and meta.get("strategy_config_hash") == cfg.strategy_fingerprint()
                and meta.get("version") == 1
                and meta.get("entry_deal_tickets")
                and trade.order_intent_id
                and trade.close_time is not None
            ):
                valid.append(trade)
        if len(valid) != evidence.metrics_json["closed_trades"] or len(valid) < cfg.stage_min_trades:
            raise TradingDisabled("stage count is not backed by real-data reconciled trade records")
        if len({trade.currency for trade in valid}) != 1:
            raise TradingDisabled("mixed-currency stage return series")
        self._verify_trade_proofs(session, evidence, stage, valid)
        wins = sum((max(Decimal("0"), trade.profit) for trade in valid), Decimal("0"))
        losses = sum((max(Decimal("0"), -trade.profit) for trade in valid), Decimal("0"))
        unallocated = session.scalars(
            select(BrokerDeal).where(
                BrokerDeal.account_key == evidence.account_key,
                BrokerDeal.mode == stage,
                BrokerDeal.time >= evidence.started_at,
                BrokerDeal.time <= evidence.finished_at,
                BrokerDeal.position_identifier.is_(None),
            )
        ).all()
        # Unattributed charges are costs too; deposits/credit are NOT profit.
        charges = sum(
            (
                row.profit + row.commission + row.swap + row.fee
                for row in unallocated
                if row.type not in {"balance", "credit"}
            ),
            Decimal("0"),
        )
        losses += max(Decimal("0"), -charges)
        if losses <= 0 or wins / losses < cfg.stage_min_profit_factor:
            raise TradingDisabled("stage lacks a finite qualifying cost-inclusive ledger profit factor")
        if abs(wins / losses - Decimal(str(evidence.metrics_json["profit_factor"]))) > Decimal("0.000001"):
            raise TradingDisabled("reported profit factor disagrees with its ledger")
        snapshots = session.scalars(
            select(AccountSnapshot)
            .where(
                AccountSnapshot.account_key == evidence.account_key,
                AccountSnapshot.mode == stage,
                AccountSnapshot.time >= evidence.started_at,
                AccountSnapshot.time <= evidence.finished_at,
            )
            .order_by(AccountSnapshot.time, AccountSnapshot.id)
            .execution_options(yield_per=1000)
        )
        maximum_gap = cfg.risk_observation_max_age_seconds * 4
        peak, previous_cash, previous_time, drawdown, count = (
            Decimal("0"),
            None,
            evidence.started_at,
            Decimal("0"),
            0,
        )
        for snapshot in snapshots:
            count += 1
            if count > 1000000:
                raise TradingDisabled("stage exceeds bounded account journal; review/archive it")
            metadata = snapshot.metadata_json
            if (
                metadata.get("version") != 1
                or metadata.get("source") != "mt5"
                or metadata.get("code_hash") != self.profile.code_hash
                or metadata.get("model_sha256") != self.profile.model_sha256
                or metadata.get("strategy_config_hash") != cfg.strategy_fingerprint()
                or (snapshot.time - previous_time).total_seconds() > maximum_gap
            ):
                raise TradingDisabled("stage account source/code/continuous coverage is unverified")
            credit = Decimal(metadata["credit"])
            effective = snapshot.equity - credit
            if not credit.is_finite() or credit < 0 or effective <= 0:
                raise TradingDisabled("stage credit-adjusted equity is invalid")
            if previous_cash is None:
                previous_cash = snapshot.cash_flow_total
            peak = max(effective, peak + snapshot.cash_flow_total - previous_cash)
            drawdown = max(drawdown, (peak - effective) * 100 / peak)
            previous_cash, previous_time = snapshot.cash_flow_total, snapshot.time
        if not count or (evidence.finished_at - previous_time).total_seconds() > maximum_gap:
            raise TradingDisabled("stage start/end account coverage is incomplete")
        if drawdown > cfg.stage_max_drawdown_percent or drawdown > Decimal(
            str(evidence.metrics_json["max_drawdown_percent"])
        ) + Decimal("0.000001"):
            raise TradingDisabled("reported drawdown understates the sampled cash/credit-adjusted ledger")

    def _verify_trade_proofs(self, session, evidence, stage, trades):
        """Recheck original intent/deal evidence; labels alone do not confer eligibility."""
        intents = {
            row.id: row
            for row in session.scalars(
                select(OrderIntent).where(
                    OrderIntent.account_key == evidence.account_key, OrderIntent.mode == stage
                )
            ).all()
        }
        ledger = session.scalars(
            select(BrokerDeal).where(
                BrokerDeal.account_key == evidence.account_key,
                BrokerDeal.mode == stage,
                BrokerDeal.time >= evidence.started_at - timedelta(seconds=2),
                BrokerDeal.time <= evidence.finished_at,
            )
        ).all()
        indexed = {row.ticket: row for row in ledger}
        for trade in trades:
            meta = trade.features_json["execution"]
            intent = intents.get(trade.order_intent_id)
            if intent is not None and intent.request.get("demo_fast_track"):
                # DEMO_FAST_TRACK orders skipped the stage chain: never promotion evidence.
                raise TradingDisabled("demo_fast_track trades are not promotion evidence")
            if (
                intent is None
                or intent.state != "reconciled"
                or intent.request.get("authorized") is not True
                or intent.request.get("data_source") != "mt5"
                or intent.request.get("code_hash") != self.profile.code_hash
                or intent.request.get("model_sha256") != self.profile.model_sha256
                or intent.request.get("strategy_config_hash") != self.settings.strategy_fingerprint()
            ):
                raise TradingDisabled("stage trade lacks its original authorized native-provenance intent")
            entries = [indexed.get(ticket) for ticket in meta["entry_deal_tickets"]]
            if not entries or any(
                row is None
                or row.position_identifier != trade.position_identifier
                or row.magic != self.settings.mt5_magic_number
                or row.entry != "in"
                or row.type != trade.direction
                or row.symbol != trade.symbol
                for row in entries
            ):
                raise TradingDisabled("stage entry ledger ownership is unproved")
            legs = [row for row in ledger if row.position_identifier == trade.position_identifier]
            exits = [row for row in legs if row.entry in {"out", "out_by"} and row.type in {"buy", "sell"}]
            original = Decimal(meta["original_volume"])
            if (
                sum((row.volume for row in entries), Decimal("0")) != original
                or sum((row.volume for row in exits), Decimal("0")) != original
                or sum((row.profit + row.commission + row.swap + row.fee for row in legs), Decimal("0"))
                != trade.profit
            ):
                raise TradingDisabled("stage P&L/volumes are not backed by complete fill legs")

    def required_evidence(self, session: Session, account: AccountInfo) -> tuple[int, ...]:
        cfg, source = self.settings, self.profile.data_source
        if cfg.mode == OperatingMode.BACKTEST:
            return ()  # Run may be synthetic, but this NEVER qualifies its report.
        if cfg.mode == OperatingMode.PAPER and source in {SourceKind.SYNTHETIC, SourceKind.TEST_SDK}:
            return ()  # Development simulation only, explicitly ineligible for promotion.
        if source != SourceKind.MT5:
            raise TradingDisabled("native promotion requires verified real MT5 provenance")
        if cfg.mode == OperatingMode.DEMO and (
            account.source != SourceKind.MT5 or account.kind != AccountKind.DEMO
        ):
            raise TradingDisabled("demo stage requires the actual native DEMO account")
        if cfg.mode == OperatingMode.LIVE and (
            account.source != SourceKind.MT5 or account.kind != AccountKind.REAL
        ):
            raise TradingDisabled("live stage requires the actual native REAL account")
        stages = {
            OperatingMode.PAPER: ("backtest",),
            OperatingMode.DEMO: ("backtest", "paper"),
            OperatingMode.LIVE: ("backtest", "paper", "demo"),
        }[cfg.mode]
        selected = []
        previous_end = None
        for stage in stages:
            rows = session.scalars(
                select(DeploymentEvidence)
                .where(
                    DeploymentEvidence.stage == stage,
                    DeploymentEvidence.passed.is_(True),
                    DeploymentEvidence.revoked.is_(False),
                    DeploymentEvidence.code_hash == self.profile.code_hash,
                    DeploymentEvidence.model_sha256 == self.profile.model_sha256,
                    DeploymentEvidence.strategy_config_hash == cfg.strategy_fingerprint(),
                )
                .order_by(DeploymentEvidence.finished_at.desc())
                .limit(100)
            ).all()
            chosen = None
            for row in rows:
                try:
                    self._validate_evidence(row, stage, session)
                    if previous_end is not None and row.started_at < previous_end:
                        continue
                    chosen = row
                    break
                except (TradingDisabled, ValueError, TypeError, KeyError, OSError):
                    continue
            if chosen is None:
                raise TradingDisabled("missing valid chronological " + stage + " promotion evidence")
            selected.append(chosen.id)
            previous_end = chosen.finished_at
        return tuple(selected)

    def live_request_hash(self, account_key: str, session_id: str, evidence_ids: tuple[int, ...]) -> str:
        return sha256_json(
            {
                "purpose": "live_enable",
                "account_key": account_key,
                "session_id": session_id,
                "config_hash": self.settings.safety_fingerprint(),
                "code_hash": self.profile.code_hash,
                "model_sha256": self.profile.model_sha256,
                "evidence_ids": evidence_ids,
            }
        )

    def demo_fast_track_eligible(self, account: AccountInfo) -> bool:
        """DEMO_FAST_TRACK scope: owner setting AND DEMO mode AND native MT5 AND the TERMINAL itself
        reports a DEMO trade-mode account. REAL, CONTEST, unknown accounts and LIVE fail closed."""
        cfg = self.settings
        return bool(
            cfg.demo_fast_track
            and cfg.mode == OperatingMode.DEMO
            and not cfg.live_trading
            and self.profile.data_source == SourceKind.MT5
            and isinstance(account, AccountInfo)
            and account.source == SourceKind.MT5
            and account.kind == AccountKind.DEMO
        )

    def entry_evidence(self, session: Session, account: AccountInfo) -> tuple[tuple[int, ...], bool]:
        """(evidence ids, demo_fast_track). Normal evidence first; the fast track never applies to
        LIVE/REAL and never creates evidence. Raises TradingDisabled exactly like required_evidence."""
        try:
            return self.required_evidence(session, account), False
        except TradingDisabled:
            if self.demo_fast_track_eligible(account):
                return (), True
            raise

    def live_confirmed(
        self, session: Session, account: AccountInfo, session_id: str, evidence: tuple[int, ...]
    ) -> bool:
        digest = self.live_request_hash(account.key, session_id, evidence)
        rows = session.scalars(
            select(OwnerApproval).where(
                OwnerApproval.purpose == "live_enable",
                OwnerApproval.status == "approved",
                OwnerApproval.request_hash == digest,
                OwnerApproval.session_id == session_id,
                OwnerApproval.account_key == account.key,
                OwnerApproval.config_hash == self.settings.safety_fingerprint(),
                OwnerApproval.owner_id == self.settings.telegram_owner_id,
                OwnerApproval.expires_at > self.clock.now(),
            )
        ).all()
        return any(tuple(row.evidence_ids) == evidence for row in rows)

    def request_live(self, account: AccountInfo, session_id: str, owner_id: int) -> LiveChallenge:
        if (
            self.settings.mode != OperatingMode.LIVE
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may request live confirmation")
        nonce = secrets.token_urlsafe(32)
        with self.database.locked_session() as session:
            state = session.get(BotState, 1)
            if state is None or state.session_id != session_id or state.kill_switch_active:
                raise TradingDisabled("live confirmation runtime/session is invalid")
            evidence = self.required_evidence(session, account)
            digest = self.live_request_hash(account.key, session_id, evidence)
            expiry = self.clock.now() + timedelta(seconds=self.settings.live_approval_ttl_seconds)
            row = OwnerApproval(
                purpose="live_enable",
                request_hash=digest,
                nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                expires_at=expiry,
                owner_id=owner_id,
                session_id=session_id,
                account_key=account.key,
                config_hash=self.settings.safety_fingerprint(),
                evidence_ids=list(evidence),
                time=self.clock.now(),
            )
            session.add(row)
            session.flush()
            self.database.add_audit(
                session, "owner.live_requested", "owner", {"approval_id": row.id, "account": account.key}
            )
            challenge = LiveChallenge(row.id, nonce, digest, expiry)
        return challenge

    def confirm_live(self, approval_id: str, nonce: str, owner_id: int) -> None:
        if (
            self.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may confirm live")
        with self.database.locked_session() as session:
            row, state = session.get(OwnerApproval, approval_id), session.get(BotState, 1)
            if (
                row is None
                or state is None
                or row.status != "pending"
                or row.purpose != "live_enable"
                or row.owner_id != owner_id
                or row.session_id != state.session_id
                or row.config_hash != self.settings.safety_fingerprint()
                or row.expires_at <= self.clock.now()
                or not hmac.compare_digest(hashlib.sha256(nonce.encode()).hexdigest(), row.nonce_hash)
            ):
                raise TradingDisabled("live confirmation nonce/session/configuration/expiry is invalid")
            expected = self.live_request_hash(row.account_key, row.session_id, tuple(row.evidence_ids))
            if row.request_hash != expected:
                raise TradingDisabled("live confirmation payload changed")
            row.status, row.decided_at = "approved", self.clock.now()
            self.database.add_audit(
                session, "owner.live_confirmed", "owner", {"approval_id": row.id, "owner_id": owner_id}
            )
