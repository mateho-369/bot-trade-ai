"""Offline demo trade cycle, AI attribution, local reports, and audit flags.

All broker effects are synthetic and scripted; no provider request, HTTP server, or live order runs.
"""

import json
from datetime import timedelta

from sqlalchemy import select

from ai.ai_brain import AIBrain
from ai.ai_first_reviewer import AIFirstReviewer
from ai.decision_journal import AIDecisionJournal, DecisionJournal
from ai.market_awareness import MarketAwarenessEngine
from ai.ollama_client import ProviderContent
from ai.trade_attribution import TradeAttribution, ai_stats, history, sync
from ai.trade_audit import audit_trades, format_report
from app.reporter import Reporter
from core.models import Trade
from tests.risk_helpers import OWNER
from tests.signal_helpers import make_signal_runtime, news


def decision_json(**changes):
    values = dict(
        action="open_buy",
        confidence=85,
        reason="trend aligned on M5/M15/H1",
        suggested_risk_percent=0.3,
        suggested_target_profit=5,
        suggested_sl_distance=0.0025,
        news_risk="low",
        market_condition="trending",
    )
    values.update(changes)
    return json.dumps(values)


class LabelledAI:
    """Scripted OpenAI-compatible AI that answers as registry label ``groq``."""

    configured = True

    def __init__(self, reply=None, *, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    async def complete(self, messages, schema):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ProviderContent("groq", "qwen/qwen3.8-27b", self.reply, simulated=False)


async def runtime(tmp_path, **changes):
    values = dict(ai_queue_min_interval_ms=0, ai_max_retries=0, ai_decision_cache_seconds=0)
    values.update(changes)
    return await make_signal_runtime(tmp_path, **values)


def reporter_for(execution):
    return Reporter(execution.settings, secrets=execution.database.secrets, clock=execution.clock)


def reports(reporter, kind):
    return [row["message"] for row in reporter.outbox if row["kind"] == kind]


async def approve_and_fill(signals, execution, journal, reporter):
    proposal = await signals.analyze("EURUSD")
    assert proposal.state == "pending" and proposal.side is not None
    side = proposal.side.value.lower()
    brain = AIBrain(
        execution.settings,
        signals.clock,
        provider=LabelledAI(decision_json(action="open_" + side)),
        journal=journal,
        notifier=reporter,
    )
    awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
    reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
    window = news(signals.clock)
    review = await reviewer.review(proposal, window)
    assert review.decision == "approve" and review.provider == "groq"
    final = await signals.finalize(proposal.signal_id, review=review, news=window)
    assert final.state == "approved", final.reasons
    execution.control.resume(OWNER, account_key=execution.account_key)
    outcome = await execution.execute_signal(proposal.signal_id)
    assert outcome.status.value == "filled"
    journal.link_signal(
        proposal.signal_id,
        executed=True,
        position_id=outcome.position_identifier,
        final_action="execution_filled",
    )
    return proposal, side


async def test_offline_demo_cycle_ai_approves_trades_closes_and_audits_clean(tmp_path):
    signals, execution = await runtime(tmp_path)
    try:
        clock, database = signals.clock, execution.database
        journal, reporter = DecisionJournal(database, clock), reporter_for(execution)
        proposal, side = await approve_and_fill(signals, execution, journal, reporter)

        assert sync(database, clock, notifier=reporter, account_key=execution.account_key)["notified"] == 1
        opened = reports(reporter, "ai.trade_opened")
        assert len(opened) == 1 and "EURUSD" in opened[0]
        with database.session() as session:
            attr = session.scalar(select(TradeAttribution))
            assert (attr.decided_by, attr.ai_model, attr.ai_confidence) == ("groq", "qwen/qwen3.8-27b", 85.0)
            assert attr.approval_journal_id is not None and attr.trailing_by == "MECHANICAL"
            assert attr.demo_fast_track is False
        assert sync(database, clock, notifier=reporter, account_key=execution.account_key)["notified"] == 0

        position = (await execution.broker.get_positions())[0]
        await execution.close_owned(position.ticket, position.identifier)
        await execution.reconcile()
        assert sync(database, clock, notifier=reporter, account_key=execution.account_key)["notified"] == 1
        closed = reports(reporter, "ai.trade_closed")
        assert len(closed) == 1 and "Profit/loss:" in closed[0] and "EURUSD" in closed[0]
        assert history(database)[0]["decided_by"] == "groq"

        stats = {row["label"]: row for row in ai_stats(database)["labels"]}
        groq = stats["groq"]
        assert (groq["approvals"], groq["trades"], groq["closed"]) == (1, 1, 1)
        assert groq["avg_confidence"] == 85.0 and groq["failures"] == 0
        with database.session() as session:
            trade = session.scalar(select(Trade))
            assert str(groq["net_profit"]) == str(trade.profit)

        report = audit_trades(database, execution.settings, clock)
        assert report["clean"] and report["checked"] == 1
        assert format_report(report).startswith("Trade audit: CLEAN")
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_ai_down_means_no_trade_and_one_locally_reported_block(tmp_path):
    signals, execution = await runtime(tmp_path)
    try:
        reporter = reporter_for(execution)
        journal = DecisionJournal(execution.database, signals.clock)
        down = LabelledAI(error=ConnectionError("provider offline"))
        brain = AIBrain(execution.settings, signals.clock, provider=down, journal=journal, notifier=reporter)
        awareness = MarketAwarenessEngine(execution.broker, execution.settings, signals.clock)
        reviewer = AIFirstReviewer(brain, awareness, execution.settings, execution.profile, signals.clock)
        execution.control.resume(OWNER, account_key=execution.account_key)
        for _ in range(3):
            proposal = await signals.analyze("EURUSD")
            window = news(signals.clock)
            review = await reviewer.review(proposal, window)
            assert review is None or review.decision != "approve"
            final = await signals.finalize(proposal.signal_id, review=review, news=window)
            assert final.state != "approved"
            signals.clock.advance(timedelta(minutes=5))
        assert await execution.broker.get_positions() == ()
        blocked = reports(reporter, "ai.blocked")
        assert len(blocked) == 1
        rows = journal.recent(kind="entry")
        assert rows and all("no_ai_approval" in (row["rejection_reason"] or "") for row in rows)
        assert audit_trades(execution.database, execution.settings, signals.clock)["checked"] == 0
    finally:
        await execution.shutdown()
        execution.database.close()


async def audited(tmp_path, mutate):
    signals, execution = await runtime(tmp_path)
    try:
        journal = DecisionJournal(execution.database, signals.clock)
        await approve_and_fill(signals, execution, journal, reporter_for(execution))
        with execution.database.session() as session:
            row = session.scalar(select(AIDecisionJournal).where(AIDecisionJournal.kind == "entry"))
            trade = session.scalar(select(Trade))
            mutate(session, row, trade, journal)
        return audit_trades(execution.database, execution.settings, signals.clock)
    finally:
        await execution.shutdown()
        execution.database.close()


async def test_audit_flags_a_trade_without_an_ai_approval(tmp_path):
    def unlink(session, row, trade, journal):
        row.position_id = None

    report = await audited(tmp_path, unlink)
    assert report["counts"]["NO_AI_APPROVAL"] == 1 and not report["clean"]


async def test_audit_flags_rule_fallback_trades(tmp_path):
    def rule(session, row, trade, journal):
        row.source, row.provider_label = "rule_fallback", "RULE_FALLBACK"

    assert (await audited(tmp_path, rule))["counts"]["RULE_FALLBACK_TRADE"] == 1


async def test_audit_flags_an_unknown_decider(tmp_path):
    def unlabelled(session, row, trade, journal):
        row.provider_label = None

    assert (await audited(tmp_path, unlabelled))["counts"]["UNKNOWN_DECIDER"] == 1


async def test_audit_flags_a_journal_mismatch(tmp_path):
    def mismatch(session, row, trade, journal):
        row.action = "open_sell" if trade.direction == "buy" else "open_buy"
        row.confidence = 10.0

    report = await audited(tmp_path, mismatch)
    assert report["counts"]["JOURNAL_MISMATCH"] == 1
    assert "confidence below threshold" in report["flags"][0]["detail"]


async def test_audit_flags_a_trade_after_an_ai_wait(tmp_path):
    def wait_after(session, row, trade, journal):
        session.add(
            AIDecisionJournal(
                time=trade.open_time - timedelta(milliseconds=1) if row.time < trade.open_time else row.time,
                kind="entry",
                symbol="EURUSD",
                source="ai",
                provider_label="groq",
                model="qwen/qwen3.8-27b",
                input_hash="0" * 64,
                input_summary={},
                action="wait",
                confidence=90.0,
                reason="later wait",
                adjustments={},
                executed=False,
                final_action="vetoed",
            )
        )

    assert (await audited(tmp_path, wait_after))["counts"]["TRADE_AFTER_AI_WAIT"] == 1


async def test_audit_cli_is_read_only_and_exits_by_result(tmp_path):
    from scripts import audit_trades as cli

    empty = tmp_path / "fresh"
    empty.mkdir()
    (empty / ".env").write_text("", encoding="utf-8")
    assert cli.main(["--env-file", str(empty / ".env")]) == 2
    assert not list(empty.rglob("*.db"))
    signals, execution = await runtime(tmp_path / "rt")
    try:
        journal = DecisionJournal(execution.database, signals.clock)
        await approve_and_fill(signals, execution, journal, reporter_for(execution))
        report = cli.run(execution.settings, sync=True, clock=signals.clock)
        assert report["clean"] and report["checked"] == 1
    finally:
        await execution.shutdown()
        execution.database.close()
