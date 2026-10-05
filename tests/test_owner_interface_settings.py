"""Conservative owner/auth/HTTP bounds and random webhook secret requirements."""

import pytest
from pydantic import ValidationError

from tests.risk_helpers import config


@pytest.mark.parametrize(
    "changes",
    [
        {"telegram_auth_future_skew_seconds": 16},
        {"telegram_auth_future_skew_seconds": -1},
        {"owner_confirmation_ttl_seconds": 61},
        {"owner_confirmation_ttl_seconds": 9},
        {"owner_max_pending_confirmations": 129},
        {"telegram_command_max_age_seconds": 121},
        {"telegram_initdata_max_bytes": 16385},
        {"api_safe_stop_rate_per_minute": 31},
        {"api_max_rate_limit_keys": 10001},
        {"api_max_header_bytes": 8192},
    ],
)
def test_bounded_owner_security_settings(tmp_path, changes):
    with pytest.raises(ValidationError):
        config(tmp_path, **changes)


@pytest.mark.parametrize(
    "hosts",
    [
        (),
        ("*",),
        ("owner.example/secret",),
        ("owner.example:443",),
        ("localhost", "localhost"),
        ("a..example",),
        ("-evil.example",),
    ],
)
def test_explicit_hosts_no_unrestricted_star_url_port_or_duplicates(tmp_path, hosts):
    with pytest.raises(ValidationError):
        config(tmp_path, api_trusted_hosts=hosts)


@pytest.mark.parametrize(
    "origin",
    [
        "https://owner.example/path",
        "https://owner.example?token=secret",
        "https://owner.example#fragment",
        "http://owner.example",
    ],
)
def test_cors_is_exact_https_origin(tmp_path, origin):
    with pytest.raises(ValidationError):
        config(tmp_path, api_cors_origins=(origin,))


@pytest.mark.parametrize("secret", ["", "short", "a" * 31, "a" * 257, "a" * 32 + "!", "a" * 32 + " "])
def test_webhook_secret_nontrivial_bounded_and_allowed_characters(tmp_path, secret):
    with pytest.raises(ValidationError):
        config(
            tmp_path,
            telegram_use_webhook=True,
            telegram_webhook_url="https://owner.example/telegram/webhook",
            telegram_webhook_secret=secret,
        )


@pytest.mark.parametrize("name", ["telegram_miniapp_url", "telegram_webhook_url"])
def test_telegram_no_credential_query_or_fragment_in_config_url(tmp_path, name):
    with pytest.raises(ValidationError):
        config(tmp_path, **{name: "https://owner.example?token=secret"})


def test_safe_auth_http_defaults_and_no_live_owner_override(tmp_path):
    cfg = config(tmp_path)
    assert cfg.telegram_initdata_max_age_seconds == 300 and cfg.owner_confirmation_ttl_seconds == 45
    assert cfg.telegram_auth_future_skew_seconds == 5 and cfg.api_host == "127.0.0.1"
    assert cfg.api_cors_origins == () and not cfg.telegram_use_webhook
    assert cfg.demo_mode and cfg.paper_trading and not cfg.live_trading
