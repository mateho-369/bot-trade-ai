"""Immutable, validated configuration. Environment flags never authorize orders."""

from __future__ import annotations

import ipaddress
import json
import re
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.security import SENSITIVE_KEY, sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
Provider = Literal["ollama", "openai", "disabled"]


class FrozenDict(dict):
    """Read-only configuration maps; values are also immutable scalars/tuples."""

    def _deny(self, *args: object, **kwargs: object) -> None:
        raise TypeError("configuration maps are read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny


class OperatingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


def _valid_url(value: str, *, allow_local_http: bool = False, https_only: bool = False) -> str:
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme not in allowed or not parsed.hostname:
        raise ValueError("a valid HTTP(S) URL is required")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL credentials and fragments are forbidden")
    if allow_local_http and parsed.scheme == "http" and not local:
        raise ValueError("non-loopback AI endpoints must use HTTPS")
    return value.rstrip("/")


def valid_news_url(value: str) -> str:
    value = _valid_url(value, https_only=True)
    parsed = urlparse(value)
    if (
        len(value) > 2048
        or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
        or parsed.params
        or parsed.port is not None
        and not 1 <= parsed.port <= 65535
    ):
        raise ValueError("invalid news URL")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "::1"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("public HTTPS news origins required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("private news IP origins forbidden")
    if any(SENSITIVE_KEY.search(k) or k.lower() in {"key", "auth"} for k, _ in parse_qsl(parsed.query)):
        raise ValueError("news URL credentials belong only in explicit SecretStr settings")
    return value


class NewsSourceCoverage(BaseModel):
    """Owner-reviewed feed scope, not independent source/entitlement attestation."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    symbols: tuple[str, ...] = ()
    currencies: tuple[str, ...] = ()
    reviewed: StrictBool = False
    realtime: StrictBool = False
    language: Literal["en", "unsupported"] = "en"
    poll_seconds: int = Field(default=180, ge=30, le=86400)
    max_publication_lag_seconds: int = Field(default=3600, ge=60, le=86400)

    @field_validator("poll_seconds", "max_publication_lag_seconds", mode="before")
    @classmethod
    def bounded_integer(cls, value):
        if type(value) is not int:
            raise ValueError("actual integer feed interval required")
        return value

    @model_validator(mode="after")
    def scopes(self):
        if (
            len(self.symbols) > 30
            or len(set(self.symbols)) != len(self.symbols)
            or len(self.currencies) > 30
            or len(set(self.currencies)) != len(self.currencies)
            or any(not re.fullmatch(r"[A-Z]{3}", c) for c in self.currencies)
        ):
            raise ValueError("bounded unique currency/symbol coverage required")
        if self.reviewed and (not self.symbols or not self.currencies):
            raise ValueError("reviewed feeds need explicit scopes")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
        populate_by_name=True,
    )

    # Trusted local/test override; defaults to the checked-out repository.
    project_root: Path = Field(default=PROJECT_ROOT, exclude=True)
    backtest_mode: bool = False
    demo_mode: bool = True
    live_trading: bool = False
    paper_trading: bool = True
    start_paused: bool = True
    mt5_backend: Literal["mock", "real"] = "mock"
    mt5_terminal_path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"
    mt5_login: int | None = Field(default=None, gt=0)
    mt5_password: SecretStr = SecretStr("")
    mt5_server: str = ""
    mt5_magic_number: int = Field(default=20260217, gt=0, le=2**31 - 1)
    mt5_connect_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    mt5_reconnect_attempts: int = Field(default=3, ge=1, le=5)
    mt5_reconnect_backoff_seconds: int = Field(default=5, ge=1, le=60)
    max_tick_age_seconds: int = Field(default=10, ge=1, le=60)
    max_candle_age_seconds: int = Field(default=420, ge=60, le=86400)
    mt5_api_timeout_seconds: float = Field(default=45, gt=0, le=90)
    order_max_age_seconds: int = Field(default=30, ge=1, le=60)
    paper_initial_balance: Decimal = Field(default=Decimal("1000"), gt=0, le=Decimal("1000000000"))
    paper_slippage_points: int = Field(default=2, ge=0, le=1000)
    mock_leverage: int = Field(default=100, ge=1, le=1000)
    paper_state_file: Path = Path("data/paper/state.json")
    runtime_lease_seconds: int = Field(default=90, ge=30, le=300)
    risk_observation_max_age_seconds: int = Field(default=30, ge=5, le=120)

    account_currency: str = "USD"
    max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    live_max_risk_percent_per_trade: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    max_total_open_risk_percent: Decimal = Field(default=Decimal("1.5"), gt=0, le=5)
    max_daily_loss_percent: Decimal = Field(default=Decimal("3"), gt=0, le=10)
    max_drawdown_percent: Decimal = Field(default=Decimal("10"), gt=0, le=25)
    max_margin_usage_percent: Decimal = Field(default=Decimal("30"), gt=0, le=50)
    max_daily_trades: int = Field(default=12, ge=1, le=50)
    min_daily_trades_target: int = Field(default=6, ge=0, le=50)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    max_same_symbol_positions: int = Field(default=1, ge=1, le=1)
    max_spread_points: int = Field(default=35, ge=0, le=100000)
    symbol_spread_limits: dict[str, int] = Field(
        default_factory=dict, validation_alias="SYMBOL_SPREAD_LIMITS_JSON"
    )
    max_slippage_points: int = Field(default=10, ge=0, le=1000)
    min_signal_score: float = Field(default=70, ge=50, le=100)
    require_stop_loss: bool = True
    min_net_reward_risk: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    trading_day_timezone: str = "UTC"

    target_profit_usd_per_trade: Decimal = Field(default=Decimal("5"), gt=0, le=10000)
    use_dynamic_target: bool = True
    target_r_multiple: Decimal = Field(default=Decimal("1.1"), ge=Decimal("0.1"), le=5)
    commission_round_turn_usd_per_lot: Decimal = Field(default=Decimal("7"), ge=0, le=10000)
    estimated_swap_usd_per_lot_per_day: Decimal = Field(default=Decimal("0"), ge=0, le=10000)
    symbols: Annotated[tuple[str, ...], NoDecode] = ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD")
    symbol_aliases: dict[str, str] = Field(default_factory=dict, validation_alias="SYMBOL_ALIASES_JSON")
    symbol_news_currencies: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "XAUUSD": ["USD"],
            "BTCUSD": ["USD"],
            "ETHUSD": ["USD"],
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "US30": ["USD"],
            "NAS100": ["USD"],
        },
        validation_alias="SYMBOL_NEWS_CURRENCIES_JSON",
    )
    account_to_usd_symbols: dict[str, str] = Field(
        default_factory=dict, validation_alias="ACCOUNT_TO_USD_SYMBOLS_JSON"
    )
    primary_timeframe: Timeframe = "M5"
    higher_timeframe: Timeframe = "M15"
    trend_timeframe: Timeframe = "H1"
    candle_lookback: int = Field(default=300, ge=200, le=5000)

    # Rule scores are heuristics, not calibrated probabilities. No forced quota.
    strategy_weights: Annotated[dict[str, Decimal], NoDecode] = Field(
        default_factory=lambda: {
            "trend": Decimal("0.30"),
            "mean_reversion": Decimal("0.25"),
            "breakout": Decimal("0.20"),
            "momentum": Decimal("0.25"),
        },
        validation_alias="STRATEGY_WEIGHTS_JSON",
    )
    strategy_min_agreeing: int = Field(default=2, ge=1, le=4)
    strategy_min_coverage: Decimal = Field(default=Decimal("0.50"), ge=Decimal("0.35"), le=1)
    strategy_min_agreement: Decimal = Field(default=Decimal("0.80"), ge=Decimal("0.75"), le=1)
    strategy_min_vote_score: float = Field(default=60, ge=50, le=100)
    strategy_stop_atr_multiplier: Decimal = Field(default=Decimal("1.8"), ge=1, le=5)
    strategy_max_entry_drift_atr: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    strategy_max_recent_gap_bars: int = Field(default=3, ge=1, le=4)
    volatility_min_atr_percent: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0.001"), le=1)
    volatility_max_atr_percent: Decimal = Field(default=Decimal("2"), ge=Decimal("0.05"), le=10)
    volatility_max_atr_shock: Decimal = Field(default=Decimal("2"), ge=Decimal("1.2"), le=3)
    volatility_max_bar_atr_ratio: Decimal = Field(default=Decimal("3"), ge=Decimal("1.5"), le=4)
    volatility_max_spread_atr_ratio: Decimal = Field(default=Decimal("0.15"), gt=0, le=Decimal("0.5"))

    trailing_levels: Annotated[tuple[tuple[float, float], ...], NoDecode] = ((30, 30), (60, 60), (90, 90))
    trailing_spread_buffer_points: int = Field(default=2, ge=0, le=1000)
    atr_trailing_enabled: bool = True
    atr_trailing_multiplier: Decimal = Field(default=Decimal("1.5"), ge=1, le=5)
    allow_tp_extension: bool = False
    tp_extension_factor: Decimal = Field(default=Decimal("1.2"), ge=1, le=1.5)

    telegram_bot_token: SecretStr = SecretStr("")
    telegram_owner_id: int | None = Field(default=None, gt=0)
    telegram_miniapp_url: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    telegram_initdata_max_age_seconds: int = Field(default=300, ge=30, le=600)
    telegram_initdata_max_bytes: int = Field(default=8192, ge=1024, le=16384)
    telegram_auth_future_skew_seconds: int = Field(default=5, ge=0, le=15)
    telegram_command_max_age_seconds: int = Field(default=60, ge=15, le=120)
    owner_confirmation_ttl_seconds: int = Field(default=45, ge=10, le=60)
    owner_max_pending_confirmations: int = Field(default=64, ge=4, le=128)
    telegram_use_webhook: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1024, le=65535)
    api_rate_limit_per_minute: int = Field(default=60, ge=5, le=300)
    api_max_request_bytes: int = Field(default=16384, ge=1024, le=65536)
    api_trusted_hosts: tuple[str, ...] = Field(
        default=("127.0.0.1", "localhost"), validation_alias="API_TRUSTED_HOSTS_JSON"
    )
    api_cors_origins: tuple[str, ...] = Field(default=(), validation_alias="API_CORS_ORIGINS_JSON")
    api_safe_stop_rate_per_minute: int = Field(default=10, ge=2, le=30)
    api_max_header_bytes: int = Field(default=16384, ge=4096, le=32768)
    api_max_rate_limit_keys: int = Field(default=2048, ge=32, le=10000)
    api_trusted_proxy_ips: tuple[str, ...] = Field(
        default=("127.0.0.1", "::1"), validation_alias="API_TRUSTED_PROXY_IPS_JSON"
    )

    @field_validator("api_trusted_proxy_ips")
    @classmethod
    def validate_proxy_ips(cls, values):
        import ipaddress

        if len(values) > 8 or len(set(values)) != len(values):
            raise ValueError("at most eight unique exact proxy IP addresses are permitted")
        try:
            normalized = tuple(str(ipaddress.ip_address(v)) for v in values)
            if len(set(normalized)) != len(normalized):
                raise ValueError("duplicate normalized proxy IP")
            return normalized
        except ValueError:
            raise ValueError("exact proxy IP addresses required; no wildcard, hostnames or CIDRs") from None

    ai_provider: Provider = "ollama"
    ai_fallback_provider: Provider = "openai"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    allow_remote_ollama: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    # json_object = provider JSON-syntax mode; json_schema_strict = provider constrained decoding
    # (Groq openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.8-27b). Local strict validation ALWAYS runs.
    openai_response_format: Literal["json_object", "json_schema_strict"] = "json_object"
    # Empty = parameter not sent. Groq reasoning models accept low/medium/high (Qwen also none/default).
    openai_reasoning_effort: Literal["", "none", "default", "low", "medium", "high"] = ""
    ai_confidence_threshold: float = Field(default=70, ge=50, le=100)
    ai_timeout_seconds: int = Field(default=12, ge=1, le=60)
    ai_max_concurrent: int = Field(default=2, ge=1, le=4)
    ai_position_review_seconds: int = Field(default=300, ge=60, le=3600)
    ai_max_response_bytes: int = Field(default=65536, ge=4096, le=262144)
    ai_max_output_tokens: int = Field(default=768, ge=128, le=2048)
    ai_circuit_failures: int = Field(default=3, ge=1, le=10)
    ai_circuit_cooldown_seconds: int = Field(default=60, ge=10, le=600)
    ai_suggestion_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    # Rule-based fallback: ONLY when the AI provider is unavailable/times out/returns invalid JSON.
    # A valid AI reject/WAIT/low confidence stays final. News/ML/risk/stage/owner gates still apply.
    ai_rule_fallback_enabled: bool = True
    ai_rule_fallback_min_score: float = Field(default=80, ge=50, le=100)
    ai_rule_fallback_allow_live: bool = False
    auto_reduce_risk: bool = False
    auto_adapt_strategy_weights: bool = False
    max_strategy_weight_step: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.02"))

    news_api_key: SecretStr = SecretStr("")
    finnhub_api_key: SecretStr = SecretStr("")
    cryptopanic_api_key: SecretStr = SecretStr("")
    use_free_news_sources: bool = True
    use_rss: bool = True
    use_economic_calendar: bool = True
    block_trading_high_impact_news: bool = True
    news_impact_threshold: Literal["medium", "high"] = "high"
    news_required_for_entry: bool = True
    rss_urls: tuple[str, ...] = Field(default=(), validation_alias="RSS_URLS_JSON")
    calendar_source_url: str = ""
    calendar_file: Path = Path("data/news/calendar.json")
    news_poll_seconds: int = Field(default=180, ge=30, le=900)
    news_max_age_seconds: int = Field(default=900, ge=60, le=3600)
    calendar_max_age_seconds: int = Field(default=21600, ge=60, le=86400)
    news_pre_event_minutes: int = Field(default=30, ge=15, le=120)
    news_post_event_minutes: int = Field(default=15, ge=5, le=120)

    newsapi_enabled: bool = False
    newsapi_access_mode: Literal["development", "production"] = "development"
    newsapi_query: str = "forex OR central bank OR inflation OR gold OR bitcoin OR ethereum"
    finnhub_news_enabled: bool = False
    finnhub_news_categories: tuple[Literal["general", "forex", "crypto"], ...] = Field(
        default=("forex", "general", "crypto"), validation_alias="FINNHUB_NEWS_CATEGORIES_JSON"
    )
    cryptopanic_enabled: bool = False
    cryptopanic_plan: Literal["developer", "growth", "enterprise"] = "developer"
    cryptopanic_currencies: tuple[str, ...] = Field(
        default=("BTC", "ETH"), validation_alias="CRYPTOPANIC_CURRENCIES_JSON"
    )
    news_source_coverage: Annotated[dict[str, NewsSourceCoverage], NoDecode] = Field(
        default_factory=dict, validation_alias="NEWS_SOURCE_COVERAGE_JSON"
    )
    news_required_source_ids: tuple[str, ...] = Field(
        default=(), validation_alias="NEWS_REQUIRED_SOURCE_IDS_JSON"
    )
    news_http_timeout_seconds: float = Field(default=8, gt=0, le=20)
    news_refresh_timeout_seconds: float = Field(default=30, gt=0, le=60)
    news_http_max_bytes: int = Field(default=1048576, ge=8192, le=2097152)
    news_max_items_per_source: int = Field(default=200, ge=10, le=1000)
    news_max_total_items: int = Field(default=500, ge=200, le=2000)
    news_max_pages: int = Field(default=3, ge=1, le=5)
    news_max_concurrent: int = Field(default=3, ge=1, le=5)
    news_request_spacing_seconds: float = Field(default=1, ge=0.2, le=60)
    news_headline_block_minutes: int = Field(default=60, ge=15, le=240)
    news_snapshot_max_bytes: int = Field(default=1048576, ge=65536, le=4194304)
    news_snapshot_max_files: int = Field(default=50000, ge=100, le=100000)
    news_alerts_per_refresh: int = Field(default=10, ge=1, le=30)
    calendar_provider: Literal["auto", "file", "json_http", "finnhub", "disabled"] = "auto"
    calendar_file_reviewed: bool = False
    calendar_allow_empty_reviewed: bool = False
    finnhub_calendar_scope_reviewed: bool = False
    finnhub_calendar_timezone: str = "UTC"
    calendar_lookback_hours: int = Field(default=24, ge=4, le=72)
    calendar_lookahead_hours: int = Field(default=48, ge=6, le=168)

    require_stage_gates: bool = True
    model_min_labelled_trades: int = Field(default=300, ge=100, le=100000)
    model_walk_forward_folds: int = Field(default=5, ge=3, le=20)
    model_label_horizon_bars: int = Field(default=12, ge=1, le=1000)
    model_embargo_bars: int = Field(default=12, ge=1, le=2000)
    model_algorithm: Literal["logistic", "lightgbm"] = "logistic"
    model_filter_enabled: bool = False
    model_min_probability: float = Field(default=0.70, ge=0.5, le=0.95)
    model_min_eval_auc: float = Field(default=0.55, ge=0.5, le=0.9)
    model_min_brier_skill: float = Field(default=0.01, ge=0, le=0.5)
    model_min_selected_test_trades: int = Field(default=30, ge=10, le=1000)
    model_max_dataset_rows: int = Field(default=10000, ge=300, le=100000)
    model_max_artifact_bytes: int = Field(default=1048576, ge=65536, le=4194304)
    model_max_age_days: int = Field(default=30, ge=1, le=365)
    model_random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    stage_min_paper_days: int = Field(default=14, ge=7, le=365)
    stage_min_demo_days: int = Field(default=14, ge=7, le=365)
    stage_min_trades: int = Field(default=100, ge=50, le=10000)
    stage_min_profit_factor: Decimal = Field(default=Decimal("1.1"), gt=1, le=5)
    stage_max_drawdown_percent: Decimal = Field(default=Decimal("5"), gt=0, le=10)
    live_approval_ttl_seconds: int = Field(default=3600, ge=60, le=86400)

    database_url: SecretStr = SecretStr("sqlite:///data/reflexbot.db")
    database_busy_timeout_ms: int = Field(default=10000, ge=1000, le=60000)
    signal_interval_seconds: int = Field(default=30, ge=5, le=300)
    position_interval_seconds: int = Field(default=5, ge=1, le=30)
    heartbeat_interval_seconds: int = Field(default=10, ge=5, le=30)
    watchdog_interval_seconds: int = Field(default=30, ge=15, le=120)
    watchdog_stale_seconds: int = Field(default=90, ge=30, le=600)
    watchdog_max_restarts_per_hour: int = Field(default=3, ge=1, le=5)
    runtime_api_enabled: bool = True
    runtime_telegram_enabled: bool = True
    runtime_register_menu: bool = False
    runtime_learning_enabled: bool = False
    runtime_learning_hour_utc: int = Field(default=2, ge=0, le=23)
    runtime_report_hour_utc: int = Field(default=0, ge=0, le=23)
    runtime_notifications_seconds: int = Field(default=30, ge=15, le=300)
    runtime_backup_enabled: bool = True
    runtime_backup_hour_utc: int = Field(default=3, ge=0, le=23)
    runtime_backup_keep: int = Field(default=7, ge=1, le=30)
    runtime_job_max_seconds: int = Field(default=120, ge=30, le=300)
    runtime_shutdown_seconds: int = Field(default=120, ge=30, le=600)
    watchdog_startup_grace_seconds: int = Field(default=240, ge=60, le=600)
    runtime_health_file: Path = Path("data/runtime/health.json")
    runtime_lock_file: Path = Path("data/runtime/runtime.lock")
    watchdog_lock_file: Path = Path("data/runtime/watchdog.lock")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Path = Path("data/logs/bot.log")
    log_max_bytes: int = Field(default=5242880, ge=1048576, le=52428800)
    log_backup_count: int = Field(default=5, ge=1, le=20)
    data_dir: Path = Path("data")
    backup_dir: Path = Path("data/backups")

    @field_validator("mt5_login", "telegram_owner_id", mode="before")
    @classmethod
    def empty_int_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = json.loads(value) if value.lstrip().startswith("[") else value.split(",")
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one symbol is required")
        symbols = tuple(str(item).strip() for item in value)
        if len(symbols) > 30 or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique; maximum 30")
        if any(not re.fullmatch(r"[A-Za-z0-9_.#-]{1,64}", item) for item in symbols):
            raise ValueError("invalid symbol name")
        return symbols

    @field_validator("strategy_weights", mode="before")
    @classmethod
    def parse_strategy_weights(cls, value: object) -> dict:
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate strategy weight key")
                result[key] = item
            return result

        if isinstance(value, str):
            value = json.loads(value, object_pairs_hook=unique)
        expected = {"trend", "mean_reversion", "breakout", "momentum"}
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or any(isinstance(item, bool) for item in value.values())
        ):
            raise ValueError("supply all four known strategy weights; booleans are forbidden")
        return value

    @field_validator("news_source_coverage", mode="before")
    @classmethod
    def coverage_json(cls, value):
        def unique(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError("duplicate news coverage key")
                result[k] = v
            return result

        if isinstance(value, str):
            value = json.loads(value, object_pairs_hook=unique)
        if not isinstance(value, dict) or len(value) > 16:
            raise ValueError("at most 16 explicit source policies")
        return value

    @field_validator(
        "news_http_timeout_seconds",
        "news_refresh_timeout_seconds",
        "news_http_max_bytes",
        "news_max_items_per_source",
        "news_max_total_items",
        "news_max_pages",
        "news_max_concurrent",
        "news_request_spacing_seconds",
        "news_headline_block_minutes",
        "news_snapshot_max_bytes",
        "news_snapshot_max_files",
        "news_alerts_per_refresh",
        "calendar_lookback_hours",
        "calendar_lookahead_hours",
        "news_poll_seconds",
        "news_max_age_seconds",
        "calendar_max_age_seconds",
        "news_pre_event_minutes",
        "news_post_event_minutes",
        mode="before",
    )
    @classmethod
    def news_numbers(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a news parameter")
        return value

    @field_validator(
        "ai_max_response_bytes",
        "ai_max_output_tokens",
        "ai_circuit_failures",
        "ai_circuit_cooldown_seconds",
        "ai_suggestion_ttl_seconds",
        "model_min_labelled_trades",
        "model_walk_forward_folds",
        "model_label_horizon_bars",
        "model_embargo_bars",
        "model_min_probability",
        "model_min_eval_auc",
        "model_min_brier_skill",
        "model_min_selected_test_trades",
        "model_max_dataset_rows",
        "model_max_artifact_bytes",
        "model_max_age_days",
        "model_random_seed",
        "strategy_min_agreeing",
        "strategy_min_coverage",
        "strategy_min_agreement",
        "strategy_min_vote_score",
        "strategy_stop_atr_multiplier",
        "strategy_max_entry_drift_atr",
        "strategy_max_recent_gap_bars",
        "volatility_min_atr_percent",
        "volatility_max_atr_percent",
        "volatility_max_atr_shock",
        "volatility_max_bar_atr_ratio",
        "volatility_max_spread_atr_ratio",
        mode="before",
    )
    @classmethod
    def no_boolean_strategy_numbers(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean is not a strategy parameter")
        return value

    @field_validator("trailing_levels", mode="before")
    @classmethod
    def parse_trailing_levels(cls, value: object) -> tuple[tuple[float, float], ...]:
        if isinstance(value, str):
            value = [item.split(":") for item in value.split(",")]
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("at least one trailing level is required")
        try:
            levels = tuple((float(trigger), float(lock)) for trigger, lock in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("use trigger:lock pairs") from exc
        if any(not 0 < lock <= trigger <= 100 for trigger, lock in levels):
            raise ValueError("each level must satisfy 0 < lock <= trigger <= 100")
        if any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(levels, levels[1:], strict=False)):
            raise ValueError("trailing triggers and locks must increase strictly")
        return levels

    @field_validator("account_currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3,8}", value):
            raise ValueError("invalid account currency code")
        return value

    @field_validator("trading_day_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def safety_invariants(self) -> Self:
        if not self.start_paused or not self.require_stop_loss or not self.require_stage_gates:
            raise ValueError("startup pause, broker SL and stage gates cannot be disabled")
        if self.ai_rule_fallback_enabled and self.ai_rule_fallback_min_score < max(
            self.ai_confidence_threshold, self.min_signal_score
        ):
            raise ValueError(
                "AI_RULE_FALLBACK_MIN_SCORE must be >= AI_CONFIDENCE_THRESHOLD and MIN_SIGNAL_SCORE"
            )
        if self.live_trading:
            if self.demo_mode or self.paper_trading or self.backtest_mode or self.mt5_backend != "real":
                raise ValueError(
                    "live requires DEMO_MODE=false, PAPER_TRADING=false, BACKTEST_MODE=false and real backend"
                )
            if not self.telegram_bot_token.get_secret_value() or not self.telegram_owner_id:
                raise ValueError("live requires owner-only Telegram controls")
            if not (
                self.news_required_for_entry
                and self.use_economic_calendar
                and self.block_trading_high_impact_news
            ):
                raise ValueError("live requires fresh news, calendar and high-impact blocking")
        elif self.backtest_mode:
            if self.paper_trading or not self.demo_mode or self.mt5_backend != "mock":
                raise ValueError("backtest requires PAPER_TRADING=false, DEMO_MODE=true and mock backend")
        elif self.paper_trading:
            if not self.demo_mode:
                raise ValueError("paper requires DEMO_MODE=true")
        elif not self.demo_mode or self.mt5_backend != "real":
            raise ValueError("broker demo requires DEMO_MODE=true and real backend")
        if self.mode in {OperatingMode.DEMO, OperatingMode.LIVE} and self.ai_provider == "disabled":
            raise ValueError("broker execution cannot bypass AI confidence gating")
        if self.live_max_risk_percent_per_trade > self.max_risk_percent_per_trade:
            raise ValueError("live risk cap cannot exceed the account risk cap")
        if self.max_risk_percent_per_trade > self.max_daily_loss_percent:
            raise ValueError("trade risk cannot exceed daily loss cap")
        if self.max_total_open_risk_percent > self.max_drawdown_percent:
            raise ValueError("open risk cannot exceed drawdown cap")
        if self.max_daily_loss_percent > self.max_drawdown_percent:
            raise ValueError("daily loss cap cannot exceed drawdown cap")
        if self.paper_slippage_points > self.max_slippage_points:
            raise ValueError("simulated slippage must fit the configured nominal slippage budget")
        if self.min_daily_trades_target > self.max_daily_trades:
            raise ValueError("trade-count target cannot exceed daily limit")
        if self.target_r_multiple < self.min_net_reward_risk:
            raise ValueError("dynamic target multiple cannot bypass the net reward/risk floor")
        if TIMEFRAME_MINUTES[self.higher_timeframe] <= TIMEFRAME_MINUTES[self.primary_timeframe]:
            raise ValueError("higher timeframe must exceed primary timeframe")
        if TIMEFRAME_MINUTES[self.trend_timeframe] < TIMEFRAME_MINUTES[self.higher_timeframe]:
            raise ValueError("trend timeframe must be at least the higher timeframe")
        if any(not item.is_finite() or not 0 <= item <= 1 for item in self.strategy_weights.values()):
            raise ValueError("strategy weights must be finite in [0,1]")
        if sum(self.strategy_weights.values()) != Decimal("1"):
            raise ValueError("strategy weights must sum exactly to 1; no silent normalization")
        if sum(item > 0 for item in self.strategy_weights.values()) < self.strategy_min_agreeing:
            raise ValueError("too few enabled strategies for required agreement")
        if self.volatility_min_atr_percent >= self.volatility_max_atr_percent:
            raise ValueError("minimum volatility must be below maximum volatility")
        if self.model_embargo_bars < self.model_label_horizon_bars:
            raise ValueError("validation embargo must cover the full label horizon")
        if self.heartbeat_interval_seconds * 3 > self.runtime_lease_seconds:
            raise ValueError("heartbeat cadence must not exceed one third of the runtime lease")
        if self.position_interval_seconds >= self.risk_observation_max_age_seconds:
            raise ValueError("position/risk observation cadence must precede risk expiry")
        if self.runtime_shutdown_seconds < self.mt5_api_timeout_seconds:
            raise ValueError("shutdown budget must cover a native API timeout without force killing")
        if self.telegram_use_webhook and not (self.runtime_api_enabled and self.runtime_telegram_enabled):
            raise ValueError("webhook needs explicitly enabled API and Telegram runtime")
        if self.watchdog_stale_seconds < 3 * self.heartbeat_interval_seconds:
            raise ValueError("watchdog staleness must allow at least three heartbeats")
        credentials = (
            bool(self.mt5_login),
            bool(self.mt5_password.get_secret_value()),
            bool(self.mt5_server),
        )
        if any(credentials) and not all(credentials):
            raise ValueError("supply all MT5 login/password/server values or leave all empty")
        if bool(self.telegram_bot_token.get_secret_value()) != bool(self.telegram_owner_id):
            raise ValueError("Telegram token and owner ID must be configured together")
        for url in (self.telegram_miniapp_url, self.telegram_webhook_url):
            if url:
                _valid_url(url, https_only=True)
        if self.telegram_use_webhook and not (
            self.telegram_bot_token.get_secret_value()
            and self.telegram_webhook_url
            and re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self.telegram_webhook_secret.get_secret_value())
        ):
            raise ValueError("webhook mode requires Telegram credentials, HTTPS and a 32+ character secret")
        if self.telegram_initdata_max_bytes + 1024 > self.api_max_header_bytes:
            raise ValueError("header bound must accommodate initData and ordinary headers")
        if (
            not self.api_trusted_hosts
            or len(self.api_trusted_hosts) > 30
            or len(set(self.api_trusted_hosts)) != len(self.api_trusted_hosts)
            or any(
                not re.fullmatch(r"(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", h)
                or ".." in h
                or h == "*"
                for h in self.api_trusted_hosts
            )
        ):
            raise ValueError("bounded explicit hostnames/IPs required; unrestricted wildcard forbidden")
        for url in (self.telegram_miniapp_url, self.telegram_webhook_url):
            parsed = urlparse(url)
            if url and (parsed.query or parsed.fragment or parsed.params):
                raise ValueError("Telegram interface URLs cannot contain queries/fragments/params")
        for origin in self.api_cors_origins:
            parsed = urlparse(origin)
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.params:
                raise ValueError("CORS needs exact HTTPS origins, not URL paths or credentials")
        if len(set(self.api_cors_origins)) != len(self.api_cors_origins) or len(self.api_cors_origins) > 8:
            raise ValueError("bounded unique CORS origins required")
        _valid_url(self.ollama_base_url)
        if not self.allow_remote_ollama and urlparse(self.ollama_base_url).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("remote Ollama is disabled")
        if self.allow_remote_ollama:
            _valid_url(self.ollama_base_url, allow_local_http=True)
        _valid_url(self.openai_base_url, allow_local_http=True)
        for url in (self.ollama_base_url, self.openai_base_url):
            parsed = urlparse(url)
            if parsed.query or parsed.params or parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("AI endpoint queries/params or invalid ports are forbidden")
        for model in (self.ollama_model, self.openai_model):
            if not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,128}", model):
                raise ValueError("invalid configured AI model identifier")
        if self.model_max_dataset_rows < self.model_min_labelled_trades:
            raise ValueError("dataset cap must accommodate the minimum labelled trades")
        for url in self.api_cors_origins:
            _valid_url(url, https_only=True)
        if len(self.rss_urls) > 12 or len(set(self.rss_urls)) != len(self.rss_urls):
            raise ValueError("at most 12 unique RSS URLs")
        for url in self.rss_urls:
            valid_news_url(url)
        if self.calendar_source_url:
            valid_news_url(self.calendar_source_url)
        if self.calendar_provider == "json_http" and not self.calendar_source_url:
            raise ValueError("explicit calendar JSON HTTPS URL required")
        if self.news_max_total_items < self.news_max_items_per_source:
            raise ValueError("news aggregate cap must fit a source")
        if not 1 <= len(self.newsapi_query) <= 300 or any(
            ord(c) < 32 or ord(c) == 127 for c in self.newsapi_query
        ):
            raise ValueError("bounded literal news query required")
        if (
            not self.finnhub_news_categories
            or len(set(self.finnhub_news_categories)) != len(self.finnhub_news_categories)
            or not self.cryptopanic_currencies
            or len(self.cryptopanic_currencies) > 30
            or len(set(self.cryptopanic_currencies)) != len(self.cryptopanic_currencies)
            or any(not re.fullmatch(r"[A-Z0-9]{2,10}", c) for c in self.cryptopanic_currencies)
        ):
            raise ValueError("unique bounded provider categories/tickers required")
        source_ids = tuple(self.news_source_coverage) + self.news_required_source_ids
        if any(not re.fullmatch(r"[a-z][a-z0-9_.:-]{1,63}", name) for name in source_ids):
            raise ValueError("invalid configured news source identifier")
        if len(self.news_required_source_ids) > 16 or len(set(self.news_required_source_ids)) != len(
            self.news_required_source_ids
        ):
            raise ValueError("bounded unique required source IDs")
        for coverage in self.news_source_coverage.values():
            if any(name not in self.symbols for name in coverage.symbols):
                raise ValueError("feed coverage needs enabled logical symbols")
        try:
            ZoneInfo(self.finnhub_calendar_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("known Finnhub calendar timezone required") from None
        if not self.api_trusted_hosts or "*" in self.api_trusted_hosts:
            raise ValueError("explicit API trusted hosts are required; wildcard is forbidden")
        for symbol, limit in self.symbol_spread_limits.items():
            if not symbol or not 0 <= limit <= 100000:
                raise ValueError("invalid per-symbol spread limit")
        for name, alias in self.symbol_aliases.items():
            # Native broker names may contain spaces/punctuation. Logical names
            # remain filename-safe; aliases are passed only as literal API values.
            if (
                name not in self.symbols
                or not alias.strip()
                or len(alias) > 64
                or any(ord(char) < 32 or ord(char) == 127 for char in alias)
            ):
                raise ValueError("aliases require an enabled logical symbol and valid broker name")
        resolved = tuple(self.symbol_aliases.get(symbol, symbol) for symbol in self.symbols)
        if len(set(resolved)) != len(resolved):
            raise ValueError("two logical symbols cannot resolve to the same broker symbol")
        for currencies in self.symbol_news_currencies.values():
            if not currencies or any(not re.fullmatch(r"[A-Z]{3}", item) for item in currencies):
                raise ValueError("news exposure requires ISO currency codes")
        for path in (
            self.data_dir,
            self.backup_dir,
            self.runtime_health_file,
            self.runtime_lock_file,
            self.watchdog_lock_file,
            self.log_file,
            self.calendar_file,
            self.paper_state_file,
        ):
            self.resolve_path(path)
        runtime_paths = tuple(
            self.resolve_path(p)
            for p in (self.runtime_health_file, self.runtime_lock_file, self.watchdog_lock_file)
        )
        runtime_root = self.resolve_path(self.data_dir) / "runtime"
        if (
            len(set(runtime_paths)) != 3
            or any(p.parent != runtime_root for p in runtime_paths)
            or self.runtime_health_file.suffix != ".json"
            or self.runtime_lock_file.suffix != ".lock"
            or self.watchdog_lock_file.suffix != ".lock"
            or self.runtime_health_file.name == "operator-stop.json"
            or self.runtime_health_file.name.startswith("stop-")
        ):
            raise ValueError("distinct health JSON and OS lock files must reside in DATA_DIR/runtime")
        for name in (
            "symbol_spread_limits",
            "symbol_aliases",
            "symbol_news_currencies",
            "account_to_usd_symbols",
            "strategy_weights",
            "news_source_coverage",
        ):
            object.__setattr__(self, name, FrozenDict(getattr(self, name)))
        return self

    @property
    def mode(self) -> OperatingMode:
        if self.live_trading:
            return OperatingMode.LIVE
        if self.backtest_mode:
            return OperatingMode.BACKTEST
        if self.paper_trading:
            return OperatingMode.PAPER
        return OperatingMode.DEMO

    @property
    def effective_risk_percent(self) -> Decimal:
        return (
            min(self.max_risk_percent_per_trade, self.live_max_risk_percent_per_trade)
            if self.mode == OperatingMode.LIVE
            else self.max_risk_percent_per_trade
        )

    def resolve_path(self, path: str | Path) -> Path:
        root = self.project_root.resolve()
        candidate = (root / Path(path)).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("runtime paths must remain inside the project directory")
        return candidate

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.backup_dir, self.log_file.parent, self.calendar_file.parent):
            self.resolve_path(path).mkdir(parents=True, exist_ok=True)
        for child in ("candles", "news", "models", "logs", "backups", "paper"):
            (self.resolve_path(self.data_dir) / child).mkdir(parents=True, exist_ok=True)

    def safety_snapshot(self) -> dict[str, object]:
        # Include all strategy/risk/provider settings, exclude credentials and
        # infrastructure-only fields. Effective runtime overrides must be hashed too.
        infrastructure = {
            "project_root",
            "database_url",
            "data_dir",
            "backup_dir",
            "log_file",
            "log_level",
            "log_max_bytes",
            "log_backup_count",
            "api_host",
            "api_port",
            "api_trusted_hosts",
            "api_trusted_proxy_ips",
            "api_cors_origins",
            "telegram_miniapp_url",
            "telegram_webhook_url",
            "telegram_use_webhook",
            "mt5_terminal_path",
            "database_busy_timeout_ms",
            "runtime_health_file",
            "runtime_lock_file",
            "watchdog_lock_file",
        }
        values = self.model_dump(mode="json")
        excluded = infrastructure | {
            name for name in type(self).model_fields if isinstance(getattr(self, name), SecretStr)
        }
        return {
            **{key: value for key, value in values.items() if key not in excluded},
            "mode": self.mode.value,
        }

    def safety_fingerprint(self) -> str:
        return sha256_json(self.safety_snapshot())

    def spread_limit_points(self, logical: str | None, native: str | None = None) -> int:
        """Per-symbol spread cap: logical override, then native-name override, then global."""
        for key in (logical, native):
            if key is not None and key in self.symbol_spread_limits:
                return self.symbol_spread_limits[key]
        return self.max_spread_points

    def strategy_fingerprint(self) -> str:
        # Stage evidence can span paper/demo/live and different account logins.
        # Owner execution approvals use safety_fingerprint PLUS actual account/session.
        snapshot = self.safety_snapshot()
        for name in (
            "mode",
            "backtest_mode",
            "demo_mode",
            "live_trading",
            "paper_trading",
            "mt5_backend",
            "mt5_login",
            "mt5_server",
            "telegram_owner_id",
        ):
            snapshot.pop(name, None)
        return sha256_json(snapshot)

    def public_config(self) -> dict[str, object]:
        # Strict allowlist. Never expose model_dump() to an API or to logs.
        return {
            "mode": self.mode.value,
            "backend": self.mt5_backend,
            "startup": "paused",
            "symbols": list(self.symbols),
            "risk_percent": str(self.max_risk_percent_per_trade),
            "live_risk_percent": str(self.live_max_risk_percent_per_trade),
            "effective_risk_percent": str(self.effective_risk_percent),
            "daily_loss_percent": str(self.max_daily_loss_percent),
            "drawdown_percent": str(self.max_drawdown_percent),
            "max_daily_trades": self.max_daily_trades,
            "min_daily_trades_target": self.min_daily_trades_target,
            "max_open_positions": self.max_open_positions,
            "target_profit_usd": str(self.target_profit_usd_per_trade),
            "min_net_reward_risk": str(self.min_net_reward_risk),
            "timeframes": [self.primary_timeframe, self.higher_timeframe, self.trend_timeframe],
            "strategy_weights": {key: str(value) for key, value in self.strategy_weights.items()},
            "strategy_min_agreeing": self.strategy_min_agreeing,
            "strategy_min_coverage": str(self.strategy_min_coverage),
            "strategy_min_agreement": str(self.strategy_min_agreement),
            "trailing_levels": self.trailing_levels,
            "news_required": self.news_required_for_entry,
            "news_source_policy_count": len(self.news_source_coverage),
            "calendar_provider": self.calendar_provider,
            "telegram_configured": bool(self.telegram_bot_token.get_secret_value()),
            "ai_provider": self.ai_provider,
            "ai_rule_fallback_enabled": self.ai_rule_fallback_enabled,
            "ai_rule_fallback_min_score": self.ai_rule_fallback_min_score,
            "ai_rule_fallback_allow_live": self.ai_rule_fallback_allow_live,
            "openai_response_format": self.openai_response_format,
            "model_filter_enabled": self.model_filter_enabled,
            "model_algorithm": self.model_algorithm,
            "config_fingerprint": self.safety_fingerprint(),
            "strategy_fingerprint": self.strategy_fingerprint(),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
