"""AI-first local reporting, overlay reset, and runtime wiring (offline/mock only)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from ai.ai_brain import BrainResult
from ai.ai_first import AIFirstLayer
from ai.ai_first_schemas import decode
from ai.config_adjuster import revert_all
from ai.decision_journal import AIConfigOverlay
from app.reporter import Reporter, catalog_parity, reporter_catalogs
from core.models import AuditLog
from tests.risk_helpers import OWNER, config
from tests.runtime_helpers import close_runtime, runtime
from trading.ai_adaptive_trailing import AdaptiveTrailing, TrailingEvent
from trading.ai_controls import read_dynamic


async def test_runtime_composes_ai_first_layer_and_lock_first_trailing(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        assert isinstance(resources.ai_first, AIFirstLayer)
        assert isinstance(resources.positions.adaptive, AdaptiveTrailing)
        assert resources.ai_first.brain.mode == "rule"
        for name in ("ai_learning", "ai_config_review", "ai_nightly_review"):
            assert name in jobs.jobs
        learning = await jobs.run_job("ai_learning")
        assert learning["state"] == "reviewed" and learning["trades"] == []
        review = await jobs.run_job("ai_config_review")
        assert review["state"] in {"ai_unavailable", "market_unavailable"}
        notifications = await jobs.run_job("notifications")
        assert notifications["runtime"]["sent"] == 0 and notifications["news"]["reported"] == 0
        assert notifications["news"]["uncertain"] == notifications["news"]["disabled"] == 0
        assert (await jobs.run_job("signals"))["state"] == "paused"
    finally:
        await close_runtime(resources, jobs)


async def test_ai_first_can_be_disabled_without_changing_the_runtime(tmp_path):
    resources, health, jobs = await runtime(tmp_path, ai_first_enabled=False)
    try:
        assert resources.ai_first is None and resources.positions.adaptive is None
        assert (await jobs.run_job("ai_learning")) == {"state": "disabled"}
    finally:
        await close_runtime(resources, jobs)


async def test_adaptive_trailing_switch_uses_mechanical_trailing(tmp_path):
    resources, health, jobs = await runtime(tmp_path, ai_adaptive_trailing_enabled=False)
    try:
        assert resources.ai_first is not None and resources.positions.adaptive is None
    finally:
        await close_runtime(resources, jobs)


async def test_trades_today_counts_the_trading_day(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        assert resources.ai_first.trades_today() == 0
    finally:
        await close_runtime(resources, jobs)


def _executable_result():
    decision = decode(
        "decision",
        '{"action":"open_buy","confidence":88,"reason":"trend","suggested_risk_percent":0.3,'
        '"suggested_target_profit":5,"suggested_sl_distance":0.002,"news_risk":"low","market_condition":"trending"}',
    )
    return BrainResult("entry", decision, "ai", "qwen/qwen3.8-27b", True)


def test_reporter_mirrors_sanitized_ai_reports_locally_and_is_outbound_disabled_by_default(tmp_path):
    output = []
    settings = config(tmp_path)
    reporter = Reporter(settings, secrets=("SECRET-VALUE",), console=output.append)
    reporter.decision(_executable_result(), "EURUSD")
    reporter.provider_event("circuit_open", "groq", "ReadTimeout SECRET-VALUE")
    assert not reporter.enabled
    assert len(reporter.outbox) == 2
    assert all("SECRET-VALUE" not in row["message"] for row in reporter.outbox)
    assert len(output) == 2
    reports = settings.resolve_path(settings.data_dir) / "reports"
    assert (reports / "actions.log").is_file()
    daily = list(reports.glob("????-??-??.jsonl"))
    assert len(daily) == 1 and '"kind"' in daily[0].read_text(encoding="utf-8")


async def test_ai_overlay_reset_is_local_operator_audited_and_does_not_touch_risk(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        adjuster = resources.ai_first.adjuster
        adjuster.propose("risk_percent_per_trade", 0.4, reason="calm")
        adjuster.propose("max_daily_trades", 8, reason="ranging")
        assert set(read_dynamic(resources.database)) == {"risk_percent_per_trade", "max_daily_trades"}
        with resources.database.session() as session:
            risk_before = session.scalar(select(AuditLog.id).where(AuditLog.action.like("risk.%")))
        assert revert_all(resources.database, resources.broker.clock, operator=OWNER) == 2
        with resources.database.session() as session:
            statuses = set(session.scalars(select(AIConfigOverlay.status)).all())
            actions = set(session.scalars(select(AuditLog.action)).all())
            risk_after = session.scalar(select(AuditLog.id).where(AuditLog.action.like("risk.%")))
        assert statuses == {"reverted"}
        assert "local_operator.ai_config_reset" in actions
        assert read_dynamic(resources.database) == {}
        assert risk_after == risk_before
        assert adjuster.effective()["risk_percent_per_trade"] == resources.settings.effective_risk_percent
    finally:
        await close_runtime(resources, jobs)


def test_reporter_catalogs_have_en_kh_parity_and_trailing_reports_are_bounded(tmp_path):
    assert catalog_parity()
    assert set(reporter_catalogs()) == {"en", "km"}
    review = Path(__file__).resolve().parents[1] / "KHMER_REVIEW.md"
    text = review.read_text(encoding="utf-8")
    assert "Native-speaker review is required" in text
    for key, value in reporter_catalogs()["km"].items():
        value = value.replace("|", "\\|").replace("\n", "<br>")
        assert f"| `{key}` | {value} |" in text
    event = TrailingEvent(
        7, 60, "1.1025", "close_now", 81.0, "momentum fading", "closed_with_locked_profit", "ai"
    )
    reporter = Reporter(config(tmp_path), console=lambda _: None)
    reporter.trailing(event)
    message = reporter.outbox[0]["message"]
    assert "position 7" in message and len(message) <= 1500


@pytest.mark.parametrize("provider", ["scripted", "rule"])
async def test_ai_first_smoke_script_passes_offline(provider):
    from scripts.smoke_ai_first import run

    report = await run(provider)
    assert report["ok"] and report["broker"] == "mock" and report["real_orders"] == 0
