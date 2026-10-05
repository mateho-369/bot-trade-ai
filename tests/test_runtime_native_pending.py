"""Marked FakeSDK native-thread tracking only; NEVER real MetaTrader5 calls."""

import asyncio
from threading import Event

import pytest

from tests.fake_mt5_sdk import FakeSDK
from tests.test_mt5_client import demo
from tests.test_trading_contracts import NOW
from trading.mt5_client import MT5Client
from trading.types import ConnectionUnavailable, ManualClock


@pytest.mark.parametrize("cancel", [False, True])
async def test_timed_out_async_wrapper_is_not_evidence_native_future_finished(cancel):
    settings, clock = demo(mt5_api_timeout_seconds=0.08), ManualClock(NOW)
    sdk = FakeSDK(clock)
    client = MT5Client(settings, sdk=sdk, clock=clock)
    await client.initialize()
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        assert release.wait(5), "TEST_ONLY blocked fake read must be released"

    sdk.account_hook = blocked
    task = asyncio.create_task(client.get_account_info())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ConnectionUnavailable):
                await task
        assert client.health()["pending_calls"] == 1
        assert client.health()["writes_quarantined"] is True
        with pytest.raises(ConnectionUnavailable, match="prior native work"):
            await client.get_positions()
        assert client.health()["pending_calls"] == 1  # no queue growth behind a hung read.
    finally:
        release.set()
        sdk.account_hook = None
        await client.shutdown()
    assert client.health()["pending_calls"] == 0
    assert not sdk.requests
