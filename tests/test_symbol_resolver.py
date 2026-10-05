"""Dynamic symbol discovery: broker suffixes, specs, spread caps, exposure, .env persistence."""

import json
import os
import stat
from dataclasses import replace
from decimal import Decimal
from functools import partial
from types import SimpleNamespace

import pytest

import trading.mt5_client as mt5_module
from core.settings import Settings
from scripts import resolve_symbols as cli
from tests.fake_mt5_sdk import FakeSDK
from tests.test_trading_contracts import NOW
from trading.mock_mt5 import MockMarketData, synthetic_catalogue
from trading.mt5_client import MT5Client
from trading.symbol_manager import SymbolManager
from trading.symbol_resolver import (
    broker_suffix,
    discover,
    infer_news_currencies,
    resolve_symbols,
    suggest_spread_limit,
    update_env_file,
)
from trading.types import ManualClock

D = Decimal


@pytest.mark.parametrize(
    ("native", "suffix"),
    [
        ("XAUUSD", ""),
        ("XAUUSD.", "."),
        ("XAUUSDm", "m"),
        ("XAUUSDc", "c"),
        ("XAUUSD.raw", ".raw"),
        ("XAUUSD-ecn", "-ecn"),
        ("XAUUSD_i", "_i"),
        ("XAUUSD#", "#"),
        ("XAUUSDpro", "pro"),
        ("XAUUSDT", None),  # uppercase tail = a different instrument
        ("XAUUSDmicro", None),  # lowercase tails are short
        ("XAUEUR", None),
        ("xauusdm", None),
        ("XAUUSD.toolongsuffix", None),
    ],
)
def test_suffix_grammar(native, suffix):
    assert broker_suffix("XAUUSD", native) == suffix


def test_exness_standard_and_cent_suffixes_resolve_without_configuration():
    standard = ["XAUUSDm", "EURUSDm", "BTCUSDm", "ETHUSDm", "ETHUSDTm", "USTECm"]
    result = resolve_symbols(("XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"), standard)
    assert result.aliases == {
        "XAUUSD": "XAUUSDm",
        "EURUSD": "EURUSDm",
        "BTCUSD": "BTCUSDm",
        "ETHUSD": "ETHUSDm",
    }
    assert not result.errors and result.detected_suffix == "m"
    cent = resolve_symbols(("XAUUSD", "GBPUSD"), ["XAUUSDc", "GBPUSDc", "EURUSDc"])
    assert cent.aliases == {"XAUUSD": "XAUUSDc", "GBPUSD": "GBPUSDc"} and cent.detected_suffix == "c"


def test_exact_names_need_no_alias():
    result = resolve_symbols(("EURUSD", "US30"), ["EURUSD", "US30", "GBPUSD"])
    assert result.natives == {"EURUSD": "EURUSD", "US30": "US30"} and result.aliases == {}
    assert result.methods == {"EURUSD": "exact", "US30": "exact"}


def test_ambiguity_is_refused_unless_suffix_votes_preference_or_market_watch_decide():
    offered = ["EURUSD", "EURUSD.raw"]
    alone = resolve_symbols(("EURUSD",), offered)
    assert "ambiguous" in alone.errors["EURUSD"] and not alone.natives
    voted = resolve_symbols(("EURUSD", "XAUUSD"), offered + ["XAUUSD.raw"])
    assert voted.natives["EURUSD"] == "EURUSD.raw" and voted.methods["EURUSD"] == "suffix_vote"
    preferred = resolve_symbols(("EURUSD",), offered, preferred_suffix="")
    assert preferred.natives == {"EURUSD": "EURUSD"} and preferred.methods["EURUSD"] == "preferred_suffix"
    watched = resolve_symbols(("EURUSD",), offered, market_watch=["EURUSD.raw"])
    assert watched.natives == {"EURUSD": "EURUSD.raw"} and watched.methods["EURUSD"] == "market_watch"


def test_explicit_alias_always_wins_and_missing_symbols_stay_disabled():
    result = resolve_symbols(
        ("GOLD", "EURUSD", "NOPE"),
        ["XAUUSD.s", "EURUSDm", "EURUSD"],
        explicit_aliases={"GOLD": "XAUUSD.s", "EURUSD": "EURUSD"},
    )
    assert result.natives == {"GOLD": "XAUUSD.s", "EURUSD": "EURUSD"}
    assert result.methods["GOLD"] == "explicit_alias" and "not offered" in result.errors["NOPE"]
    stale = resolve_symbols(("GOLD",), ["XAUUSD"], explicit_aliases={"GOLD": "XAUUSD.s"})
    assert "configured alias" in stale.errors["GOLD"]


def test_two_logicals_can_never_share_a_broker_symbol():
    result = resolve_symbols(("GOLD", "XAUUSD"), ["XAUUSD"], explicit_aliases={"GOLD": "XAUUSD"})
    assert not result.natives and set(result.errors) == {"GOLD", "XAUUSD"}


def test_news_exposure_is_inferred_only_from_fiat_metadata():
    symbols, _ = synthetic_catalogue()
    assert infer_news_currencies(symbols["XAUUSD"]) == ("USD",)
    assert infer_news_currencies(symbols["BTCUSD"]) == ("USD",)
    assert infer_news_currencies(symbols["EURUSD"]) == ("EUR", "USD")
    assert infer_news_currencies(symbols["USDJPY"]) == ("USD", "JPY")
    assert infer_news_currencies(replace(symbols["US30"], currency_base="XYZ", currency_profit="ABC")) == ()


def test_spread_cap_follows_the_instrument_with_the_global_floor():
    assert suggest_spread_limit([2000, 2100, 1900, 0], None, floor=35) == 4000  # BTC-like
    assert suggest_spread_limit([8, 9, 10], None, floor=35) == 35  # tight FX keeps the floor
    assert suggest_spread_limit([], D("25"), floor=35, multiplier=D("2")) == 50  # tick fallback
    assert suggest_spread_limit([], None, floor=35) == 35
    assert suggest_spread_limit([90000], None, floor=35) == 100000  # settings bound
    with pytest.raises(ValueError):
        suggest_spread_limit([10], None, floor=35, multiplier=D("0.5"))


def test_spread_limit_lookup_is_one_rule_everywhere():
    cfg = Settings(
        _env_file=None,
        symbols=("XAUUSD", "EURUSD"),
        symbol_aliases={"XAUUSD": "XAUUSDm"},
        symbol_spread_limits={"XAUUSD": 60, "XAUUSDm": 99, "EURUSDm": 12},
    )
    assert cfg.spread_limit_points("XAUUSD", "XAUUSDm") == 60  # logical first
    assert cfg.spread_limit_points(None, "XAUUSDm") == 99
    assert cfg.spread_limit_points("EURUSD", "EURUSD") == cfg.max_spread_points


class SuffixedMarket(MockMarketData):
    """Synthetic broker that only offers suffixed names (e.g. an Exness Cent server)."""

    def __init__(self, settings, suffix, **kwargs):
        symbols, quotes = synthetic_catalogue()
        renamed = {name + suffix: replace(info, name=name + suffix) for name, info in symbols.items()}
        prices = {name + suffix: quote for name, quote in quotes.items()}
        super().__init__(settings, symbols=renamed, quotes=prices, **kwargs)
        self._symbols, self._quotes = renamed, prices  # no bare names at all
        self._offered = tuple(renamed)

    async def get_symbols(self):
        self._ready()
        return self._offered


async def test_discovery_reads_specs_and_proposes_env_values_for_a_cent_server():
    cfg = Settings(_env_file=None, account_currency="USC", symbols=("XAUUSD", "BTCUSD", "USDJPY"))
    market = SuffixedMarket(cfg, "c", clock=ManualClock(NOW))
    await market.initialize()
    report = await discover(market, cfg)
    assert report.resolution.aliases == {"XAUUSD": "XAUUSDc", "BTCUSD": "BTCUSDc", "USDJPY": "USDJPYc"}
    gold = next(spec for spec in report.specs if spec.logical == "XAUUSD").to_dict()
    assert gold["broker_name"] == "XAUUSDc" and gold["suffix"] == "c"
    assert gold["contract_size"] == "100" and gold["tick_size"] == "0.01" and gold["volume_step"] == "0.01"
    assert gold["spread_limit_suggested"] == 40  # median 20 points x 2
    updates = report.env_updates(cfg)
    assert json.loads(updates["SYMBOL_ALIASES_JSON"])["BTCUSD"] == "BTCUSDc"
    assert json.loads(updates["SYMBOL_SPREAD_LIMITS_JSON"])["BTCUSD"] == 4000
    assert json.loads(updates["SYMBOL_NEWS_CURRENCIES_JSON"])["USDJPY"] == ["USD", "JPY"]
    assert "ACCOUNT_CURRENCY" not in updates
    summary = report.to_dict()
    assert summary["account"]["cent_account"] is True and summary["real_orders_sent"] == 0
    # The persisted values validate, and the runtime SymbolManager then enables everything.
    resolved = Settings(
        _env_file=None,
        account_currency="USC",
        symbols=cfg.symbols,
        symbol_aliases=json.loads(updates["SYMBOL_ALIASES_JSON"]),
        symbol_spread_limits=json.loads(updates["SYMBOL_SPREAD_LIMITS_JSON"]),
        symbol_news_currencies=json.loads(updates["SYMBOL_NEWS_CURRENCIES_JSON"]),
    )
    manager = SymbolManager(SuffixedMarket(resolved, "c", clock=ManualClock(NOW)), resolved)
    await manager.market.initialize()
    await manager.initialize()
    assert {item.native for item in manager.enabled()} == {"XAUUSDc", "BTCUSDc", "USDJPYc"}
    assert manager.resolve("BTCUSD").spread_limit_points == 4000 and not manager.errors()


async def test_runtime_never_silently_rebinds_but_names_the_broker_symbol():
    cfg = Settings(_env_file=None, symbols=("XAUUSD",))
    manager = SymbolManager(SuffixedMarket(cfg, "m", clock=ManualClock(NOW)), cfg)
    await manager.market.initialize()
    await manager.initialize()
    assert manager.enabled() == ()
    assert "XAUUSDm" in manager.errors()["XAUUSD"] and "resolve_symbols" in manager.errors()["XAUUSD"]


async def test_non_usd_parent_gets_a_conversion_route_from_the_same_suffix():
    cfg = Settings(_env_file=None, account_currency="EUC", symbols=("XAUUSD",))
    market = SuffixedMarket(cfg, "c", clock=ManualClock(NOW))
    await market.initialize()
    report = await discover(market, cfg)
    assert report.conversion_routes == {"EUR": "EURUSDc"}
    assert json.loads(report.env_updates(cfg)["ACCOUNT_TO_USD_SYMBOLS_JSON"]) == {"EUR": "EURUSDc"}


def test_env_update_touches_only_given_keys_keeps_mode_and_never_backs_up_secrets(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# owner comment\nMT5_PASSWORD=TEST_SECRET\nSYMBOL_ALIASES_JSON={}\nSYMBOL_ALIASES_JSON={}\nKEEP=1",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    changed = update_env_file(
        path, {"SYMBOL_ALIASES_JSON": '{"XAUUSD":"XAUUSDm"}', "ACCOUNT_CURRENCY": "USC"}
    )
    assert changed == ["SYMBOL_ALIASES_JSON", "ACCOUNT_CURRENCY"]
    text = path.read_text(encoding="utf-8")
    assert text.startswith('# owner comment\nMT5_PASSWORD=TEST_SECRET\nSYMBOL_ALIASES_JSON={"XAUUSD"')
    assert text.count("SYMBOL_ALIASES_JSON") == 1 and "KEEP=1\n" in text and "ACCOUNT_CURRENCY=USC\n" in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]
    before = path.stat().st_mtime_ns
    assert update_env_file(path, {"ACCOUNT_CURRENCY": "USC"}) == [] and path.stat().st_mtime_ns == before
    with pytest.raises(ValueError):
        update_env_file(path, {"BAD": "x\nINJECTED=1"})


def _env(tmp_path, *lines):
    template = (cli.Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    path = tmp_path / ".env"
    path.write_text(template + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_cli_reports_then_writes_idempotently_on_the_mock_backend(tmp_path, capsys):
    path = _env(tmp_path, "SYMBOLS=XAUUSD,BTCUSD,USDJPY")
    original = path.read_text(encoding="utf-8")
    assert cli.main(["--env-file", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["written_keys"] == [] and path.read_text(encoding="utf-8") == original
    assert report["real_orders_sent"] == 0 and not report["disabled_symbols"]
    assert cli.main(["--env-file", str(path), "--write"]) == 0
    written = json.loads(capsys.readouterr().out)["written_keys"]
    assert "SYMBOL_SPREAD_LIMITS_JSON" in written and "SYMBOL_NEWS_CURRENCIES_JSON" in written
    loaded = Settings(_env_file=path, project_root=tmp_path)
    assert loaded.symbol_spread_limits["BTCUSD"] == 4000
    assert tuple(loaded.symbol_news_currencies["USDJPY"]) == ("USD", "JPY")
    assert cli.main(["--env-file", str(path), "--write"]) == 0
    assert json.loads(capsys.readouterr().out)["written_keys"] == []


def test_cli_exit_code_flags_disabled_symbols_and_bad_arguments(tmp_path, capsys):
    path = _env(tmp_path, "SYMBOLS=XAUUSD,NOSUCH")
    assert cli.main(["--env-file", str(path)]) == 1
    assert "NOSUCH" in json.loads(capsys.readouterr().out)["disabled_symbols"]
    assert cli.main(["--env-file", str(tmp_path / "missing.env")]) == 2
    with pytest.raises(SystemExit):
        cli.main(["--env-file", str(path), "--spread-multiplier", "0.5"])


async def test_native_discovery_reads_market_watch_and_proposes_the_cent_currency(monkeypatch):
    clock = ManualClock(NOW)
    sdk = FakeSDK(clock)
    sdk.metadata.name = "XAUUSDc"
    sdk.account.currency = "USC"
    original_get = sdk.symbols_get

    def symbols_get():
        shown = original_get()[0]  # in Market Watch
        return (shown, SimpleNamespace(**{**vars(shown), "name": "XAUUSD.pro", "visible": False}))

    sdk.symbols_get = symbols_get
    monkeypatch.setattr(mt5_module, "MT5Client", partial(MT5Client, sdk=sdk, clock=clock))
    cfg = Settings(_env_file=None, mt5_backend="real", symbols=("XAUUSD",))  # declares USD
    report = await cli.run(cfg, suffix=None, multiplier=D("2"))
    assert report.account_currency == "USC" and report.declared_currency == "USD"
    assert report.resolution.natives == {"XAUUSD": "XAUUSDc"}
    assert report.resolution.methods["XAUUSD"] == "market_watch"
    updates = report.env_updates(cfg)
    assert updates["ACCOUNT_CURRENCY"] == "USC"
    assert json.loads(updates["SYMBOL_ALIASES_JSON"]) == {"XAUUSD": "XAUUSDc"}
    assert not sdk.requests and not any(name in {"order_send", "order_check"} for name, _ in sdk.calls)
