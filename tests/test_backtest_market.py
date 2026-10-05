"""Causal prefix stability, no future revisions/quotes/partial higher frames or silent FX relabeling."""

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from backtesting.backtester import isolated_settings
from backtesting.dataset import HistoricalDataset
from backtesting.market import HistoricalMarket
from core.settings import Settings
from tests.backtest_helpers import document, fixture_path, patch_csv, patch_manifest, rewrite, step
from trading.types import BrokerError, ManualClock, Side, SourceKind, TradingDisabled


async def market_at(dataset, tmp_path, **changes):
    cfg = isolated_settings(Settings(_env_file=None, **changes), dataset, tmp_path)
    market = HistoricalMarket(dataset, cfg, ManualClock(dataset.manifest.replay_from))
    await market.initialize()
    market.seed()
    return market


async def test_market_implements_read_only_protocol_and_real_tick_timestamps(tmp_path):
    from trading.types import MarketData

    dataset = HistoricalDataset.load(fixture_path(tmp_path))
    market = await market_at(dataset, tmp_path / "run")
    assert isinstance(market, MarketData) and market.source_kind == SourceKind.HISTORICAL
    assert (await market.get_account_info()).source == SourceKind.HISTORICAL
    assert not (await market.get_account_info()).trade_allowed
    old = await market.get_tick("EURUSD")
    market.clock.advance(timedelta(seconds=2))
    assert await market.get_tick("EURUSD") == old  # Never fabricate quote freshness at read time.
    assert await market.get_symbols() == ("EURUSD",)
    assert (await market.select_symbol("EURUSD")).name == "EURUSD"


@pytest.mark.parametrize("timeframe", ["M1", "M5", "M15", "H1"])
async def test_only_complete_asof_bars_are_exposed(tmp_path, timeframe):
    dataset = HistoricalDataset.load(fixture_path(tmp_path, warmup=60, minutes=3))
    market = await market_at(dataset, tmp_path / "run")
    bars = await market.get_candles("EURUSD", timeframe, 100)
    assert (bars.close_time <= market.clock.now()).all()
    assert bars.iloc[-1].close_time == pd.Timestamp(market.clock.now())
    old = bars.copy()
    bars.loc[0, "close"] = 999
    pd.testing.assert_frame_equal(await market.get_candles("EURUSD", timeframe, 100), old)


@pytest.mark.parametrize("timeframe,expected", [("M1", 60), ("M5", 12), ("M15", 4), ("H1", 1)])
async def test_higher_frame_aggregation_is_consistent(tmp_path, timeframe, expected):
    dataset = HistoricalDataset.load(fixture_path(tmp_path))
    market = await market_at(dataset, tmp_path / "run")
    frame = await market.get_candles("EURUSD", timeframe, 100)
    assert len(frame) == expected
    assert int(frame.tick_volume.sum()) == 60 * 60
    assert frame.iloc[0].open == float(dataset.bars["EURUSD"][0].open)
    assert frame.iloc[-1].close == float(dataset.bars["EURUSD"][59].close)


async def test_a_missing_minute_invalidates_containing_higher_frames(tmp_path):
    path = fixture_path(tmp_path)
    patch_csv(path, change=lambda rows: rows.pop(17))
    dataset = HistoricalDataset.load(path)
    market = await market_at(dataset, tmp_path / "run")
    assert len(await market.get_candles("EURUSD", "M5", 100)) == 11
    assert len(await market.get_candles("EURUSD", "M15", 100)) == 3
    with pytest.raises(BrokerError):
        await market.get_candles("EURUSD", "H1", 100)


async def test_future_data_mutation_cannot_change_an_earlier_prefix(tmp_path):
    path = fixture_path(tmp_path, minutes=3)
    original = HistoricalDataset.load(path)
    start = original.manifest.replay_from
    patch_csv(path, change=lambda rows: rows[-1].update(high="2.00000", close="1.99900"))
    revised = HistoricalDataset.load(path)
    first = await market_at(original, tmp_path / "a")
    second = await market_at(revised, tmp_path / "b")
    for timeframe in ("M1", "M5", "M15", "H1"):
        pd.testing.assert_frame_equal(
            await first.get_candles("EURUSD", timeframe, 100),
            await second.get_candles("EURUSD", timeframe, 100),
        )
    assert first.clock.now() == start and await first.get_tick("EURUSD") == await second.get_tick("EURUSD")
    assert original.dataset_sha256 != revised.dataset_sha256  # Provenance changes, decisions do not peek.


async def test_delayed_last_minute_is_not_retroactively_known(tmp_path):
    path = fixture_path(tmp_path, minutes=3)
    body = document(path)
    delay = body["replay_from"]
    patch_csv(path, change=lambda rows: rows[59].update(available_at="2024-01-01T01:01:00Z"))
    # Preserve monotonic publication order of later M1 records too.
    patch_csv(
        path, change=lambda rows: [row.update(available_at="2024-01-01T01:01:00Z") for row in rows[60:61]]
    )
    dataset = HistoricalDataset.load(path)
    market = await market_at(dataset, tmp_path / "run")
    assert market.clock.now().isoformat() == delay
    assert (await market.get_candles("EURUSD", "M5", 100)).iloc[-1].close_time < market.clock.now()
    with pytest.raises(BrokerError):
        await market.get_candles("EURUSD", "H1", 100)
    step(market, 60)
    assert len(await market.get_candles("EURUSD", "H1", 100)) == 1


@pytest.mark.parametrize("count", [True, 0, -1, 10001, "20"])
async def test_candle_request_size_cannot_bypass_bound(tmp_path, count):
    market = await market_at(HistoricalDataset.load(fixture_path(tmp_path)), tmp_path / "run")
    with pytest.raises(BrokerError):
        await market.get_candles("EURUSD", "M1", count)


async def test_future_asof_naive_and_unknown_data_requests_are_blocked(tmp_path):
    market = await market_at(HistoricalDataset.load(fixture_path(tmp_path)), tmp_path / "run")
    for cutoff in (market.clock.now() + timedelta(seconds=1), market.clock.now().replace(tzinfo=None)):
        with pytest.raises(BrokerError):
            await market.get_candles("EURUSD", "M5", 20, as_of=cutoff)
    with pytest.raises(BrokerError):
        await market.get_candles("EURUSD", "UNKNOWN", 20)
    with pytest.raises(BrokerError):
        await market.get_tick("UNKNOWN")


@pytest.mark.parametrize("side,expected", [(Side.BUY, "10"), (Side.SELL, "-10")])
async def test_linear_contract_profit_is_account_currency_not_a_tick_value_guess(tmp_path, side, expected):
    market = await market_at(HistoricalDataset.load(fixture_path(tmp_path)), tmp_path / "run")
    pnl = await market.calculate_profit("EURUSD", side, Decimal("0.1"), Decimal("1.1"), Decimal("1.101"))
    assert pnl == Decimal(expected)
    margin = await market.calculate_margin("EURUSD", side, Decimal("0.1"), Decimal("1.1"))
    assert margin == Decimal("110")


async def test_nonusd_cash_conversion_uses_bid_ask_and_no_usd_relabel(tmp_path):
    path = fixture_path(tmp_path)
    patch_manifest(path, account_currency="EUR")
    market = await market_at(
        HistoricalDataset.load(path),
        tmp_path / "run",
        account_currency="EUR",
        account_to_usd_symbols={"EUR": "EURUSD"},
    )
    tick = await market.get_tick("EURUSD")
    gain = await market.calculate_profit("EURUSD", Side.BUY, Decimal("0.1"), Decimal("1.1"), Decimal("1.101"))
    loss = await market.calculate_profit("EURUSD", Side.BUY, Decimal("0.1"), Decimal("1.101"), Decimal("1.1"))
    assert gain == Decimal("10") / tick.ask and loss == Decimal("-10") / tick.bid


async def test_forex_base_margin_requires_live_asof_currency_leg(tmp_path):
    path = fixture_path(tmp_path)
    body = document(path)
    body["symbols"][0]["margin_model"] = "forex_base"
    rewrite(path, body)
    market = await market_at(
        HistoricalDataset.load(path), tmp_path / "run", account_to_usd_symbols={"EUR": "EURUSD"}
    )
    tick = await market.get_tick("EURUSD")
    assert (
        await market.calculate_margin("EURUSD", Side.BUY, Decimal("0.1"), tick.ask)
        == Decimal("100") * tick.ask
    )
    market.clock.advance(timedelta(seconds=11))
    with pytest.raises(BrokerError):
        await market.calculate_margin("EURUSD", Side.BUY, Decimal("0.1"), tick.ask)


def test_historical_adapter_cannot_enter_a_paper_or_native_mode(tmp_path):
    dataset = HistoricalDataset.load(fixture_path(tmp_path))
    cfg = Settings(_env_file=None)
    with pytest.raises(TradingDisabled):
        HistoricalMarket(dataset, cfg, ManualClock(dataset.manifest.replay_from))
