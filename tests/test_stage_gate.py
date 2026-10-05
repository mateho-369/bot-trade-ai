"""TEST ONLY hand-authored reports/accounts. No broker approval or promotion run.

Nonce tests stub evidence selection to isolate confirmation binding; runtime
composition NEVER uses these stubs, and TEST_SDK remains promotion-ineligible.
"""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from core.database import Database
from core.models import BotState, DeploymentEvidence, OwnerApproval
from core.security import canonical_json
from tests.risk_helpers import MOMENT, OWNER, D, config
from trading.risk_types import RuntimeProfile
from trading.runtime_state import RuntimeControl
from trading.stage_gate import StageGate, read_report
from trading.types import AccountInfo, AccountKind, ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")  # Still paper; no native client is constructed.
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    profile = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)
    gate = StageGate(db, cfg, clock, profile)
    yield cfg, db, clock, profile, gate
    db.close()


def report(sample, **overrides):
    cfg, db, clock, profile, gate = sample
    started, finished = MOMENT - timedelta(days=45), MOMENT - timedelta(days=30)
    dataset = cfg.project_root / "reports/TEST_ONLY.csv"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("TEST ONLY fixture, NOT market data\n")
    metrics = {
        "closed_trades": 100,
        "profit_factor": "2",
        "max_drawdown_percent": "2",
        "unexplained_gaps": 0,
        "costs_included": True,
        "lookahead_free": True,
    }
    row = DeploymentEvidence(
        stage="backtest",
        created_at=MOMENT,
        started_at=started,
        finished_at=finished,
        strategy_config_hash=cfg.strategy_fingerprint(),
        code_hash=profile.code_hash,
        model_sha256=profile.model_sha256,
        account_key=None,
        metrics_json=metrics,
        artifact_path="reports/backtest.json",
        artifact_sha256="a" * 64,
        passed=True,
        owner_reviewed_by=OWNER,
        revoked=False,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    body = {
        "format": "reflex-stage-v1",
        "stage": row.stage,
        "source": "historical_real",
        "strategy_config_hash": row.strategy_config_hash,
        "code_hash": row.code_hash,
        "model_sha256": row.model_sha256,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat(),
        "account_key": row.account_key,
        "metrics": row.metrics_json,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "dataset_path": "reports/TEST_ONLY.csv",
    }
    path = cfg.resolve_path(row.artifact_path)
    path.write_text(canonical_json(body))
    row.artifact_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with db.session() as session:
        session.add(row)
    return row, path


def paper_account():
    return AccountInfo(
        1,
        "TEST-PAPER-ACCOUNT",
        "USD",
        AccountKind.SIMULATED,
        SourceKind.PAPER,
        D("1000"),
        D("1000"),
        D("0"),
        D("1000"),
    )


def test_structurally_valid_reviewed_backtest_is_artifact_and_dataset_bound(sample):
    row, path = report(sample)
    with sample[1].session() as session:
        assert sample[4].required_evidence(session, paper_account()) == (row.id,)
    # This verifies policy format only. The fixture is NOT real-market evidence.
    sample[0].resolve_path("reports/TEST_ONLY.csv").write_text("changed")
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"revoked": True},
        {"owner_reviewed_by": OWNER + 1},
        {"code_hash": "e" * 64},
        {"model_sha256": "e" * 64},
        {"strategy_config_hash": "e" * 64},
        {"finished_at": MOMENT + timedelta(seconds=1)},
    ],
)
def test_unreviewed_revoked_changed_or_future_evidence_is_denied(sample, change):
    report(sample, **change)
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize(
    "field,value",
    [
        ("closed_trades", 99),
        ("closed_trades", True),
        ("profit_factor", "1.0"),
        ("profit_factor", "NaN"),
        ("max_drawdown_percent", "5.01"),
        ("max_drawdown_percent", "-1"),
        ("unexplained_gaps", 1),
        ("costs_included", False),
        ("lookahead_free", False),
    ],
)
def test_stage_metrics_fail_closed(sample, field, value):
    metrics = {
        "closed_trades": 100,
        "profit_factor": "2",
        "max_drawdown_percent": "2",
        "unexplained_gaps": 0,
        "costs_included": True,
        "lookahead_free": True,
        field: value,
    }
    report(sample, metrics_json=metrics)
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize("kind", ["hash", "source", "schema", "dataset_digest", "artifact_deleted"])
def test_bad_artifact_is_never_an_approval(sample, kind):
    row, path = report(sample)
    if kind == "artifact_deleted":
        path.unlink()
    elif kind == "hash":
        path.write_text(path.read_text() + " ")
    else:
        body = json.loads(path.read_text())
        if kind == "source":
            body["source"] = "synthetic"
        elif kind == "schema":
            body["format"] = "wrong"
        else:
            body["dataset_sha256"] = "invalid"
        path.write_text(canonical_json(body))
        with sample[1].session() as session:
            session.get(DeploymentEvidence, row.id).artifact_sha256 = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    with sample[1].session() as session:
        with pytest.raises(TradingDisabled):
            sample[4].required_evidence(session, paper_account())


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', "[]", '"scalar"'])
def test_report_parser_rejects_duplicate_nonfinite_or_wrong_root(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(TradingDisabled):
        read_report(path)


def test_deep_and_oversized_reports_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"a":' * 18 + "1" + "}" * 18)
    with pytest.raises(TradingDisabled):
        read_report(path)
    path.write_bytes(b" " * (1048576 + 1))
    with pytest.raises(TradingDisabled):
        read_report(path)


@pytest.mark.parametrize("source", [SourceKind.SYNTHETIC, SourceKind.TEST_SDK, SourceKind.PAPER])
def test_test_synthetic_or_unproven_paper_source_never_promotes_to_broker(sample, source):
    cfg, db, clock, profile, _ = sample
    demo = config(cfg.project_root, mt5_backend="real", paper_trading=False)
    gate = StageGate(db, demo, clock, replace(profile, data_source=source))
    account = replace(paper_account(), source=source, kind=AccountKind.DEMO)
    with db.session() as session:
        with pytest.raises(TradingDisabled, match="provenance"):
            gate.required_evidence(session, account)


def test_paper_demo_claims_require_actual_trade_and_account_ledger(sample):
    cfg, db, _, _, gate = sample
    row, _ = report(sample, stage="paper", account_key="paper:TEST")
    with db.session() as session:
        with pytest.raises(TradingDisabled, match="trade records"):
            gate._verify_stage_ledger(session, row, "paper")


@pytest.fixture
def live(tmp_path, monkeypatch):
    cfg = config(tmp_path, mt5_backend="real", paper_trading=False, demo_mode=False, live_trading=True)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    control = RuntimeControl(db, cfg, clock)
    control.claim()
    gate = StageGate(db, cfg, clock, RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5))
    # TEST ONLY isolates nonce logic; actual required_evidence never has this bypass.
    monkeypatch.setattr(gate, "required_evidence", lambda session, account: (1, 2, 3))
    account = replace(paper_account(), source=SourceKind.MT5, kind=AccountKind.REAL)
    yield cfg, db, clock, control, gate, account
    control.release()
    db.close()


def test_live_nonce_is_hashed_single_use_session_account_code_bound(live):
    cfg, db, _, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    assert challenge.nonce not in repr(challenge)
    with db.session() as session:
        stored = session.get(OwnerApproval, challenge.approval_id)
        assert stored.nonce_hash != challenge.nonce and stored.status == "pending"
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, "wrong", OWNER)
    gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)
    with db.session() as session:
        assert gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
        assert not gate.live_confirmed(session, account, "other-session", (1, 2, 3))
        assert not gate.live_confirmed(
            session, replace(account, server="different"), control.session_id, (1, 2, 3)
        )
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 4))
        changed = StageGate(db, cfg, live[2], RuntimeProfile("e" * 64, "d" * 64, SourceKind.MT5))
        assert not changed.live_confirmed(session, account, control.session_id, (1, 2, 3))
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)


@pytest.mark.parametrize("case", ["owner", "expiry", "session", "configuration", "payload"])
def test_live_confirmation_cannot_override_binding(live, case):
    cfg, db, clock, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    owner = OWNER + 1 if case == "owner" else OWNER
    if case == "expiry":
        clock.advance(timedelta(seconds=cfg.live_approval_ttl_seconds + 1))
    with db.session() as session:
        row = session.get(OwnerApproval, challenge.approval_id)
        if case == "session":
            session.get(BotState, 1).session_id = "new-session"
        if case == "configuration":
            row.config_hash = "f" * 64
        if case == "payload":
            row.evidence_ids = [9]
    with pytest.raises(TradingDisabled):
        gate.confirm_live(challenge.approval_id, challenge.nonce, owner)


def test_confirmed_live_expires_without_rearming(live):
    cfg, db, clock, control, gate, account = live
    challenge = gate.request_live(account, control.session_id, OWNER)
    gate.confirm_live(challenge.approval_id, challenge.nonce, OWNER)
    clock.advance(timedelta(seconds=cfg.live_approval_ttl_seconds + 1))
    with db.session() as session:
        assert not gate.live_confirmed(session, account, control.session_id, (1, 2, 3))
