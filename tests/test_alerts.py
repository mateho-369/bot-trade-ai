"""Durable critical alerts and failure-isolated local/outbound reporting; offline only."""

import logging
import os
from logging.handlers import RotatingFileHandler

import httpx
import pytest
from sqlalchemy import select

from app.alerts import DEDUP_SECONDS, RATE_LIMIT_COUNT, Alert, AlertCenter, AlertLogHandler, audit_alert
from app.reporter import Reporter
from core.logging_setup import AI_LOG_NAME, ERROR_LOG_NAME, configure_logging
from core.models import AuditLog, BotState
from tests.risk_helpers import MOMENT, OWNER, config, make_engine
from tests.runtime_helpers import close_runtime, runtime
from trading.types import BrokerError, ManualClock

TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


def _reporter(settings, database, clock, *, sent=None, fail=False):
    sent = sent if sent is not None else []

    def handler(request):
        if fail:
            raise RuntimeError("TEST_ONLY remote unavailable")
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    return Reporter(
        settings,
        secrets=database.secrets,
        transport=httpx.MockTransport(handler),
        clock=clock,
        console=lambda _: None,
    )


def _rows(database):
    with database.session() as session:
        return [
            (row.level, row.component, row.message, row.repeat_count, row.report_status)
            for row in session.scalars(select(Alert).order_by(Alert.id))
        ]


class Ticker:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_info_is_local_only_and_warning_error_use_bounded_plain_report(tmp_path):
    engine = await make_engine(
        tmp_path,
        telegram_bot_token=TOKEN,
        telegram_report_chat_id="123",
    )
    try:
        sent = []
        reporter = _reporter(engine.settings, engine.database, engine.clock, sent=sent)
        center = AlertCenter(engine.database, engine.settings, engine.clock)
        center.emit("INFO", "AI Provider", "fallback mode changed")
        center.emit("WARNING", "Risk Engine", "risk check blocked an approved trade")
        center.emit("ERROR", "Execution", "Order rejected by broker", action="No position opened")
        result = await center.flush(reporter)
        assert result["sent"] == 2 and len(sent) == 2
        texts = [__import__("json").loads(request.content)["text"] for request in sent]
        assert all(request.method == "POST" and request.url.scheme == "https" for request in sent)
        assert "Level: WARNING" in texts[0]
        assert "Component: Execution" in texts[1] and "Message: Order rejected by broker" in texts[1]
        assert "Action: No position opened" in texts[1]
        assert [row[4] for row in _rows(engine.database)] == ["not_required", "sent", "sent"]
        assert all(len(text) <= 1500 for text in texts)
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_broker_disconnect_halts_entries_but_reports_only_sanitized_error(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        reporter = _reporter(resources.settings, resources.database, resources.broker.clock)
        original = resources.positions.cycle

        async def disconnected(*args, **kwargs):
            raise BrokerError("terminal disconnected PRIVATE_RAW_BODY")

        resources.positions.cycle = disconnected
        assert (await jobs.run_job("positions"))["state"] == "failed"
        await resources.alerts.flush(reporter)
        resources.positions.cycle = original
        await jobs.run_job("positions")
        await resources.alerts.flush(reporter)
        assert resources.database.status()["state"] == "paused"
        with resources.database.session() as session:
            assert session.get(BotState, 1).last_error == "broker_unstable"
        messages = (
            resources.settings.resolve_path(resources.settings.data_dir) / "reports" / "actions.log"
        ).read_text()
        assert "Connection lost" in messages and "MT5 reconnected" in messages
        assert "PRIVATE_RAW_BODY" not in messages and "terminal disconnected" not in messages
    finally:
        await close_runtime(resources, jobs)


async def test_outbound_failure_is_recorded_locally_and_never_resent(tmp_path):
    engine = await make_engine(tmp_path, telegram_bot_token=TOKEN, telegram_report_chat_id="123")
    try:
        reporter = _reporter(engine.settings, engine.database, engine.clock, fail=True)
        center = AlertCenter(engine.database, engine.settings, engine.clock)
        center.emit("ERROR", "Database", "Database write failure - trading halted")
        await center.flush(reporter)
        await center.flush(reporter)
        assert _rows(engine.database)[0][4] == "uncertain"
        assert (reporter.reports_dir / "actions.log").is_file()
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_same_alert_is_deduplicated_and_critical_bypasses_rate_limit(tmp_path):
    engine = await make_engine(
        tmp_path,
        telegram_bot_token=TOKEN,
        telegram_report_chat_id="123",
    )
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        sent = []
        reporter = _reporter(engine.settings, engine.database, engine.clock, sent=sent)
        ticker = Ticker()
        center = AlertCenter(
            engine.database,
            engine.settings,
            engine.clock,
            control=engine.control,
            account_key=lambda: engine.account_key,
            monotonic=ticker,
        )
        for attempt in range(5):
            center.emit("WARNING", "MT5 Client", f"retry {attempt} failed after {attempt * 10} ms")
        result = await center.flush(reporter)
        assert result["sent"] == 1 and result["deduplicated"] == 4
        [(level, component, _, repeats, _)] = _rows(engine.database)
        assert (level, component, repeats) == ("WARNING", "MT5 Client", 5)
        ticker.now += DEDUP_SECONDS + 1
        center.emit("WARNING", "MT5 Client", "retry 9 failed after 90 ms")
        await center.flush(reporter)
        assert len(sent) == 2 and "Repeated: x6" in __import__("json").loads(sent[-1].content)["text"]

        for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"):
            center.emit("WARNING", "Scheduler", f"job {name} failed")
        result = await center.flush(reporter)
        assert result["sent"] == RATE_LIMIT_COUNT - 1 and result["rate_limited"] == 3
        before_critical = len(sent)
        for _ in range(3):
            center.emit("CRITICAL", "Risk Engine", "Daily loss limit reached - trading auto-paused")
        result = await center.flush(reporter)
        assert result["sent"] == 3 and result["deduplicated"] == 0
        assert len(sent) - before_critical == 3
        with engine.database.session() as session:
            state = session.get(BotState, 1)
            actions = [row.action for row in session.scalars(select(AuditLog))]
        assert state.desired_state == "paused" and state.last_error is None and not state.kill_switch_active
        assert actions.count("runtime.auto_paused") == 1
        engine.control.resume(OWNER, account_key=engine.account_key)
    finally:
        await engine.shutdown()
        engine.database.close()


@pytest.mark.parametrize(
    ("action", "details", "level", "fragment"),
    [
        ("runtime.local_killed", {"operator_id": 1}, "CRITICAL", "Kill switch"),
        ("risk.daily_loss_latched", {}, "CRITICAL", "Daily loss limit"),
        ("risk.drawdown_latched", {}, "CRITICAL", "Max drawdown"),
        ("runtime.halted", {"reason": "persistence_failure"}, "ERROR", "Database write failure"),
        ("runtime.notice_pending", {"kind": "restart"}, "WARNING", "Watchdog restarted"),
        ("broker.acknowledged", {"status": "rejected", "operation": "open"}, "ERROR", "Order rejected"),
        (
            "broker.acknowledged",
            {"status": "rejected", "operation": "protect"},
            "ERROR",
            "SL/TP modification",
        ),
        ("risk.entry_decision", {"approved": False, "reasons": ["margin_cap"]}, "WARNING", "margin_cap"),
        (
            "local_operator.ai_fallback_mode_changed",
            {"to": "TECHNICAL_ONLY"},
            "INFO",
            "TECHNICAL_ONLY",
        ),
    ],
)
def test_audit_events_map_to_safety_or_local_operator_alerts(action, details, level, fragment):
    mapped = audit_alert(action, details)
    assert mapped is not None and mapped[0] == level and fragment in mapped[2]


@pytest.mark.parametrize(
    ("action", "details"),
    [
        ("broker.acknowledged", {"status": "filled", "operation": "open"}),
        ("risk.entry_decision", {"approved": False, "reasons": ["paused"]}),
        ("risk.entry_decision", {"approved": True, "reasons": []}),
        ("runtime.halted", {"reason": "broker_unstable"}),
        ("runtime.local_resumed", {}),
    ],
)
def test_routine_audit_events_do_not_alert(action, details):
    assert audit_alert(action, details) is None


async def test_audit_scanner_never_replays_history(tmp_path):
    settings = config(tmp_path)
    from core.database import Database

    database = Database(settings)
    database.initialize()
    try:
        database.audit("risk.daily_loss_latched", "risk", {"account": "x"})
        center = AlertCenter(database, settings, ManualClock(MOMENT))
        assert center.scan_audit() == 0
        database.audit("runtime.local_killed", "local_operator", {"operator_id": 1})
        assert center.scan_audit() == 1 and center.pending() == 1
    finally:
        database.close()


async def test_log_handler_captures_own_warnings_only(tmp_path):
    settings = config(tmp_path)
    from core.database import Database

    database = Database(settings)
    database.initialize()
    try:
        center = AlertCenter(database, settings, ManualClock(MOMENT))
        handler = AlertLogHandler(center)
        logger = logging.getLogger("reflexbot.mt5")
        logger.addHandler(handler)
        try:
            logger.warning("MT5 order_send returned no result")
            logger.error("already alerted", extra={"alerted": True})
            logging.getLogger("trading.ai_adaptive_trailing").warning(
                "AI_TRAILING_FALLBACK", extra={"no_alert": True}
            )
            logging.getLogger("httpx").addHandler(handler)
            logging.getLogger("httpx").error("third-party noise")
        finally:
            logger.removeHandler(handler)
            logging.getLogger("httpx").removeHandler(handler)
        assert center.pending() == 1
        await center.flush(None)
        assert _rows(database)[0][:3] == ("WARNING", "MT5 Client", "MT5 order_send returned no result")
    finally:
        database.close()


def test_bot_errors_and_ai_decision_logs_rotate_at_5mb_with_6_files(tmp_path):
    settings = config(tmp_path)
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        configure_logging(settings)
        handlers = [handler for handler in root.handlers if isinstance(handler, RotatingFileHandler)]
        names = sorted(os.path.basename(handler.baseFilename) for handler in handlers)
        assert names == sorted(["bot.log", ERROR_LOG_NAME, AI_LOG_NAME])
        assert all(handler.maxBytes == 5 * 1024 * 1024 and handler.backupCount == 5 for handler in handlers)
        logging.getLogger("ai.brain").info("AI decision kind=entry symbol=EURUSD action=wait")
        logging.getLogger("trading.execution").warning("Order rejected")
        for handler in handlers:
            handler.flush()
        log_dir = settings.resolve_path(settings.log_file).parent
        assert "AI decision" in (log_dir / AI_LOG_NAME).read_text()
        assert "Order rejected" not in (log_dir / AI_LOG_NAME).read_text()
        assert "Order rejected" in (log_dir / ERROR_LOG_NAME).read_text()
        assert "AI decision" not in (log_dir / ERROR_LOG_NAME).read_text()
    finally:
        for handler in root.handlers[:]:
            if handler not in saved:
                root.removeHandler(handler)
                handler.close()
        for handler in saved:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)
