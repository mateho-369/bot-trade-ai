"""Attack even rehashed local manifests; actual journal/economic/private-row consistency must still bind."""

import pytest

from backtesting.audit.runner import audit_bundle
from tests.bundle_audit_helpers import (
    checkpoint_edit,
    clone_bundle,
    edit_db,
    edit_json,
    edit_lines,
    inventory,
    lines,
    reseal_for_attack,
    write_lines,
)
from tests.test_bundle_audit_integrity import blocked


def test_completed_replay_is_consistent_research_not_stage_or_owner(completed_replay):
    before = inventory(completed_replay)
    result = audit_bundle(completed_replay)
    assert result["overall"] == "consistent_research_bundle" and result["integrity_verified"]
    assert result["observations"]["metric_fields_recomputed"] == 32
    assert (
        result["observations"]["trade_rows"] == 1
        and result["observations"]["closed_private_memory_ledger_checked"]
    )
    for name in (
        "stage_evidence",
        "trading_authorized",
        "historical_provenance_verified",
        "owner_authenticated",
        "production_model_activated",
        "native_validation_complete",
        "automatic_resume",
        "financial_history_reset",
    ):
        assert result[name] is False
    assert result["application_state_writes"] == 0 and result["original_db_opened_by_sqlite"] is False
    assert before == inventory(completed_replay)


@pytest.mark.parametrize(
    "field,value",
    [
        ("promotion_eligible", True),
        ("live_enabled", True),
        ("auto_resume_production", True),
        ("financial_history_reset", True),
        ("native_broker_calls", 1),
        ("provider_calls", False),
        ("telegram_calls", 1),
        ("source", "mt5"),
        ("quote_mode", "ticks_modified"),
        ("dataset_sha256", "a" * 64),
        ("model_sha256", "b" * 64),
        ("code_hash", "c" * 64),
        ("manifest_sha256", "d" * 64),
    ],
)
def test_rehashed_report_flags_or_identity_not_accepted(completed_replay, tmp_path, field, value):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_json(root, "report.json", lambda body: body.update({field: value}))
    blocked(root)


@pytest.mark.parametrize(
    "metric,value",
    [
        ("net_profit_account", "5000"),
        ("closed_trades", 2),
        ("open_trades", 2),
        ("win_rate_percent", "100"),
        ("profit_factor", "25"),
        ("profit_factor_defined", False),
        ("max_drawdown_percent", "0"),
        ("commission_account", "0"),
        ("swap_account", "10"),
        ("equity_samples", 0),
        ("costs_included", False),
        ("unexplained_gaps", 99),
    ],
)
def test_derived_metrics_recomputed_not_trusted_rehashed_numbers(completed_replay, tmp_path, metric, value):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_json(root, "report.json", lambda body: body["metrics"].update({metric: value}))
    blocked(root, "bundle_recomputed_metrics_mismatch")


@pytest.mark.parametrize(
    "target",
    [
        "trade",
        "equity",
        "checkpoint_balance",
        "checkpoint_deal",
        "checkpoint_source",
        "empty_trades",
        "duplicate_trade",
        "veto_count",
        "future_equity",
        "reversed_equity",
    ],
)
def test_rehashed_economic_journals_not_just_local_digest_checks(completed_replay, tmp_path, target):
    root = clone_bundle(completed_replay, tmp_path / "run")
    if target == "trade":
        edit_lines(root, "trades.jsonl", lambda rows: rows[0].update(net_profit_account="99"))
    elif target == "equity":
        edit_lines(root, "equity.jsonl", lambda rows: rows[-1].update(equity="1005"))
    elif target == "checkpoint_balance":
        checkpoint_edit(root, lambda state: state.update(balance="50000"))
    elif target == "checkpoint_deal":
        checkpoint_edit(root, lambda state: state["deals"][-1].update(profit="500"))
    elif target == "checkpoint_source":
        checkpoint_edit(root, lambda state: state.update(market_source_kind="mt5"))
    elif target == "empty_trades":
        edit_lines(root, "trades.jsonl", lambda rows: rows.clear())
    elif target == "duplicate_trade":
        edit_lines(root, "trades.jsonl", lambda rows: rows.append(rows[0]))
    elif target == "veto_count":
        edit_json(root, "report.json", lambda body: body["veto_reasons"].update(end_close_Invented=1))
    elif target == "future_equity":
        edit_lines(root, "equity.jsonl", lambda rows: rows[-1].update(time="2030-01-01T00:00:00+00:00"))
    else:
        edit_lines(root, "equity.jsonl", lambda rows: rows.reverse())
    blocked(root)


@pytest.mark.parametrize(
    "sql,args",
    [
        ("UPDATE trades SET profit='99'", ()),
        ("UPDATE trades SET config_hash=?", ("a" * 64,)),
        ("UPDATE trades SET direction='sell'", ()),
        ("UPDATE trades SET ai_score=100", ()),
        ("UPDATE signals SET ai_score=100", ()),
        ("UPDATE signals SET score=100", ()),
        ("UPDATE signals SET final_decision='rejected'", ()),
        ("UPDATE broker_deals SET profit='99'", ()),
        ("UPDATE order_intents SET config_hash=?", ("b" * 64,)),
        ("DELETE FROM signals", ()),
        ("UPDATE bot_state SET desired_state='running'", ()),
        ("UPDATE bot_state SET session_id='TEST_BUSY'", ()),
        ("UPDATE bot_state SET settings_overrides=?", ('{"max_daily_trades":100}',)),
        ("UPDATE schema_version SET version=1", ()),
    ],
)
def test_rehashed_sql_private_rows_must_match_journals_and_stopped_scope(
    completed_replay, tmp_path, sql, args
):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_db(root, sql, args)
    blocked(root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposal_hash", "a" * 64),
        ("code_hash", "b" * 64),
        ("source", "mt5"),
        ("observed_at", "2030-01-01T00:00:00+00:00"),
        ("model_sha256", "c" * 64),
        ("config_hash", "d" * 64),
    ],
)
def test_signal_proposals_not_trusted_when_file_digest_recomputed(completed_replay, tmp_path, field, value):
    root = clone_bundle(completed_replay, tmp_path / "run")
    edit_lines(root, "signals.jsonl", lambda rows: rows[-1]["payload"].update({field: value}))
    blocked(root)


@pytest.mark.parametrize(
    "fault",
    [
        "review",
        "decision_digest",
        "wrong_context_id",
        "news",
        "future_frame",
        "entry_signal",
        "missing_finalization",
        "duplicate_id",
    ],
)
def test_approval_reviews_context_and_entries_have_cross_artifact_bindings(completed_replay, tmp_path, fault):
    root = clone_bundle(completed_replay, tmp_path / "run")
    traces = lines(root / "signals.jsonl")
    final = next(row for row in traces if row["state"] == "approved")
    payload = final["payload"]
    if fault == "entry_signal":
        edit_lines(root, "operations.jsonl", lambda rows: rows[0].update(signal_id=123456789))
    else:
        if fault == "review":
            payload["ai_review"]["provider"] = "openai"
        elif fault == "decision_digest":
            payload["decision_digest"] = "e" * 64
        elif fault == "wrong_context_id":
            payload["decision_context"]["signal_id"] = 999
        elif fault == "news":
            payload["news_hash"] = "f" * 64
        elif fault == "future_frame":
            payload["feature_snapshot"]["frames"][0]["closed_at"] = "2030-01-01T00:00:00+00:00"
        elif fault == "missing_finalization":
            traces.remove(final)
        else:
            final["signal_id"] = 999
        write_lines(root / "signals.jsonl", traces)
        reseal_for_attack(root)
    blocked(root)


def test_even_report_trade_and_sql_edit_cannot_invent_profit_without_deal_legs(completed_replay, tmp_path):
    from backtesting.audit.contracts import money, stamp
    from backtesting.metrics import EquityPoint, summarize

    root = clone_bundle(completed_replay, tmp_path / "run")
    trades = lines(root / "trades.jsonl")
    trades[0]["net_profit_account"] = "99"
    write_lines(root / "trades.jsonl", trades)
    edit_db(root, "UPDATE trades SET profit='99'")
    points = [
        EquityPoint(
            stamp(row["time"]),
            *[money(row[key]) for key in ("balance", "equity", "credit", "cash_flow_total")],
            quotes_stale=row["quotes_stale"],
        )
        for row in lines(root / "equity.jsonl")
    ]
    edit_json(
        root, "report.json", lambda body: body.update(metrics=summarize(trades, points, currency="USD"))
    )
    blocked(root, "bundle_trade_deal_economics_mismatch")


def test_memory_only_sql_connection_no_package_setting_provider_or_children(completed_replay, monkeypatch):
    import socket
    import sqlite3
    import subprocess

    from core.settings import Settings

    calls, real = [], sqlite3.connect

    def connect(database, *args, **kwargs):
        assert database == ":memory:"
        calls.append(database)
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(
        Settings, "__init__", lambda *args, **kwargs: pytest.fail("No Settings/credentials load")
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("No network"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No child process"))
    before = inventory(completed_replay)
    assert audit_bundle(completed_replay)["internal_consistency_verified"]
    assert calls == [":memory:"] and before == inventory(completed_replay)


def test_audit_handles_bundle_changed_after_inspection_as_blocked(completed_replay, tmp_path, monkeypatch):
    import backtesting.audit.semantics as module

    root = clone_bundle(completed_replay, tmp_path / "run")
    actual = module.inspect_semantics

    def mutate(capture):
        result = actual(capture)
        (root / "report.md").write_text("CHANGED_AFTER_CAPTURE")
        return result

    monkeypatch.setattr(module, "inspect_semantics", mutate)
    result = blocked(root, "bundle_changed_during_audit")
    assert result["observations"] == {}
