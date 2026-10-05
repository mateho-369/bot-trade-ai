"""Exness cent accounts (USC/EUC): exact 1/100 denomination through sizing and trailing."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from core.settings import Settings
from tests.fake_mt5_sdk import FakeSDK
from tests.risk_helpers import make_engine, open_one
from tests.test_trading_contracts import NOW
from trading.currency import (
    CurrencyConverter,
    account_currency_profile,
    cent_parent,
    denomination,
    is_cent_currency,
)
from trading.mock_mt5 import MockMarketData, MockMT5Client
from trading.mt5_client import MT5Client
from trading.order_calculator import OrderCalculator
from trading.trailing_engine import TrailingEngine
from trading.types import IdentityChanged, ManualClock, RiskViolation, Side, SourceKind

D = Decimal


def test_only_exact_cent_codes_are_cent_accounts():
    assert denomination("USC") == ("USD", D("100")) and denomination("EUC") == ("EUR", D("100"))
    assert denomination("USD") == ("USD", D("1")) and denomination("EUR") == ("EUR", D("1"))
    # A stablecoin or lookalike is NEVER relabelled as US cents/dollars.
    for code in ("USDC", "USDT", "US", "usc", "CENT"):
        assert not is_cent_currency(code) and cent_parent(code) is None
    profile = account_currency_profile("USC")
    assert profile == {
        "account_currency": "USC",
        "cent_account": True,
        "parent_currency": "USD",
        "units_per_parent": "100",
    }
    assert account_currency_profile("USD")["cent_account"] is False


async def test_usc_converts_exactly_without_any_quote_or_route():
    cfg = Settings(_env_file=None, account_currency="USC")
    market = MockMarketData(cfg, clock=ManualClock(NOW))
    await market.initialize()
    converter = CurrencyConverter(market, cfg)
    assert await converter.convert(D("200"), "USC", "USD") == D("2")
    assert await converter.convert(D("2"), "USD", "USC") == D("200")
    assert await converter.convert(D("-7"), "USD", "USC") == D("-700")  # liabilities too
    assert await converter.to_usd(D("100000")) == D("1000")
    assert await converter.usd_to_account(D("2")) == D("200")


async def test_euc_uses_the_eur_route_then_exact_cents_and_refuses_without_one():
    cfg = Settings(_env_file=None, account_currency="EUC", account_to_usd_symbols={"EUR": "EURUSD"})
    market = MockMarketData(cfg, clock=ManualClock(NOW))
    await market.initialize()
    converter = CurrencyConverter(market, cfg)
    bid, ask = D("1.10000"), D("1.10012")
    # 100 EUC = 1 EUR; an asset sells EUR at the bid, a liability buys at the ask.
    assert await converter.convert(D("100"), "EUC", "USD") == bid
    assert await converter.convert(D("-100"), "EUC", "USD") == -ask
    assert await converter.convert(bid, "USD", "EUC") == D("100") * bid / ask
    unrouted = Settings(_env_file=None, account_currency="EUC")
    missing = CurrencyConverter(MockMarketData(unrouted, clock=ManualClock(NOW)), unrouted)
    with pytest.raises(RiskViolation, match="explicit conversion-symbol"):
        await missing.convert(D("100"), "EUC", "USD")


async def _flow(currency, balance):
    root = Path(tempfile.mkdtemp(prefix="reflex-cent-"))
    engine = await make_engine(root, account_currency=currency, paper_initial_balance=D(balance))
    try:
        plan, result = await open_one(engine)
        locks = []
        for bid in ("1.10165", "1.10250", "1.10280"):
            await engine.broker.set_tick("EURUSD", D(bid), D(bid) + D("0.00012"))
            await engine.reconcile()
            position = (await engine.broker.get_positions())[0]
            owned = engine.logger.owned(engine.account_key)[0]
            trail = await engine.trailing.plan(position, owned)
            locks.append((trail.sl, trail.lock_level, trail.estimated_net_at_stop_usd))
        account = await engine.broker.get_account_info()
        return plan, result, locks, account
    finally:
        await engine.shutdown()
        engine.database.close()


async def test_cent_account_sizes_trails_and_locks_exactly_like_its_usd_equivalent():
    usd_plan, usd_result, usd_locks, usd_account = await _flow("USD", "1000")
    usc_plan, usc_result, usc_locks, usc_account = await _flow("USC", "100000")
    assert usd_result.status == usc_result.status == "filled"
    # Same lot: the USC balance is NOT mistaken for 100x more dollars.
    assert usc_plan.order.volume == usd_plan.order.volume == D("0.02")
    assert usc_plan.order.tp == usd_plan.order.tp and usc_plan.order.sl == usd_plan.order.sl
    # Account-currency amounts are exactly x100; USD figures are identical.
    assert usc_plan.risk_budget_account == usd_plan.risk_budget_account * 100 == D("500")
    assert usc_plan.worst_loss_account == usd_plan.worst_loss_account * 100
    assert usc_plan.target_profit_usd == usd_plan.target_profit_usd
    assert usc_plan.expected_net_profit_usd == usd_plan.expected_net_profit_usd
    # 30/60/90 locks: same stop prices and the same net-USD protection.
    assert usc_locks == usd_locks and [level for _, level, _ in usc_locks] == [30, 60, 90]
    # Entry half of the $7/lot round turn on 0.02 lots = $0.07 = 7 USC.
    assert usd_account.balance == D("999.93") and usc_account.balance == D("99993")
    assert usc_account.currency == "USC"


async def test_usd_profit_target_becomes_cents_for_the_trailing_tiers():
    cfg = Settings(_env_file=None, account_currency="USC")
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        trailing = TrailingEngine(broker, cfg, SourceKind.SYNTHETIC)
        targets = await trailing.lock_targets_account(D("2"))
        assert targets == {"target": D("200"), "lock_30": D("60"), "lock_60": D("120"), "lock_90": D("180")}
        with pytest.raises(RiskViolation):
            await trailing.lock_targets_account(D("0"))
    usd = Settings(_env_file=None)
    async with MockMT5Client(usd, clock=ManualClock(NOW)) as broker:
        targets = await TrailingEngine(broker, usd, SourceKind.SYNTHETIC).lock_targets_account(D("2"))
        assert targets["target"] == D("2") and targets["lock_90"] == D("1.8")


@pytest.mark.parametrize(("currency", "balance", "scale"), [("USD", "1000", 1), ("USC", "100000", 100)])
async def test_cent_lot_uses_cent_valuation_not_a_dollar_shortcut(currency, balance, scale):
    cfg = Settings(_env_file=None, account_currency=currency, paper_initial_balance=D(balance))
    async with MockMT5Client(cfg, clock=ManualClock(NOW)) as broker:
        lot = await OrderCalculator(broker, cfg).calculate_lot_size(
            "XAUUSD", Side.BUY, D("2610.20"), D("2608")
        )
        # 0.5% of $1,000 (= 100,000 USC): identical 0.02 lot, never 2.00 lots.
        assert lot.volume == D("0.02")
        assert lot.risk_budget_account == D("5") * scale
        assert lot.worst_loss_account == D("4.94") * scale
        move = -(await broker.calculate_profit("XAUUSD", Side.BUY, D("0.01"), D("2610"), D("2609")))
        assert move == D("1") * scale  # 1.00 x 100 oz x 0.01 lot = $1 = 100 USC


def _native(currency, **kwargs):
    cfg = Settings(_env_file=None, mt5_backend="real", account_currency=currency, **kwargs)
    clock = ManualClock(NOW)
    sdk = FakeSDK(clock)
    sdk.account.currency = currency
    sdk.account.balance = sdk.account.equity = sdk.account.margin_free = 100000.0
    return cfg, clock, sdk


async def test_native_cent_commission_and_snapshot_rates_are_exact():
    cfg, clock, sdk = _native("USC")
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        account = await client.get_account_info()
        assert account.currency == "USC" and account.balance == D("100000")
        # $7 round turn per lot x 0.01 lot = $0.07 = 7 USC.
        assert await client._call(lambda: client._cost_sync(D("0.01"), account)) == D("7")
        meta = await client.get_symbol_info("XAUUSD")
        tick = await client.get_tick("XAUUSD")
        snapshot = await client._call(
            lambda: client._snapshot_sync(account, meta, tick, (), D("1"), D("0"), D("2"), entry=True)
        )
        assert snapshot.usd_asset_rate == snapshot.usd_liability_rate == D("0.01")
        assert snapshot.dollars(D("200")) == D("2")


async def test_native_euc_requires_the_eur_route_and_never_guesses():
    cfg, clock, sdk = _native("EUC")
    async with MT5Client(cfg, sdk=sdk, clock=clock) as client:
        account = await client.get_account_info()
        with pytest.raises(RiskViolation, match="USD conversion route"):
            await client._call(lambda: client._cost_sync(D("0.01"), account))


async def test_terminal_currency_mismatch_still_quarantines_but_reports_the_code():
    cfg, clock, sdk = _native("USD")
    sdk.account.currency = "USC"  # .env still declares USD
    client = MT5Client(cfg, sdk=sdk, clock=clock)
    with pytest.raises(IdentityChanged):
        async with client:
            pass
    assert client.observed_account_currency == "USC"
