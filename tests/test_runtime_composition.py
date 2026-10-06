"""Resource matching, no factory login and fail-closed explicit DB startup."""

import pytest
from sqlalchemy import select

from app.dependencies import compose, require_existing_database
from app.lifecycle import RuntimeLifecycle
from core.database import Database
from core.models import AuditLog
from core.settings import Settings
from scripts.smoke_runtime import OfflineRuntimeSettings
from tests.risk_helpers import MOMENT, config
from trading.mock_mt5 import MockMT5Client
from trading.types import ManualClock, TradingDisabled


async def test_factory_constructs_without_login_provider_poll_or_control_claim(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    broker = MockMT5Client(cfg, clock=ManualClock(MOMENT))
    r = compose(cfg, db, broker=broker)
    try:
        assert not r.engine._initialized and not r.news._initialized and not r.supervisor._initialized
        assert not r.signals._initialized and not broker.health()["connected"]
        assert r.engine.control.session_id is None and r.reporter is not None and not r.reporter.enabled
        with db.session() as sql:
            assert sql.scalar(select(AuditLog.id).where(AuditLog.action == "runtime.claimed_paused")) is None
    finally:
        await r.supervisor.close()
        await r.news.close()
        await broker.shutdown()
        db.close()


@pytest.mark.parametrize("backend_config", ["database", "broker"])
def test_factory_refuses_mismatched_risk_configuration(tmp_path, backend_config):
    cfg, other = config(tmp_path), config(tmp_path, max_daily_trades=11)
    db = Database(other if backend_config == "database" else cfg)
    try:
        with pytest.raises(TradingDisabled, match="differs"):
            compose(cfg, db, broker=MockMT5Client(other if backend_config == "broker" else cfg))
    finally:
        db.close()


def test_model_filter_has_no_silent_rule_baseline_fallback(tmp_path):
    cfg = config(tmp_path, model_filter_enabled=True)
    db = Database(cfg)
    db.initialize()
    try:
        with pytest.raises(TradingDisabled, match="active model"):
            compose(cfg, db)
        assert db.status()["state"] == "paused"
    finally:
        db.close()


@pytest.mark.parametrize(
    "url", ["sqlite:///data/missing.db", "sqlite:///:memory:", "sqlite:///data/x.db?mode=ro"]
)
def test_existing_db_requirement_does_not_create_financial_state(tmp_path, url):
    cfg = Settings(_env_file=None, project_root=tmp_path, database_url=url)
    with pytest.raises(TradingDisabled):
        require_existing_database(cfg)
    assert not list(tmp_path.glob("data/*.db"))


async def test_daemon_refuses_missing_db_before_factory_or_empty_sqlite_creation(tmp_path):
    cfg = Settings(_env_file=None, project_root=tmp_path)
    calls = []

    def fail_if_called(*args):
        calls.append(True)
        raise AssertionError("factory must not run without initialized financial state")

    service = RuntimeLifecycle(cfg, factory=fail_if_called)
    with pytest.raises(TradingDisabled):
        await service.start()
    await service.stop()
    assert not calls and not list(tmp_path.glob("data/*.db"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("LIVE_TRADING", "true"),
        ("MT5_BACKEND", "real"),
        ("PAPER_TRADING", "false"),
        ("TELEGRAM_BOT_TOKEN", "DO_NOT_CONTACT_ANYTHING"),
    ],
)
def test_runtime_smoke_ignores_inherited_environment(monkeypatch, tmp_path, key, value):
    monkeypatch.setenv(key, value)
    cfg = OfflineRuntimeSettings(_env_file=None, project_root=tmp_path)
    assert cfg.paper_trading and not cfg.live_trading and cfg.mt5_backend == "mock"
    assert not cfg.telegram_bot_token.get_secret_value()
