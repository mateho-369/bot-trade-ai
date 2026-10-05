"""Alert Center: levels, delivery, dedup, rate limit, CRITICAL bypass/auto-pause, API and Telegram.

Offline only: an in-process fake Telegram bot, mock broker, no network, no orders.
"""

import logging
from datetime import timedelta
from logging.handlers import RotatingFileHandler

import pytest
from sqlalchemy import select

from app.alerts import (
    DEDUP_SECONDS,
    RATE_LIMIT_COUNT,
    Alert,
    AlertCenter,
    AlertLogHandler,
    acknowledge,
    audit_alert,
    list_alerts,
    resolve_older_than,
)
from core.logging_setup import AI_LOG_NAME, ERROR_LOG_NAME, configure_logging
from core.models import AuditLog, BotState
from tests.owner_helpers import api_client, auth_header, fake_transport, message_update, owner_services
from tests.risk_helpers import OWNER, config, make_engine
from tests.runtime_helpers import close_runtime, runtime
from trading.types import BrokerError


class FakeBot:
    def __init__(self, *, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.fail:
            raise RuntimeError("telegram down")
        assert parse_mode is None  # Plain text only: no HTML/Markdown injection from messages.
        self.sent.append((chat_id, text))


class Ticker:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def center_for(services_or_engine, **kwargs):
    target = services_or_engine
    clock = getattr(target, "clock", None)
    return AlertCenter(target.database, target.settings, clock, **kwargs)


def rows(database):
    with database.session() as session:
        return [
            (r.level, r.component, r.message, r.repeat_count, r.telegram_status)
            for r in session.scalars(select(Alert).order_by(Alert.id))
        ]


# -- levels and delivery -----------------------------------------------------------------------------
async def test_info_is_stored_only_and_warning_error_go_to_telegram(tmp_path):
    services = owner_services(tmp_path)
    center, bot = center_for(services), FakeBot()
    center.emit("INFO", "AI Provider", "fallback mode changed")
    center.emit("WARNING", "Risk Engine", "risk check blocked an approved trade")
    center.emit("ERROR", "Execution", "Order rejected by broker", action="No position opened")
    result = await center.flush(bot)
    assert result["sent"] == 2 and len(bot.sent) == 2
    assert all(chat == services.settings.telegram_owner_id for chat, _ in bot.sent)
    error = bot.sent[1][1]
    for part in (
        "🚨 ERROR ALERT",
        "Level: ERROR",
        "Component: Execution",
        "Message: Order rejected by broker",
    ):
        assert part in error
    assert "Action: No position opened" in error and "Time: " in error
    assert [r[4] for r in rows(services.database)] == ["not_required", "sent", "sent"]


async def test_mt5_disconnect_and_reconnect_raise_immediate_alerts(tmp_path):
    resources, health, jobs = await runtime(tmp_path)
    try:
        assert isinstance(resources.alerts, AlertCenter)
        original = resources.positions.cycle

        async def disconnected(*args, **kwargs):
            raise BrokerError("terminal disconnected")

        resources.positions.cycle = disconnected
        assert (await jobs.run_job("positions"))["state"] == "failed"
        bot = FakeBot()
        await resources.alerts.flush(bot)
        [(_, text)] = bot.sent
        assert "ERROR" in text and "MT5 Client" in text and "Connection lost" in text
        assert "terminal disconnected" not in text  # Raw broker error text is never forwarded.
        resources.positions.cycle = original
        await jobs.run_job("positions")
        await resources.alerts.flush(bot)
        assert "MT5 reconnected" in bot.sent[-1][1] and "WARNING" in bot.sent[-1][1]
        with resources.database.session() as session:
            assert session.get(BotState, 1).last_error == "broker_unstable"  # Entries stay halted.
    finally:
        await close_runtime(resources, jobs)


async def test_telegram_failure_is_recorded_and_never_resent(tmp_path):
    services = owner_services(tmp_path)
    center = center_for(services)
    center.emit("ERROR", "Database", "Database write failure - trading halted")
    await center.flush(FakeBot(fail=True))
    await center.flush(FakeBot())  # Nothing pending: at-most-once.
    assert rows(services.database)[0][4] == "uncertain"


# -- dedup / rate limit / CRITICAL bypass ----------------------------------------------------------------
async def test_same_alert_within_five_minutes_is_grouped(tmp_path):
    services = owner_services(tmp_path)
    ticker = Ticker()
    center, bot = center_for(services, monotonic=ticker), FakeBot()
    for attempt in range(5):
        center.emit("WARNING", "MT5 Client", f"retry {attempt} failed after {attempt * 10} ms")
    result = await center.flush(bot)
    assert result["sent"] == 1 and result["deduplicated"] == 4
    [(level, component, _, repeats, _)] = rows(services.database)
    assert (level, component, repeats) == ("WARNING", "MT5 Client", 5)
    ticker.now += DEDUP_SECONDS + 1
    center.emit("WARNING", "MT5 Client", "retry 9 failed after 90 ms")
    await center.flush(bot)
    assert len(bot.sent) == 2 and "Repeated: x6" in bot.sent[1][1]


async def test_rate_limit_caps_telegram_but_critical_bypasses_dedup_and_limit(tmp_path):
    engine = await make_engine(tmp_path)
    try:
        engine.control.resume(OWNER, account_key=engine.account_key)
        center = AlertCenter(
            engine.database,
            engine.settings,
            engine.clock,
            control=engine.control,
            account_key=lambda: engine.account_key,
        )
        bot = FakeBot()
        for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"):
            center.emit("WARNING", "Scheduler", f"job {name} failed")
        result = await center.flush(bot)
        assert result["sent"] == RATE_LIMIT_COUNT and result["rate_limited"] == 2
        for _ in range(3):
            center.emit("CRITICAL", "Risk Engine", "Daily loss limit reached - trading auto-paused")
        result = await center.flush(bot)
        assert result["sent"] == 3 and result["deduplicated"] == 0  # Never grouped, never limited.
        assert all("🛑 CRITICAL ALERT" in text for _, text in bot.sent[-3:])
        assert "rate-limited" in bot.sent[-3][1]  # Owner told that others were suppressed.
        with engine.database.session() as session:
            state = session.get(BotState, 1)
            assert state.desired_state == "paused"  # Auto-pause of NEW entries.
            assert state.last_error is None and not state.kill_switch_active  # Not a halt or kill.
            actions = [r.action for r in session.scalars(select(AuditLog))]
        assert actions.count("runtime.auto_paused") == 1
        # Normal gated owner resume still works after a CRITICAL auto-pause.
        engine.control.resume(OWNER, account_key=engine.account_key)
    finally:
        await engine.shutdown()
        engine.database.close()


# -- sources: audit events and log records -------------------------------------------------------------
@pytest.mark.parametrize(
    ("action", "details", "level", "fragment"),
    [
        ("owner.killed", {"owner_id": 1}, "CRITICAL", "Kill switch"),
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
    ],
)
def test_audit_events_map_to_the_specified_alerts(action, details, level, fragment):
    mapped = audit_alert(action, details)
    assert mapped is not None and mapped[0] == level and fragment in mapped[2]


@pytest.mark.parametrize(
    ("action", "details"),
    [
        ("broker.acknowledged", {"status": "filled", "operation": "open"}),
        ("risk.entry_decision", {"approved": False, "reasons": ["paused"]}),  # Known state, not news.
        ("risk.entry_decision", {"approved": True, "reasons": []}),
        ("runtime.halted", {"reason": "broker_unstable"}),  # Scheduler emits the richer alert.
        ("owner.resumed_entries", {}),
    ],
)
def test_routine_audit_events_do_not_alert(action, details):
    assert audit_alert(action, details) is None


async def test_audit_scanner_never_replays_history(tmp_path):
    services = owner_services(tmp_path)
    services.database.audit("risk.daily_loss_latched", "risk", {"account": "x"})
    center = center_for(services)
    assert center.scan_audit() == 0  # Cursor starts at the current maximum id.
    services.database.audit("owner.killed", "owner", {"owner_id": OWNER})
    assert center.scan_audit() == 1 and center.pending() == 1


async def test_log_handler_captures_own_warnings_only(tmp_path):
    services = owner_services(tmp_path)
    center = center_for(services)
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
    assert rows(services.database)[0][:3] == ("WARNING", "MT5 Client", "MT5 order_send returned no result")


# -- owner API + Telegram ----------------------------------------------------------------------------------
async def seeded(tmp_path):
    services = owner_services(tmp_path)
    center = center_for(services)
    center.emit("INFO", "AI Provider", "fallback mode set")
    center.emit("ERROR", "MT5 Client", "Connection lost: broker unavailable")
    center.emit("CRITICAL", "Risk Engine", "Kill switch activated")
    await center.flush(None)  # No transport: stored only (CRITICAL auto-pause needs a control).
    return services


async def test_api_alerts_lists_filters_and_acknowledges(tmp_path):
    services = await seeded(tmp_path)
    async with api_client(services) as (client, _):
        denied = await client.get("/api/alerts")
        listed = await client.get("/api/alerts", headers=auth_header(services))
        critical = await client.get("/api/alerts?level=CRITICAL", headers=auth_header(services))
        invalid = await client.get("/api/alerts?level=DEBUG", headers=auth_header(services))
        first = listed.json()["items"][-1]["id"]
        one = await client.post(
            "/api/alerts/ack",
            json={"request_id": "3" * 8 + "-3333-4333-8333-" + "3" * 12, "alert_id": first},
            headers=auth_header(services),
        )
        everything = await client.post(
            "/api/alerts/ack",
            json={"request_id": "4" * 8 + "-4444-4444-8444-" + "4" * 12},
            headers=auth_header(services),
        )
        after = await client.get("/api/alerts", headers=auth_header(services))
    assert denied.status_code == 401 and invalid.status_code in {400, 422}
    body = listed.json()
    assert [i["level"] for i in body["items"]] == ["CRITICAL", "ERROR", "INFO"]  # Newest first.
    assert body["unacknowledged"] == 3 and body["counts"]["CRITICAL"] == 1
    assert [i["level"] for i in critical.json()["items"]] == ["CRITICAL"]
    assert one.json()["acknowledged"] == 1 and everything.json()["acknowledged"] == 2
    assert after.json()["unacknowledged"] == 0 and all(i["acknowledged"] for i in after.json()["items"])
    assert "TEST_ONLY_NEVER_CONTACT_TELEGRAM" not in listed.text
    with services.database.session() as session:
        actions = [r.action for r in session.scalars(select(AuditLog))]
    assert actions.count("owner.alerts_acknowledged") == 2


async def test_telegram_alert_commands(tmp_path):
    services = await seeded(tmp_path)
    transport, session = fake_transport(services)
    try:
        for text, message_id in (("/alerts", 1), ("/alerts critical", 2), ("/ack_all", 3), ("/alerts", 4)):
            await transport.dispatcher.feed_update(
                transport.bot, message_update(services, text, message_id=message_id)
            )
        replies = [m.text for name, m in session.calls if name == "SendMessage"]
        assert "unacknowledged: 3" in replies[0] and "Connection lost" in replies[0]
        assert "Kill switch activated" in replies[1] and "Connection lost" not in replies[1]
        assert "3 alert(s) acknowledged" in replies[2]
        assert "unacknowledged: 0" in replies[3]
        calls = len(session.calls)
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/ack_all", sender=43, message_id=5)
        )
        assert len(session.calls) == calls  # Owner only.
    finally:
        await transport.close()


def test_list_and_acknowledge_without_any_alert_rows(tmp_path):
    services = owner_services(tmp_path)
    assert list_alerts(services.database)["items"] == []
    assert acknowledge(services.database, services.clock, owner_id=OWNER) == 0


async def test_housekeeping_resolves_only_old_acknowledged_alerts(tmp_path):
    services = owner_services(tmp_path)
    center = center_for(services)
    center.emit("WARNING", "Risk Engine", "first")
    center.emit("ERROR", "Execution", "second")
    await center.flush(None)
    first = list_alerts(services.database)["items"][-1]["id"]
    assert acknowledge(services.database, services.clock, owner_id=OWNER, alert_id=first) == 1

    class Later:
        def now(self):
            return services.clock.now() + timedelta(hours=25)

    assert resolve_older_than(services.database, services.clock, hours=24) == 0  # Too recent.
    assert resolve_older_than(services.database, Later(), hours=24) == 1  # Unacknowledged stays open.
    with services.database.session() as session:
        assert [(r.message, r.resolved) for r in session.scalars(select(Alert).order_by(Alert.id))] == [
            ("first", True),
            ("second", False),
        ]


# -- log files -----------------------------------------------------------------------------------------------
def test_bot_errors_and_ai_decision_logs_rotate_at_5mb_with_6_files(tmp_path):
    settings = config(tmp_path)
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        configure_logging(settings)
        handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        names = sorted(h.baseFilename.rsplit("/", 1)[-1] for h in handlers)
        assert names == sorted(["bot.log", ERROR_LOG_NAME, AI_LOG_NAME])
        assert all(h.maxBytes == 5 * 1024 * 1024 and h.backupCount == 5 for h in handlers)  # 1 + 5 files.
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
