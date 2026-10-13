"""TEST ONLY hand-authored artifacts and native tags. No genuine promotion or price evidence."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from backtesting.promotion import export_ledger_stage, import_reviewed_stage, native_profile
from core.database import Database
from core.models import AuditLog, BotState, DeploymentEvidence, OrderIntent, Trade
from core.security import canonical_json
from tests.risk_helpers import BAD_OPERATOR, MOMENT, OPERATOR_ID, OWNER, config
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")  # Native configuration only; NO client/SDK is constructed.
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    profile = RuntimeProfile.current(cfg, SourceKind.MT5)
    dataset = tmp_path / "reviewed_TEST_ONLY.csv"
    dataset.write_text("TEST ONLY invented bytes, NOT historical or stage qualification evidence\n")
    report = {
        "format": "reflex-stage-v1",
        "stage": "backtest",
        "source": "historical_real",
        "account_key": None,
        "code_hash": profile.code_hash,
        "model_sha256": profile.model_sha256,
        "strategy_config_hash": cfg.strategy_fingerprint(),
        "started_at": (MOMENT - timedelta(days=10)).isoformat(),
        "finished_at": (MOMENT - timedelta(days=1)).isoformat(),
        "metrics": {
            "closed_trades": 100,
            "profit_factor": "1.5",
            "max_drawdown_percent": "1",
            "unexplained_gaps": 0,
            "costs_included": True,
            "lookahead_free": True,
        },
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "dataset_path": dataset.name,
    }
    path = tmp_path / "TEST_ONLY_REPORT.json"
    path.write_text(canonical_json(report))
    yield cfg, db, clock, report, path
    db.close()


def invoke(sample, **changes):
    cfg, db, clock, _, path = sample
    values = {
        "report_path": path,
        "confirm_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "operator": OWNER,
    }
    values.update(changes)
    return import_reviewed_stage(db, cfg, clock, **values)


def test_local_operator_import_is_append_only_idempotent_and_not_resume_or_live(sample):
    cfg, db, clock, _, path = sample
    evidence_id = invoke(sample)
    assert invoke(sample) == evidence_id
    with db.session() as session:
        evidence = session.get(DeploymentEvidence, evidence_id)
        assert evidence.stage == "backtest" and evidence.owner_reviewed_by == OPERATOR_ID
        state = session.get(BotState, 1)
        assert state.desired_state == "paused" and state.session_id is None
        assert len(session.scalars(select(DeploymentEvidence)).all()) == 1
        assert session.scalar(select(Trade)) is None and session.scalar(select(OrderIntent)) is None
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "local_operator.stage_evidence_reviewed")
        )
        assert audit.details["authentication"] == "current_local_operator"
        assert not audit.details["grants_resume"] and not audit.details["grants_live"]
        assert "initData" not in canonical_json(audit.details)
    assert not cfg.live_trading and path.is_file()


@pytest.mark.parametrize("operator", [None, 42, True, BAD_OPERATOR])
def test_stage_import_rejects_noncurrent_local_operators_without_db_mutation(sample, operator):
    _, db, _, _, _ = sample
    with pytest.raises(TradingDisabled, match="local operator"):
        invoke(sample, operator=operator)
    with db.session() as session:
        assert session.scalar(select(DeploymentEvidence)) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "reflex-backtest-v1"),
        ("source", "synthetic_fixture"),
        ("source", "historical"),
        ("source", "test_sdk"),
        ("research_only", True),
        ("promotion_eligible", False),
        ("code_hash", "b" * 64),
        ("model_sha256", "c" * 64),
        ("strategy_config_hash", "a" * 64),
        ("finished_at", "2030-01-01T00:00:00+00:00"),
    ],
)
def test_labels_research_and_stale_scopes_are_not_local_qualification(sample, field, value):
    _, db, _, report, path = sample
    report[field] = value
    path.write_text(canonical_json(report))
    with pytest.raises(TradingDisabled):
        invoke(sample)
    with db.session() as session:
        assert session.scalar(select(DeploymentEvidence)) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("closed_trades", True),
        ("closed_trades", 99),
        ("profit_factor", None),
        ("profit_factor", "1.0"),
        ("max_drawdown_percent", "6"),
        ("unexplained_gaps", 1),
        ("costs_included", False),
        ("lookahead_free", False),
    ],
)
def test_import_rechecks_quality_and_cost_chronology_not_report_labels(sample, field, value):
    _, db, _, report, path = sample
    report["metrics"][field] = value
    path.write_text(canonical_json(report))
    with pytest.raises(TradingDisabled):
        invoke(sample)
    with db.session() as session:
        assert session.scalar(select(DeploymentEvidence)) is None


def test_local_confirmation_digest_binds_exact_file_bytes(sample):
    cfg, db, clock, _, path = sample
    old = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text() + "\n")
    with pytest.raises(TradingDisabled):
        import_reviewed_stage(db, cfg, clock, report_path=path, confirm_sha256=old, operator=OWNER)


@pytest.mark.parametrize("kind", ["active_session", "running", "dataset_changed", "symlink", "outside"])
def test_active_state_path_or_dataset_revisions_block_import(sample, kind, tmp_path):
    _, db, _, report, path = sample
    if kind in {"active_session", "running"}:
        with db.session() as session:
            state = session.get(BotState, 1)
            if kind == "running":
                state.desired_state = "running"
            else:
                state.session_id = "TEST_SESSION"
    elif kind == "dataset_changed":
        (tmp_path / report["dataset_path"]).write_text("changed")
    elif kind == "symlink":
        target = path.with_suffix(".original")
        path.rename(target)
        path.symlink_to(target)
    else:
        report["dataset_path"] = "../unreviewed.csv"
        path.write_text(canonical_json(report))
    with pytest.raises((TradingDisabled, ValueError)):
        invoke(sample)


def test_mock_and_backtest_profile_cannot_export_native_qualification(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    try:
        with pytest.raises(TradingDisabled):
            native_profile(db, cfg, ManualClock(MOMENT))
        with pytest.raises(TradingDisabled):
            export_ledger_stage(
                db,
                cfg,
                ManualClock(MOMENT),
                stage="paper",
                account_key="synthetic:test",
                started_at=MOMENT - timedelta(days=15),
                finished_at=MOMENT,
                output=tmp_path / "do_not_write.json",
            )
        assert not (tmp_path / "do_not_write.json").exists()
    finally:
        db.close()


@pytest.mark.parametrize(
    "stage,scope,started,ended",
    [
        ("live", "x", MOMENT - timedelta(days=15), MOMENT),
        ("paper", "x", MOMENT, MOMENT - timedelta(seconds=1)),
        ("demo", "x", MOMENT - timedelta(days=15), MOMENT + timedelta(seconds=1)),
    ],
)
def test_export_rejects_bad_stage_dates_and_scope(sample, tmp_path, stage, scope, started, ended):
    cfg, db, clock, _, _ = sample
    with pytest.raises(TradingDisabled):
        export_ledger_stage(
            db,
            cfg,
            clock,
            stage=stage,
            account_key=scope,
            started_at=started,
            finished_at=ended,
            output=tmp_path / "never.json",
        )
    assert not (tmp_path / "never.json").exists()


def test_empty_native_tagged_database_is_not_trade_evidence(sample, tmp_path):
    cfg, db, clock, _, _ = sample
    with pytest.raises(TradingDisabled):
        export_ledger_stage(
            db,
            cfg,
            clock,
            stage="paper",
            account_key="paper:TEST",
            started_at=MOMENT - timedelta(days=15),
            finished_at=MOMENT,
            output=tmp_path / "never.json",
        )
    assert not (tmp_path / "never.json").exists()
