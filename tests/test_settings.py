from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.settings import OperatingMode, Settings


def config(**values):
    return Settings(_env_file=None, **values)


def test_safe_defaults():
    settings = config()
    assert settings.mode == OperatingMode.PAPER
    assert settings.mt5_backend == "mock"
    assert settings.demo_mode and settings.start_paused and settings.require_stage_gates
    assert not settings.live_trading
    assert settings.live_max_risk_percent_per_trade == Decimal("0.1")


def test_complete_env_example_is_valid():
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env.example")
    assert settings.mode == OperatingMode.PAPER
    assert settings.symbols == ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    assert settings.trailing_levels == ((30, 30), (60, 60), (90, 90))


@pytest.mark.parametrize(
    "flags",
    [
        {"live_trading": True},
        {"demo_mode": False},
        {"backtest_mode": True},
        {"paper_trading": False},
        {"start_paused": False},
        {"require_stage_gates": False},
        {"require_stop_loss": False},
    ],
)
def test_ambiguous_or_unsafe_flags_are_rejected(flags):
    with pytest.raises(ValidationError):
        config(**flags)


def test_explicit_modes_do_not_imply_authorization():
    assert config(backtest_mode=True, paper_trading=False).mode == OperatingMode.BACKTEST
    assert config(paper_trading=False, mt5_backend="real").mode == OperatingMode.DEMO
    live = config(
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
    )
    assert live.mode == OperatingMode.LIVE
    assert live.public_config()["startup"] == "paused"


@pytest.mark.parametrize(
    "values",
    [
        {"max_risk_percent_per_trade": "1.01"},
        {"live_max_risk_percent_per_trade": "0.6"},
        {"min_daily_trades_target": 13},
        {"max_same_symbol_positions": 2},
        {"model_embargo_bars": 11},
        {"max_daily_loss_percent": "11"},
        {"target_r_multiple": "1"},
        {"trailing_levels": "60:60,30:30"},
        {"trailing_levels": "30:40"},
        {"symbols": "XAUUSD,XAUUSD"},
        {"symbols": "../secret"},
        {"telegram_bot_token": "token-without-owner"},
        {"log_file": "../outside.log"},
        {"ollama_base_url": "http://remote.example"},
        {"openai_base_url": "http://remote.example/v1"},
        {"API_TRUSTED_HOSTS_JSON": ["*"]},
    ],
)
def test_risk_and_security_configuration_rejected(values):
    with pytest.raises(ValidationError):
        config(**values)


def test_config_is_frozen():
    settings = config()
    with pytest.raises(ValidationError):
        settings.live_trading = True


def test_secrets_excluded_from_public_config_and_hash():
    first = config(telegram_bot_token="123:TOP-SECRET-A", telegram_owner_id=123)
    second = config(telegram_bot_token="123:TOP-SECRET-B", telegram_owner_id=123)
    assert "TOP-SECRET" not in str(first.public_config())
    assert first.safety_fingerprint() == second.safety_fingerprint()
    assert first.safety_fingerprint() != config(max_daily_trades=11).safety_fingerprint()


def test_unknown_dotenv_key_is_rejected(tmp_path):
    path = tmp_path / ".env"
    path.write_text("LIVE_TRDAING=true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=path)


def test_nested_config_is_read_only():
    settings = config(symbol_aliases={"XAUUSD": "XAUUSDm"})
    with pytest.raises(TypeError):
        settings.symbol_aliases["XAUUSD"] = "other"
    with pytest.raises(TypeError):
        settings.symbol_news_currencies["XAUUSD"] = ("EUR",)
    assert isinstance(settings.symbol_news_currencies["XAUUSD"], tuple)
    assert isinstance(settings.rss_urls, tuple)


def test_stage_fingerprint_spans_modes_but_approval_fingerprint_does_not():
    paper = config(telegram_bot_token="123:test-token", telegram_owner_id=123)
    live = config(
        telegram_bot_token="123:test-token",
        telegram_owner_id=123,
        live_trading=True,
        demo_mode=False,
        paper_trading=False,
        mt5_backend="real",
    )
    assert paper.strategy_fingerprint() == live.strategy_fingerprint()
    assert paper.safety_fingerprint() != live.safety_fingerprint()
    assert live.effective_risk_percent == Decimal("0.1")


def test_lower_r_targets_require_explicit_lower_reward_risk_floor():
    with pytest.raises(ValidationError):
        config(target_r_multiple="0.35")
    lab = config(target_r_multiple="0.35", min_net_reward_risk="0.3")
    assert lab.target_r_multiple == Decimal("0.35")


def test_alias_collision_rejected():
    with pytest.raises(ValidationError):
        config(symbols="XAUUSD,GOLD", symbol_aliases={"GOLD": "XAUUSD"})


def test_literal_broker_alias_with_spaces_supported():
    settings = config(symbols="V75", symbol_aliases={"V75": "Volatility 75 Index"})
    assert settings.symbol_aliases["V75"] == "Volatility 75 Index"
    with pytest.raises(ValidationError):
        config(symbols="V75", symbol_aliases={"V75": "Volatility\n75"})
