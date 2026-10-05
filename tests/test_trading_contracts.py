from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from core.security import sha256_json
from core.settings import Settings
from trading.candles import validated_candles
from trading.mock_mt5 import synthetic_catalogue
from trading.price_rules import adverse_price, floor_volume, protection_prices, snap, validate_entry
from trading.types import (
    AccountInfo,
    AccountKind,
    BrokerCommand,
    BrokerError,
    InvalidOrder,
    ManualClock,
    MarketOrder,
    Operation,
    Position,
    RiskViolation,
    Side,
    SourceKind,
    StaleData,
    Tick,
    decimal_value,
)

D = Decimal
NOW = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
KEY = sha256_json({"fixture": "contracts"})


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


def order(side=Side.BUY, **kwargs):
    values = dict(
        symbol="XAUUSD",
        side=side,
        volume=D("0.01"),
        reference_price=D("2610.20") if side == Side.BUY else D("2610"),
        sl=D("2608") if side == Side.BUY else D("2612"),
        tp=D("2616") if side == Side.BUY else D("2604"),
        idempotency_key=KEY,
        created_at=NOW,
    )
    values.update(kwargs)
    return MarketOrder(**values)


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", "-Infinity", None, "not-a-number"])
def test_sdk_decimal_boundary_rejects_bad_numbers(bad):
    with pytest.raises(BrokerError):
        decimal_value(bad)


def test_risk_capital_does_not_spend_credit_or_floating_wins():
    account = AccountInfo(
        1,
        "private",
        "USD",
        AccountKind.DEMO,
        SourceKind.TEST_SDK,
        D("1000"),
        D("1500"),
        D("0"),
        D("1500"),
        credit=D("600"),
    )
    assert account.risk_capital == D("900")
    assert replace(account, equity=D("1600")).risk_capital == D("1000")
    assert "private" not in repr(account)


@pytest.mark.parametrize(
    "field,value",
    [
        ("volume", 0.01),
        ("sl", D("0")),
        ("tp", D("NaN")),
        ("idempotency_key", "retry-1"),
        ("side", "buy"),
        ("created_at", NOW.replace(tzinfo=None)),
    ],
)
def test_orders_require_decimal_valid_direction_utc_and_strong_key(field, value):
    with pytest.raises(BrokerError):
        order(**{field: value})


def test_maintenance_hash_is_stable_but_payload_bound():
    first = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=D("2609"))
    assert first.request_hash == replace(first, created_at=NOW + timedelta(seconds=20)).request_hash
    assert first.request_hash != replace(first, sl=D("2609.1")).request_hash
    opened = BrokerCommand(Operation.OPEN, KEY, NOW, order=order())
    assert (
        opened.request_hash
        != replace(opened, order=replace(order(), created_at=NOW + timedelta(seconds=1))).request_hash
    )


def test_native_tick_grid_is_not_decimal_digits_and_lot_never_rounds_up():
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], tick_size=D("0.25"))
    assert snap(D("100.12"), meta.tick_size, up=True) == D("100.25")
    assert snap(D("100.12"), meta.tick_size, up=False) == D("100.00")
    assert adverse_price(D("100"), Side.BUY, meta, 10, entry=True) == D("100.25")
    assert adverse_price(D("100"), Side.SELL, meta, 10, entry=True) == D("99.75")
    assert adverse_price(D("100"), Side.BUY, meta, 10, entry=False) == D("99.75")
    assert floor_volume(D("0.009"), meta) == 0
    assert floor_volume(D("0.0199"), meta) == D("0.01")


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_stop_protection_only_improves(side):
    meta = synthetic_catalogue()[0]["XAUUSD"]
    position = Position(
        17,
        200,
        "XAUUSD",
        side,
        D("0.01"),
        D("2610"),
        D("2608") if side == Side.BUY else D("2612"),
        D("2616") if side == Side.BUY else D("2604"),
        NOW,
        42,
    )
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    improved = D("2609") if side == Side.BUY else D("2611")
    command = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=improved)
    assert protection_prices(command, position, meta, tick, settings())[0] == improved
    with pytest.raises(RiskViolation):
        protection_prices(replace(command, sl=position.sl - side.sign), position, meta, tick, settings())
    with pytest.raises(InvalidOrder):
        protection_prices(replace(command, sl=D("0")), position, meta, tick, settings())
    with pytest.raises(InvalidOrder):
        protection_prices(replace(command, tp=D("0")), position, meta, tick, settings())


def test_existing_freeze_zone_and_tp_extension_are_not_bypassed():
    meta = replace(synthetic_catalogue()[0]["XAUUSD"], freeze_level=30)
    position = Position(
        17, 200, "XAUUSD", Side.BUY, D("0.01"), D("2610.20"), D("2609.80"), D("2616"), NOW, 42
    )
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    command = BrokerCommand(Operation.PROTECT, KEY, NOW, ticket=17, position_identifier=200, sl=D("2609.90"))
    with pytest.raises(InvalidOrder):
        protection_prices(command, position, meta, tick, settings())
    with pytest.raises(RiskViolation):
        protection_prices(
            replace(command, sl=None, tp=D("2617")), position, replace(meta, freeze_level=0), tick, settings()
        )


@pytest.mark.parametrize(
    "case",
    ["old_quote", "future_quote", "old_order", "spread", "deviation", "wrong_symbol", "disabled", "no_stops"],
)
def test_entry_fails_closed(case):
    meta = synthetic_catalogue()[0]["XAUUSD"]
    tick = Tick("XAUUSD", D("2610"), D("2610.20"), NOW)
    current = order()
    if case == "old_quote":
        tick = replace(tick, time=NOW - timedelta(seconds=11))
    if case == "future_quote":
        tick = replace(tick, time=NOW + timedelta(seconds=3))
    if case == "old_order":
        current = replace(current, created_at=NOW - timedelta(seconds=31))
    if case == "spread":
        tick = replace(tick, ask=D("2610.40"))
    if case == "deviation":
        tick = replace(tick, bid=D("2610.20"), ask=D("2610.40"))
    if case == "wrong_symbol":
        current = replace(current, symbol="UNAPPROVED")
    if case == "disabled":
        meta = replace(meta, trade_mode=0)
    if case == "no_stops":
        meta = replace(meta, order_mode=1)
    with pytest.raises(BrokerError):
        validate_entry(current, meta, tick, settings(), ManualClock(NOW))


def candle_frame():
    return pd.DataFrame(
        [
            dict(
                time=NOW - timedelta(minutes=10 - n * 5),
                open=10.0,
                high=12.0,
                low=9.0,
                close=11.0,
                tick_volume=50,
                spread=1,
                real_volume=0,
            )
            for n in range(3)
        ]
    )


def test_candles_exclude_forming_bars_and_carry_close_time():
    result = validated_candles(candle_frame(), "M5", NOW)
    assert len(result) == 2
    assert result.close_time.max() == pd.Timestamp(NOW)


@pytest.mark.parametrize("case", ["duplicate", "unordered", "nan", "negative_volume", "range", "naive"])
def test_invalid_candles_are_rejected(case):
    frame = candle_frame().iloc[:2].copy()
    if case == "duplicate":
        frame.loc[1, "time"] = frame.loc[0, "time"]
    if case == "unordered":
        frame = frame.iloc[::-1]
    if case == "nan":
        frame.loc[0, "close"] = float("nan")
    if case == "negative_volume":
        frame.loc[0, "tick_volume"] = -1
    if case == "range":
        frame.loc[0, "high"] = 5
    if case == "naive":
        frame["time"] = frame.time.dt.tz_localize(None)
    with pytest.raises(BrokerError):
        validated_candles(frame, "M5", NOW)


def test_simulation_config_slippage_and_timeout_bounds():
    with pytest.raises(ValidationError):
        settings(paper_slippage_points=11)
    with pytest.raises(ValidationError):
        settings(mt5_api_timeout_seconds=0)
    with pytest.raises(ValidationError):
        settings(paper_initial_balance=0)
    with pytest.raises(ValueError):
        ManualClock(NOW).advance(timedelta(seconds=-1))


def test_tick_side_convention_and_staleness():
    tick = Tick("EURUSD", D("1.10"), D("1.11"), NOW)
    assert tick.entry(Side.BUY) == D("1.11")
    assert tick.exit(Side.BUY) == D("1.10")
    assert tick.entry(Side.SELL) == D("1.10")
    assert tick.exit(Side.SELL) == D("1.11")
    with pytest.raises(StaleData):
        tick.fresh(ManualClock(NOW + timedelta(seconds=11)), 10)
