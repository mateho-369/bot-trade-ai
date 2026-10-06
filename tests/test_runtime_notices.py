"""Local-first runtime notices and bounded HTTPS sendMessage-only reports."""

import asyncio
import json

import httpx
import pytest

from app.notifications import RuntimeNotices
from app.reporter import MAX_TEXT, Reporter
from core.database import Database
from tests.risk_helpers import MOMENT, config
from trading.types import ManualClock

TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


@pytest.fixture
def system(tmp_path):
    settings = config(tmp_path, telegram_bot_token=TOKEN, telegram_report_chat_id="123")
    database = Database(settings)
    database.initialize()
    clock = ManualClock(MOMENT)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    reporter = Reporter(
        settings,
        secrets=(*database.secrets, "PRIVATE_TOKEN_DO_NOT_LOG"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        console=lambda _: None,
    )
    yield RuntimeNotices(database, settings, clock, reporter), sent
    database.close()


@pytest.mark.parametrize(
    "kind",
    [
        "started",
        "restart",
        "stalled",
        "budget",
        "job_failed",
        "stopped",
        "daily_report",
        "learning_candidate",
        "auto_resumed",
    ],
)
async def test_fixed_runtime_notices_are_local_first_and_use_sendmessage_only(system, kind):
    notices, sent = system
    notices.enqueue(kind, dedup="TEST_ONLY")
    assert notices.enqueue(kind, dedup="TEST_ONLY") is None
    result = await notices.drain(limit=10)
    assert result["sent"] == 1 and result["queued"] == 0
    assert len(sent) == 1
    request = sent[0]
    assert request.method == "POST"
    assert request.url.scheme == "https" and request.url.host == "api.telegram.org"
    assert request.url.path == f"/bot{TOKEN}/sendMessage" and not request.url.query
    payload = json.loads(request.content)
    assert payload["chat_id"] == 123 and payload["parse_mode"] is None
    assert len(payload["text"]) <= MAX_TEXT
    assert set(payload) == {"chat_id", "text", "parse_mode", "disable_web_page_preview"}
    reports = notices.reporter.reports_dir
    assert (reports / "actions.log").is_file()
    assert len(list(reports.glob("????-??-??.jsonl"))) == 1


async def test_remote_failure_is_isolated_local_record_survives_and_is_not_retried(tmp_path):
    settings = config(tmp_path, telegram_bot_token=TOKEN, telegram_report_chat_id="123")
    database = Database(settings)
    database.initialize()
    count = []

    def fail(request):
        count.append(request)
        raise RuntimeError("PRIVATE_TOKEN_DO_NOT_LOG")

    reporter = Reporter(
        settings,
        secrets=("PRIVATE_TOKEN_DO_NOT_LOG",),
        transport=httpx.MockTransport(fail),
        console=lambda _: None,
    )
    notices = RuntimeNotices(database, settings, ManualClock(MOMENT), reporter)
    try:
        notices.enqueue("stalled", dedup="TEST_ONLY", details={"raw": "PRIVATE_TOKEN_DO_NOT_LOG"})
        first = await notices.drain()
        second = await notices.drain()
        assert first["uncertain"] == 1 and second["uncertain"] == 0
        assert len(count) == 1
        local = (reporter.reports_dir / "actions.log").read_text(encoding="utf-8")
        assert "PRIVATE_TOKEN_DO_NOT_LOG" not in local
    finally:
        database.close()


async def test_cancellation_claims_report_before_network_and_never_resubmits(tmp_path):
    settings = config(tmp_path, telegram_bot_token=TOKEN, telegram_report_chat_id="123")
    database = Database(settings)
    database.initialize()

    def cancel(request):
        raise asyncio.CancelledError()

    reporter = Reporter(
        settings,
        transport=httpx.MockTransport(cancel),
        console=lambda _: None,
    )
    notices = RuntimeNotices(database, settings, ManualClock(MOMENT), reporter)
    notices.enqueue("restart", dedup="TEST_ONLY")
    try:
        with pytest.raises(asyncio.CancelledError):
            await notices.drain()
        assert not reporter.outbox
        assert (await notices.drain())["sent"] == 0
    finally:
        database.close()


async def test_simultaneous_drains_claim_an_inmemory_report_once(system):
    notices, sent = system
    notices.enqueue("started", dedup="TEST_ONLY")
    a, b = await asyncio.gather(notices.drain(), notices.drain())
    assert a["sent"] + b["sent"] == 1 and len(sent) == 1


async def test_missing_report_config_is_harmless_and_local(tmp_path):
    settings = config(tmp_path)
    database = Database(settings)
    database.initialize()
    reporter = Reporter(settings, console=lambda _: None)
    notices = RuntimeNotices(database, settings, ManualClock(MOMENT), reporter)
    try:
        notices.enqueue("started", dedup="TEST_ONLY")
        result = await notices.drain()
        assert result["disabled"] == 1 and result["sent"] == 0
        assert (reporter.reports_dir / "actions.log").is_file()
    finally:
        database.close()


@pytest.mark.parametrize(
    "kind,dedup", [("resume", "x"), ("started", ""), ("started", "x" * 257), ("started", 1)]
)
def test_notice_input_is_fixed_bounded_and_cannot_encode_control(kind, dedup, system):
    notices, _ = system
    with pytest.raises(ValueError):
        notices.enqueue(kind, dedup=dedup)


@pytest.mark.parametrize("limit", [0, 21, True, 1.5])
async def test_report_batch_bound_enforced(system, limit):
    notices, _ = system
    with pytest.raises(ValueError):
        await notices.drain(limit=limit)
