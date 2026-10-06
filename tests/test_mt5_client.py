import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Event, get_ident
from types import SimpleNamespace as NS

import pytest

from core.security import sha256_json
from core.settings import Settings
from tests.fake_mt5_sdk import FakeSDK, FixtureAuthority
from tests.test_trading_contracts import NOW, order
from trading.mt5_client import MT5Client
from trading.types import (
    BrokerError,
    ConnectionUnavailable,
    IdentityChanged,
    InvalidOrder,
    ManualClock,
    ResultStatus,
    RiskViolation,
    Side,
    SourceKind,
    TradingDisabled,
    UncertainExecution,
)

D = Decimal


def key(name):
    return sha256_json({"native_test": name})


def demo(**kwargs):
    return Settings(_env_file=None, mt5_backend="real", paper_trading=False, **kwargs)


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
async def test_native_request_math_identifiers_and_no_duplicate_send(side):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    authority = FixtureAuthority(cfg, clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        current = order(side=side)
        result = await (
            client.open_market_buy(current) if side == Side.BUY else client.open_market_sell(current)
        )
        duplicate = await (
            client.open_market_buy(current) if side == Side.BUY else client.open_market_sell(current)
        )
        assert duplicate == result and len(sdk.requests) == 1
        request = sdk.requests[0]
        assert request["type"] == (0 if side == Side.BUY else 1)
        assert request["price"] == (2610.2 if side == Side.BUY else 2610.0)
        assert request["sl"] == float(current.sl) and request["tp"] == float(current.tp)
        assert request["magic"] == cfg.mt5_magic_number and len(request["comment"]) <= 31
        assert request["type_filling"] == 0  # Capability 1 FOK maps to enum 0.
        assert result.order_ticket == 777 and result.deal_ticket == 888
        assert result.position_identifier is None and result.requires_reconciliation
        assert client.source_kind == SourceKind.TEST_SDK
        with pytest.raises(RiskViolation):
            await client.open_market_buy(replace(order(), tp=D("2617")))
        thread_ids = {thread for _, thread in sdk.calls}
        assert len(thread_ids) == 1 and get_ident() not in thread_ids


async def test_all_real_writes_default_to_deny_all_even_in_demo():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        with pytest.raises(TradingDisabled, match="no durable"):
            await client.open_market_buy(order())
        sdk.positions = (sdk.owned_position(),)
        with pytest.raises(TradingDisabled):
            await client.close_position(42, position_identifier=50042, idempotency_key=key("close"))
        with pytest.raises(TradingDisabled):
            await client.modify_sl(42, D("2609"), position_identifier=50042, idempotency_key=key("sl"))
        assert not sdk.requests and not any(name == "order_check" for name, _ in sdk.calls)


async def test_paper_native_source_is_strictly_read_only_even_with_fixture_authority():
    cfg, clock = Settings(_env_file=None, mt5_backend="real"), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=FixtureAuthority(cfg, clock)) as client:
        with pytest.raises(TradingDisabled, match="strictly read-only"):
            await client.open_market_buy(order())
        assert not sdk.requests


@pytest.mark.parametrize(
    "case",
    [
        "foreign",
        "unprotected",
        "same_symbol",
        "pending",
        "positions_none",
        "orders_none",
        "python_disabled",
        "account_disabled",
        "stale",
    ],
)
async def test_unstable_or_unmanaged_exposure_blocks_before_authorization(case):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    authority = FixtureAuthority(cfg, clock)
    if case == "foreign":
        sdk.positions = (sdk.owned_position(symbol="EURUSD", magic=1),)
    if case == "unprotected":
        sdk.positions = (sdk.owned_position(symbol="EURUSD", sl=0.0),)
    if case == "same_symbol":
        sdk.positions = (sdk.owned_position(),)
    if case == "pending":
        sdk.pending = (NS(ticket=9),)
    if case == "positions_none":
        sdk.positions = None
    if case == "orders_none":
        sdk.pending = None
    if case == "python_disabled":
        sdk.tradeapi_disabled = True
    if case == "account_disabled":
        sdk.account.trade_allowed = False
    if case == "stale":
        sdk.tick_time = NOW - timedelta(seconds=11)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests and not authority.authorized


@pytest.mark.parametrize("change", ["login", "currency", "kind"])
async def test_pinned_identity_and_mode_mismatch_latch_quarantine(change):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        if change == "login":
            sdk.account.login += 1
        if change == "currency":
            sdk.account.currency = "EUR"
        if change == "kind":
            sdk.account.trade_mode = 2
        with pytest.raises(IdentityChanged):
            await client.get_account_info()
        assert client.health()["writes_quarantined"]
        assert not sdk.requests


async def test_disconnection_read_reconnects_but_does_not_switch_accounts():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        sdk.connected = False
        await client.get_account_info()
        assert sdk.init_count == 2
        sdk.account.login += 1
        sdk.connected = False
        with pytest.raises(IdentityChanged):
            await client.get_account_info()
        assert client.health()["writes_quarantined"]


async def test_one_sdk_process_lease_and_safe_failed_initialize_cleanup():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    first = MT5Client(cfg, sdk=sdk, clock=clock)
    await first.initialize()
    second = MT5Client(cfg, sdk=sdk, clock=clock)
    with pytest.raises(ConnectionUnavailable, match="leased"):
        async with second:
            pass
    assert sdk.shutdown_count == 0  # Failed second cannot shut down first's SDK.
    await first.shutdown()
    async with MT5Client(cfg, sdk=sdk, clock=clock):
        pass
    assert sdk.shutdown_count == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_key", "different"),
        ("request_hash", "f" * 64),
        ("config_hash", "f" * 64),
        ("expires_at", NOW),
        ("max_volume", D("0")),
        ("max_loss_account", D("0")),
        ("entry_gates_verified", False),
    ],
)
async def test_authorization_binding_and_unsent_intent_outcome(field, value):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    authority.mutate_grant = lambda grant: replace(grant, **{field: value})
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests
        assert authority.results[-1].status == ResultStatus.REJECTED
        assert authority.results[-1].reason == "preflight_aborted_no_send"


@pytest.mark.parametrize("check", [None, NS(retcode=10019)])
async def test_order_check_failure_is_definitely_unsent_and_reported(check):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.check = check
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == ResultStatus.REJECTED
        assert not sdk.requests and authority.results == [result]


@pytest.mark.parametrize("mutation", ["tick_move", "exposure", "permission", "equity", "pending", "expired"])
async def test_second_snapshot_prevents_toctou_entry_during_authorization(mutation):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)

    def mutate():
        if mutation == "tick_move":
            sdk.bid, sdk.ask = 2611.0, 2611.2
        if mutation == "exposure":
            sdk.positions = (sdk.owned_position(),)
        if mutation == "permission":
            sdk.tradeapi_disabled = True
        if mutation == "equity":
            sdk.account.equity = 100.0
        if mutation == "pending":
            sdk.pending = (NS(ticket=1),)
        if mutation == "expired":
            clock.advance(timedelta(seconds=21))

    authority.after_authorize = mutate
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(BrokerError):
            await client.open_market_buy(order())
        assert not sdk.requests and authority.latest == ResultStatus.REJECTED


@pytest.mark.parametrize(
    "code,status,quarantine",
    [
        (10009, ResultStatus.FILLED, False),
        (10010, ResultStatus.PARTIAL, True),
        (10008, ResultStatus.ACCEPTED, True),
        (10004, ResultStatus.REJECTED, False),
        (10019, ResultStatus.REJECTED, False),
        (10012, ResultStatus.UNKNOWN, True),
        (10031, ResultStatus.UNKNOWN, True),
        (43210, ResultStatus.UNKNOWN, True),
    ],
)
async def test_broker_acknowledgement_categories_never_infer_position_ticket(code, status, quarantine):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.ack.retcode = code
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == status and result.position_identifier is None
        assert client.health()["writes_quarantined"] == quarantine
        assert len(sdk.requests) == 1
        if quarantine:
            await client.reconnect()
            with pytest.raises(UncertainExecution):
                await client.open_market_buy(order(idempotency_key=key("retry")))
            assert len(sdk.requests) == 1


@pytest.mark.parametrize("case", ["none", "send_exception", "invalid_volume", "switch_after_send"])
async def test_ambiguous_send_never_retries(case):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    if case == "none":
        sdk.ack = None
    if case == "send_exception":
        sdk.send_error = RuntimeError("TEST-secret raw")
    if case == "invalid_volume":
        sdk.ack.volume = 5.0
    if case == "switch_after_send":
        sdk.send_hook = lambda: setattr(sdk.account, "login", 999)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        result = await client.open_market_buy(order())
        assert result.status == ResultStatus.UNKNOWN and client.health()["writes_quarantined"]
        with pytest.raises(UncertainExecution):
            await client.open_market_buy(order())
        assert len(sdk.requests) == 1


async def test_durable_callback_failure_quarantines_already_sent_fill_without_leaking_error(caplog):
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    authority.fail_result = True
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(UncertainExecution) as error:
            await client.open_market_buy(order())
        assert "TEST-persistence" not in str(error.value) + caplog.text
        assert client.health()["writes_quarantined"] and len(sdk.requests) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_nonblocking_timeout_or_cancel_quarantines_and_drains_late_ack(cancel):
    cfg, clock = demo(mt5_api_timeout_seconds=0.08), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.block_send = True
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    try:
        assert await asyncio.to_thread(sdk.send_entered.wait, 1)
        # A blocked SDK worker must not block the event loop / scheduler.
        sentinel = asyncio.create_task(asyncio.sleep(0.001, result="responsive"))
        assert await sentinel == "responsive"
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(UncertainExecution):
                await task
        assert client.health()["writes_quarantined"]
        with pytest.raises(UncertainExecution):
            await client.open_market_buy(order())
        sdk.send_release.set()
        assert await asyncio.to_thread(authority.result_event.wait, 1)
        assert authority.latest == ResultStatus.FILLED
        authority.on_uncertain(None, "late_observer")
        assert authority.latest == ResultStatus.FILLED
        assert len(sdk.requests) == 1
    finally:
        sdk.send_release.set()
        await client.shutdown()


async def test_shutdown_timeout_retains_sdk_lease_until_worker_really_drains():
    cfg, clock = demo(mt5_api_timeout_seconds=0.05), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.block_send = True
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    assert await asyncio.to_thread(sdk.send_entered.wait, 1)
    try:
        with pytest.raises(UncertainExecution):
            await task
        with pytest.raises(ConnectionUnavailable):
            await client.shutdown()
        second = MT5Client(cfg, sdk=sdk, clock=clock)
        with pytest.raises(ConnectionUnavailable, match="leased"):
            async with second:
                pass
    finally:
        sdk.send_release.set()
        assert await asyncio.to_thread(sdk.shutdown_done.wait, 1)
    async with MT5Client(cfg, sdk=sdk, clock=clock):
        pass


async def test_read_timeout_latches_writes_and_is_not_reported_as_empty_data():
    cfg, clock = demo(mt5_api_timeout_seconds=0.05), ManualClock(NOW)
    sdk = FakeSDK(clock)
    client = MT5Client(cfg, sdk=sdk, clock=clock)
    await client.initialize()
    release, entered = Event(), Event()

    def blocked():
        entered.set()
        release.wait(2)

    sdk.account_hook = blocked
    try:
        task = asyncio.create_task(client.get_account_info())
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(ConnectionUnavailable):
            await task
        assert client.health()["writes_quarantined"]
    finally:
        sdk.account_hook = None
        release.set()
        await client.shutdown()


async def test_close_and_modify_keep_position_identifiers_and_idempotency_after_time_changes():
    cfg, clock = demo(), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.positions = (sdk.owned_position(),)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(TradingDisabled):
            await client.close_position(42, position_identifier=50043, idempotency_key=key("wrong"))
        with pytest.raises(RiskViolation):
            await client.modify_sl(42, D("2607"), position_identifier=50042, idempotency_key=key("loosen"))
        changed = await client.modify_sl(42, D("2609"), position_identifier=50042, idempotency_key=key("sl"))
        clock.advance(timedelta(seconds=1))
        assert changed == await client.modify_sl(
            42, D("2609"), position_identifier=50042, idempotency_key=key("sl")
        )
        assert sdk.requests[0]["action"] == 6 and sdk.requests[0]["position"] == 42
        closed = await client.close_position(42, position_identifier=50042, idempotency_key=key("close"))
        clock.advance(timedelta(seconds=1))
        assert closed == await client.close_position(
            42, position_identifier=50042, idempotency_key=key("close")
        )
        assert sdk.requests[-1]["position"] == 42 and sdk.requests[-1]["type"] == 1
        assert sdk.requests[-1]["price"] == 2610.0 and len(sdk.requests) == 2


@pytest.mark.parametrize(
    "approved,target,success", [(False, "2617", False), (True, "2617", True), (True, "2618", False)]
)
async def test_tp_extension_needs_authority_and_original_target_bound(approved, target, success):
    cfg, clock = demo(allow_tp_extension=True), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    sdk.positions = (sdk.owned_position(),)
    authority.mutate_grant = lambda grant: replace(grant, allow_tp_extension=approved, original_tp=D("2616"))
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        if success:
            await client.modify_tp(42, D(target), position_identifier=50042, idempotency_key=key("tp"))
            assert len(sdk.requests) == 1
        else:
            with pytest.raises(BrokerError):
                await client.modify_tp(42, D(target), position_identifier=50042, idempotency_key=key("tp"))
            assert not sdk.requests and authority.latest == ResultStatus.REJECTED


def test_filling_capabilities_are_not_native_enums():
    from trading.mock_mt5 import synthetic_catalogue

    meta = synthetic_catalogue()[0]["XAUUSD"]
    assert MT5Client._filling(replace(meta, filling_mode=1)) == 0
    assert MT5Client._filling(replace(meta, filling_mode=2)) == 1
    with pytest.raises(InvalidOrder):
        MT5Client._filling(replace(meta, filling_mode=0, execution_mode=2))
    assert MT5Client._filling(replace(meta, filling_mode=0, execution_mode=1)) == 2


async def test_native_candles_closed_only_and_deals_include_cost_and_cash_legs():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    rows = []
    for index, (kind, entry, profit, commission) in enumerate(
        [(0, 0, 0.0, -0.07), (1, 1, 5.0, -0.07), (2, 0, 100.0, 0.0)]
    ):
        rows.append(
            NS(
                ticket=index + 1,
                order=10 + index,
                position_id=50042 if kind != 2 else 0,
                symbol="XAUUSD" if kind != 2 else "",
                type=kind,
                entry=entry,
                time=int(NOW.timestamp()),
                time_msc=int(NOW.timestamp() * 1000),
                volume=0.01 if kind != 2 else 0.0,
                price=2610.0,
                profit=profit,
                commission=commission,
                swap=0.0,
                fee=0.0,
                magic=cfg.mt5_magic_number,
                reason=0,
                comment="test",
            )
        )
    sdk.deals = tuple(rows)
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        frame = await client.get_candles("XAUUSD", "M5", 5)
        assert len(frame) == 5 and frame.close_time.max().to_pydatetime() <= clock.now()
        with pytest.raises(BrokerError):
            await client.get_candles("XAUUSD", "M5", as_of=NOW + timedelta(seconds=1))
        deals = await client.get_deals(NOW - timedelta(seconds=1))
        assert len(deals) == 3 and deals[2].entry == "cash"
        assert deals[0].net + deals[1].net == D("4.86")
        assert (await client.get_closed_deals(NOW))[0].net == D("4.93")  # Not the whole trade PnL!
        sdk.deals = None
        with pytest.raises(ConnectionUnavailable):
            await client.get_deals(NOW)


def test_live_trading_flag_is_refused_before_native_client_creation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="LIVE_TRADING=true is refused"):
        Settings(
            _env_file=None,
            mt5_backend="real",
            paper_trading=False,
            demo_mode=False,
            live_trading=True,
        )


def test_unmarked_sdk_injection_cannot_accidentally_use_native_module():
    with pytest.raises(TradingDisabled, match="marked test SDK"):
        MT5Client(demo(), sdk=object())


async def test_timeout_during_order_check_is_reported_unsent_when_worker_drains():
    cfg, clock = demo(mt5_api_timeout_seconds=0.08), ManualClock(NOW)
    sdk, authority = FakeSDK(clock), FixtureAuthority(cfg, clock)
    entered, release = Event(), Event()

    def slow_check():
        entered.set()
        release.wait(2)

    sdk.check_hook = slow_check
    client = MT5Client(cfg, sdk=sdk, clock=clock, authority=authority)
    await client.initialize()
    task = asyncio.create_task(client.open_market_buy(order()))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(UncertainExecution):
            await task
        release.set()
        assert await asyncio.to_thread(authority.result_event.wait, 1)
        assert authority.latest == ResultStatus.REJECTED and not sdk.requests
        assert client.health()["writes_quarantined"]
    finally:
        release.set()
        await client.shutdown()


async def test_native_close_boundary_uses_next_open_and_never_peeks_at_long_session_bar():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    hours = (NOW - timedelta(hours=3), NOW - timedelta(hours=2), NOW - timedelta(minutes=30), NOW)
    rows = [
        dict(
            time=int(instant.timestamp()),
            open=2610.0,
            high=2611.0,
            low=2609.0,
            close=2610.5,
            tick_volume=100,
            spread=20,
            real_volume=0,
        )
        for instant in hours
    ]
    sdk.copy_rates_from_pos = lambda symbol, tf, start, count: rows[-count:]
    sdk.copy_rates_from = lambda symbol, tf, cutoff, count: [
        row for row in rows if row["time"] <= cutoff.timestamp()
    ][-count:]
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        frame = await client.get_candles("XAUUSD", "H1", 3)
        assert frame.iloc[1].close_time.to_pydatetime() == NOW - timedelta(minutes=30)
        historical = await client.get_candles("XAUUSD", "H1", 3, as_of=NOW - timedelta(minutes=50))
        assert len(historical) == 1
        assert historical.iloc[0].time.to_pydatetime() == hours[0]


async def test_post_check_hook_can_veto_a_previously_valid_permit_before_native_send():
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)

    class LateVetoAuthority(FixtureAuthority):
        def before_send(self, command, snapshot, grant):
            assert snapshot.data_source == SourceKind.TEST_SDK
            assert "order_check" in [name for name, _ in sdk.calls]
            raise TradingDisabled("TEST-only local operator paused after order_check")

    authority = LateVetoAuthority(cfg, clock)
    async with MT5Client(cfg, sdk=sdk, clock=clock, authority=authority) as client:
        with pytest.raises(TradingDisabled, match="after order_check"):
            await client.open_market_buy(order())
        assert not sdk.requests and authority.latest == ResultStatus.REJECTED


@pytest.mark.parametrize(
    "state,expected", [(2, True), (4, True), (5, True), (6, True), (0, False), (1, False), (3, False)]
)
async def test_native_order_finality_requires_actual_final_history_not_pending_absence(state, expected):
    cfg, clock = demo(), ManualClock(NOW)
    sdk = FakeSDK(clock)
    sdk.history_orders_get = lambda *, ticket: (
        NS(ticket=ticket, magic=cfg.mt5_magic_number, state=state, volume_initial=0.02),
    )
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        assert await client.get_settled_orders((777,)) == (frozenset({777}) if expected else frozenset())
