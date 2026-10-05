"""Fail-closed news policy/config, public endpoint and immutable scope validation."""

import json

import pytest
from pydantic import ValidationError

from core.settings import Settings
from news.evidence import configured_source_ids
from news.types import rss_source_id
from scripts.synthetic_news_fixtures import RSS_URL, news_fixture_settings


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/rss",
        "https://user:pw@example.com/rss",
        "https://example.com/rss#fragment",
        "https://localhost/rss",
        "https://127.0.0.1/rss",
        "https://169.254.169.254/latest",
        "https://[::1]/rss",
        "https://example.local/rss",
        "https://example.com:0/rss",
        "https://example.com/rss?auth_token=FAKE_SECRET",
        "https://example.com/rss?apiKey=FAKE_SECRET",
        "https://example.com/\\rss",
        "https://exam ple.com/rss",
    ],
)
def test_public_https_no_credential_or_private_literal_origins(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rss_urls=(url,))


@pytest.mark.parametrize(
    "field,value",
    [
        ("news_http_max_bytes", True),
        ("news_max_pages", True),
        ("news_request_spacing_seconds", False),
        ("news_refresh_timeout_seconds", 100),
        ("news_max_concurrent", 10),
        ("news_max_items_per_source", 1001),
        ("news_snapshot_max_files", 99),
        ("calendar_lookahead_hours", 169),
        ("newsapi_query", "x\nsecret"),
        ("cryptopanic_currencies", ("btc",)),
        ("finnhub_news_categories", ("forex", "forex")),
        ("finnhub_calendar_timezone", "Not/A/Zone"),
        ("news_required_source_ids", ("invalid/path",)),
    ],
)
def test_bounded_operational_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed": 1},
        {"realtime": "true"},
        {"poll_seconds": True},
        {"poll_seconds": 29},
        {"currencies": ("usd",)},
        {"currencies": ("USD", "USD")},
        {"symbols": ("DISABLED",)},
        {"unknown": True},
        {"symbols": ()},
    ],
)
def test_explicit_source_review_is_strict_not_coerced(change):
    data = news_fixture_settings()
    data["news_source_coverage"][rss_source_id(RSS_URL)].update(change)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, symbols=("EURUSD", "GBPUSD"), **data)


def test_duplicate_json_keys_and_policies_are_not_mutable():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, news_source_coverage='{"aa":{},"aa":{}}')
    cfg = Settings(_env_file=None, symbols=("EURUSD", "GBPUSD"), **news_fixture_settings())
    with pytest.raises(TypeError):
        cfg.news_source_coverage["new"] = {}
    with pytest.raises(ValidationError):
        cfg.news_source_coverage[rss_source_id(RSS_URL)].reviewed = False


def test_defaults_do_not_license_or_enable_api_providers():
    cfg = Settings(
        _env_file=None,
        news_api_key="FAKE_TEST_KEY",
        finnhub_api_key="FAKE_TEST_KEY",
        cryptopanic_api_key="FAKE_TEST_KEY",
    )
    assert not configured_source_ids(cfg) and not cfg.news_source_coverage
    assert cfg.newsapi_access_mode == "development" and not cfg.calendar_file_reviewed
    assert cfg.demo_mode and cfg.paper_trading and not cfg.live_trading
    assert "FAKE_TEST_KEY" not in json.dumps(cfg.public_config())


def test_news_scope_config_is_in_fingerprints():
    data = news_fixture_settings()
    cfg = Settings(_env_file=None, symbols=("EURUSD", "GBPUSD"), **data)
    data["news_source_coverage"][rss_source_id(RSS_URL)]["max_publication_lag_seconds"] = 900
    changed = Settings(_env_file=None, symbols=("EURUSD", "GBPUSD"), **data)
    assert changed.safety_fingerprint() != cfg.safety_fingerprint()
    assert changed.strategy_fingerprint() != cfg.strategy_fingerprint()
