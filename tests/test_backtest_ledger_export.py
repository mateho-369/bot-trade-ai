"""TEST ONLY fabricated native tags/legs/snapshots. No genuine ledger, broker or stage is qualified.

Exercise the positive export shape against the existing original proof verifier without
weakening count/duration/PF/gap policy. Never confuse fixture tags with authenticated data.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from backtesting.promotion import export_ledger_stage
from core.models import AccountSnapshot, BrokerDeal, DeploymentEvidence, OrderIntent, Trade
from tests.risk_helpers import MOMENT
from tests.test_stage_ledger import sample as invented_ledger  # noqa: F401 -- explicit pytest fixture
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


@pytest.mark.parametrize("stage", ["paper", "demo"])
def test_structural_native_ledger_export_positive_is_fixture_not_authentic_evidence(
    invented_ledger,  # noqa: F811 -- explicit pytest fixture injection
    stage,
):
    cfg, db, _, _, scope = invented_ledger
    profile = RuntimeProfile.current(cfg, SourceKind.MT5)
    start = MOMENT - timedelta(days=14)
    with db.session() as session:
        session.execute(delete(DeploymentEvidence))
        original = session.scalar(select(AccountSnapshot))
        meta = {
            **original.metadata_json,
            "code_hash": profile.code_hash,
            "model_sha256": profile.model_sha256,
            "strategy_config_hash": cfg.strategy_fingerprint(),
        }
        session.execute(delete(AccountSnapshot))
        for row in session.scalars(select(Trade)):
            row.mode, row.open_time, row.close_time = (
                stage,
                start + timedelta(seconds=1),
                MOMENT - timedelta(seconds=1),
            )
            row.features_json = {
                "execution": {
                    **row.features_json["execution"],
                    "code_hash": profile.code_hash,
                    "model_sha256": profile.model_sha256,
                }
            }
        for row in session.scalars(select(OrderIntent)):
            row.mode, row.time, row.expires_at = stage, start, MOMENT
            row.request = {
                **row.request,
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
            }
        for row in session.scalars(select(BrokerDeal)):
            row.mode = stage
            row.time = start + timedelta(seconds=1) if row.entry == "in" else MOMENT - timedelta(seconds=1)
        for offset in range(0, 14 * 86400 + 1, 120):
            equity = Decimal("1050" if offset == 14 * 86400 else "1000")
            session.add(
                AccountSnapshot(
                    time=start + timedelta(seconds=offset),
                    account_key=scope,
                    mode=stage,
                    currency="USD",
                    balance=equity,
                    equity=equity,
                    margin=Decimal("0"),
                    free_margin=equity,
                    cash_flow_total=Decimal("0"),
                    metadata_json=meta,
                )
            )
    output = cfg.project_root / f"TEST_ONLY_{stage}_structure.json"
    result = export_ledger_stage(
        db,
        cfg,
        ManualClock(MOMENT),
        stage=stage,
        account_key=scope,
        started_at=start,
        finished_at=MOMENT,
        output=output,
    )
    report = json.loads(output.read_text())
    assert result["live_enabled"] is False
    assert report["grants_live"] is False and report["grants_resume"] is False
    assert report["metrics"]["closed_trades"] == 100 and report["metrics"]["unexplained_gaps"] == 0
    assert Decimal(report["metrics"]["profit_factor"]) == 2
    assert report["metrics"]["net_profit_account"] == "50.00000000"
    assert report["metrics"]["costs_included"] and report["owner_review_required"]
    with db.session() as session:
        assert session.scalar(select(DeploymentEvidence)) is None  # Export never approves/inserts.
    with pytest.raises(FileExistsError):
        export_ledger_stage(
            db,
            cfg,
            ManualClock(MOMENT),
            stage=stage,
            account_key=scope,
            started_at=start,
            finished_at=MOMENT,
            output=output,
        )
