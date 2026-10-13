"""TEST ONLY hand-authored native-tagged DB proof rows; no trading/provenance claim."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from core.database import Database
from core.models import AccountSnapshot, BrokerDeal, DeploymentEvidence, OrderIntent, Trade
from tests.risk_helpers import MOMENT, OPERATOR_ID, D, config
from trading.risk_types import RuntimeProfile
from trading.stage_gate import StageGate
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")
    db = Database(cfg)
    db.initialize()
    profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
    gate = StageGate(db, cfg, ManualClock(MOMENT), profile)
    scope = "paper:TEST_ONLY_NEVER_PROMOTION"
    start, finish = MOMENT - timedelta(seconds=120), MOMENT
    meta = {
        "version": 1,
        "data_source": "mt5",
        "source": "mt5",
        "code_hash": profile.code_hash,
        "model_sha256": profile.model_sha256,
        "strategy_config_hash": cfg.strategy_fingerprint(),
        "credit": "0",
        "original_volume": "0.02",
    }
    with db.session() as session:
        evidence = DeploymentEvidence(
            stage="paper",
            started_at=start,
            finished_at=finish,
            created_at=MOMENT,
            account_key=scope,
            strategy_config_hash=cfg.strategy_fingerprint(),
            code_hash=profile.code_hash,
            model_sha256=profile.model_sha256,
            artifact_path="TEST_ONLY",
            artifact_sha256="f" * 64,
            metrics_json={"closed_trades": 100, "profit_factor": "2", "max_drawdown_percent": "0"},
            passed=True,
            owner_reviewed_by=OPERATOR_ID,
        )
        session.add(evidence)
        for index in range(100):
            net = D("2") if index < 50 else D("-1")
            ticket, pid, iid = 9000 + index * 2, 8000 + index, "TEST-" + str(index)
            request = {
                "authorized": True,
                "data_source": "mt5",
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "strategy_config_hash": cfg.strategy_fingerprint(),
            }
            intent = OrderIntent(
                id=iid,
                idempotency_key=f"{index:064x}",
                time=start,
                expires_at=finish,
                mode="paper",
                account_key=scope,
                symbol="EURUSD",
                direction="buy",
                state="reconciled",
                request=request,
                config_hash=cfg.safety_fingerprint(),
            )
            session.add(intent)
            session.flush()
            session.add(
                Trade(
                    order_intent_id=iid,
                    account_key=scope,
                    mode="paper",
                    currency="USD",
                    symbol="EURUSD",
                    direction="buy",
                    ticket=pid,
                    position_identifier=pid,
                    volume=D("0.02"),
                    entry_price=D("1.1"),
                    sl=D("1.09"),
                    tp=D("1.12"),
                    open_time=start + timedelta(seconds=1),
                    close_time=finish - timedelta(seconds=1),
                    profit=net,
                    profit_usd=net,
                    initial_risk_usd=D("1"),
                    target_profit_usd=D("2"),
                    strategy="TEST",
                    signal_score=90,
                    ai_score=90,
                    status="closed",
                    config_hash=cfg.safety_fingerprint(),
                    features_json={"execution": {**meta, "entry_deal_tickets": [ticket]}},
                )
            )
            session.add(
                BrokerDeal(
                    account_key=scope,
                    mode="paper",
                    ticket=ticket,
                    order_ticket=ticket,
                    position_identifier=pid,
                    time=start + timedelta(seconds=1),
                    type="buy",
                    entry="in",
                    symbol="EURUSD",
                    currency="USD",
                    magic=cfg.mt5_magic_number,
                    volume=D("0.02"),
                    price=D("1.1"),
                    commission=D("-0.07"),
                )
            )
            session.add(
                BrokerDeal(
                    account_key=scope,
                    mode="paper",
                    ticket=ticket + 1,
                    order_ticket=ticket + 1,
                    position_identifier=pid,
                    time=finish - timedelta(seconds=1),
                    type="sell",
                    entry="out",
                    symbol="EURUSD",
                    currency="USD",
                    magic=cfg.mt5_magic_number,
                    volume=D("0.02"),
                    price=D("1.11"),
                    profit=net + D("0.14"),
                    commission=D("-0.07"),
                )
            )
        for offset in (0, 60, 120):
            session.add(
                AccountSnapshot(
                    time=start + timedelta(seconds=offset),
                    account_key=scope,
                    mode="paper",
                    currency="USD",
                    balance=D("1000"),
                    equity=D("1000"),
                    margin=D("0"),
                    free_margin=D("1000"),
                    cash_flow_total=D("0"),
                    metadata_json=meta,
                )
            )
        session.flush()
        eid = evidence.id
    yield cfg, db, gate, eid, scope
    db.close()


def verify(sample):
    _, db, gate, eid, _ = sample
    with db.session() as session:
        gate._verify_stage_ledger(session, session.get(DeploymentEvidence, eid), "paper")


def test_ledger_proof_calculates_finite_cost_inclusive_pf_and_coverage(sample):
    verify(sample)  # Isolated ledger verifier only; 120 seconds cannot qualify a 14-day stage.


@pytest.mark.parametrize(
    "fault",
    ["source", "code", "volume", "intent", "fee", "snapshot_source", "coverage", "credit", "pf", "drawdown"],
)
def test_proof_metrics_and_source_fail_closed_when_changed(sample, fault):
    cfg, db, _, eid, _ = sample
    with db.session() as session:
        trade = session.scalars(select(Trade).order_by(Trade.id)).first()
        if fault in {"source", "code"}:
            key = "data_source" if fault == "source" else "code_hash"
            trade.features_json = {"execution": {**trade.features_json["execution"], key: "synthetic"}}
        if fault == "volume":
            session.scalar(select(BrokerDeal).where(BrokerDeal.entry == "out").limit(1)).volume = D("0.01")
        if fault == "intent":
            session.get(OrderIntent, trade.order_intent_id).request = {"authorized": False}
        if fault == "fee":
            session.scalar(select(BrokerDeal).where(BrokerDeal.entry == "out").limit(1)).commission = D("0")
        snapshot = session.scalars(select(AccountSnapshot).order_by(AccountSnapshot.id)).first()
        if fault == "snapshot_source":
            snapshot.metadata_json = {**snapshot.metadata_json, "source": "test_sdk"}
        if fault == "coverage":
            session.get(DeploymentEvidence, eid).started_at -= timedelta(seconds=600)
        if fault == "credit":
            snapshot.metadata_json = {**snapshot.metadata_json, "credit": "1000"}
        if fault == "pf":
            row = session.get(DeploymentEvidence, eid)
            row.metrics_json = {**row.metrics_json, "profit_factor": "100"}
        if fault == "drawdown":
            later = session.scalars(select(AccountSnapshot).order_by(AccountSnapshot.id.desc())).first()
            later.equity = D("990")
    with pytest.raises(TradingDisabled):
        verify(sample)
