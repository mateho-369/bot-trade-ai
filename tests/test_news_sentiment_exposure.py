"""Sentiment advisory only, conservative impact and logical currency/asset exposure."""

from datetime import timedelta

import pytest

from core.settings import Settings
from news.exposure import ExposureMapper
from news.sentiment_analyzer import SentimentAnalyzer
from news.types import Headline
from scripts.synthetic_signal_market import ANCHOR


@pytest.mark.parametrize(
    "text,impact",
    [
        ("USD FOMC rate decision announced", "high"),
        ("Non-farm payrolls release", "high"),
        ("Exchange hack causes withdrawals suspended", "high"),
        ("War fears despite strong rally", "high"),
        ("Euro inflation discussion", "medium"),
        ("Gold ETF regulatory review", "medium"),
        ("Market commentary: Euro and dollar unchanged", "low"),
    ],
)
def test_rule_impact_is_not_sentiment_confidence(text, impact):
    assert SentimentAnalyzer().analyze(text).impact == impact


def test_negation_and_unsupported_language_are_not_fabricated_certainty():
    a = SentimentAnalyzer()
    assert a.analyze("Bitcoin bullish rally gains").score > 0
    assert a.analyze("Bitcoin crash losses bearish").score < 0
    assert a.analyze("Not bullish").score < 0
    unknown = a.analyze("金融市場と中央銀行")
    assert not unknown.language_supported and unknown.impact == "unknown" and unknown.score == 0
    assert a.analyze("Market stable", language="unsupported").impact == "unknown"
    assert a.analyze("FOMC strong bullish rally").impact == "high"


def item(text, *, instruments=(), currencies=()):
    return Headline(
        "test-feed",
        "TEST",
        text,
        "",
        "https://test.example/article",
        ANCHOR - timedelta(minutes=1),
        ANCHOR,
        currencies,
        instruments,
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FOMC USD decision", {"EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD", "US30", "NAS100"}),
        ("ECB Euro decision", {"EURUSD"}),
        ("BoE sterling decision", {"GBPUSD"}),
        ("Bitcoin market news", {"BTCUSD"}),
        ("Ethereum upgrade", {"ETHUSD"}),
        ("Gold ETF", {"XAUUSD"}),
        ("Nasdaq corporate review", {"NAS100"}),
        ("Missile invasion fears", {"EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD", "US30", "NAS100"}),
    ],
)
def test_explicit_currency_and_asset_mapping(text, expected):
    cfg = Settings(
        _env_file=None, symbols=("EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD", "US30", "NAS100")
    )
    assert set(ExposureMapper(cfg).affected(item(text), unknown_high=True)) == expected


def test_unknown_high_currency_risk_is_scoped_conservatively_and_alias_not_guessed():
    cfg = Settings(_env_file=None, symbols=("EURUSD", "GBPUSD"), symbol_aliases={"EURUSD": "EURUSD.a"})
    mapper = ExposureMapper(cfg)
    assert mapper.affected(
        item("Emergency rate decision"), source_symbols=("GBPUSD",), unknown_high=True
    ) == ("GBPUSD",)
    assert mapper.calendar_symbols("EUR") == ("EURUSD",)
    assert mapper.affected(item("Instrument update", instruments=("BTC",))) == ()
    assert mapper.affected(item("Plain report", currencies=("GBP",))) == ("GBPUSD",)
