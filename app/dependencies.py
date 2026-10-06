"""Explicit composition of the actual guarded services. No module-global clients.

Caller holds runtime OS lock and supplies an existing verified DB. This function
constructs resources only: no login, provider request, poll, migration or resume.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import make_url

from ai.ai_supervisor import AISupervisor
from ai.model_registry import ModelRegistry
from app.notifications import RuntimeNotices
from app.reporter import Reporter
from core.settings import OperatingMode
from news.news_manager import NewsManager
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.mt5_client import MT5Client
from trading.paper_mt5 import PaperMT5Client
from trading.position_manager import PositionManager
from trading.risk_types import RuntimeProfile
from trading.simulation import SimulatedBroker
from trading.types import TradingDisabled


@dataclass
class RuntimeResources:
    settings: object
    database: object
    broker: object
    engine: ExecutionEngine
    signals: SignalEngine
    supervisor: AISupervisor
    news: NewsManager
    positions: PositionManager
    reporter: Reporter
    notices: RuntimeNotices
    ai_first: object = None  # ai.ai_first.AIFirstLayer when AI_FIRST_ENABLED=true
    alerts: object = None  # app.alerts.AlertCenter


def compose(settings, database, *, broker=None):
    if settings.mode == OperatingMode.BACKTEST:
        raise TradingDisabled("backtests cannot be scheduled as a daemon")
    if database.settings.safety_fingerprint() != settings.safety_fingerprint():
        raise TradingDisabled("runtime/database configuration differs")
    if broker is None:
        if settings.mt5_backend == "mock":
            broker = MockMT5Client(settings)
        else:
            native = MT5Client(settings)
            broker = PaperMT5Client(native, settings) if settings.mode == OperatingMode.PAPER else native
    if broker.settings.safety_fingerprint() != settings.safety_fingerprint():
        raise TradingDisabled("runtime/broker configuration differs")
    profile = RuntimeProfile.current(
        settings, broker.market.source_kind if isinstance(broker, SimulatedBroker) else broker.source_kind
    )
    if settings.model_filter_enabled:
        # Scope/age/artifact/evaluation checked by registry; no implicit activation.
        profile = ModelRegistry(database, settings, broker.clock, profile).runtime_profile()
    engine = ExecutionEngine(broker, database, settings, profile=profile)
    news = NewsManager(database, settings, broker.clock, profile)
    supervisor = AISupervisor(database, settings, broker.clock, profile)
    reporter = Reporter(settings, secrets=database.secrets, clock=broker.clock)
    ai_first = None
    if settings.ai_first_enabled:
        from ai.ai_first import AIFirstLayer

        ai_first = AIFirstLayer(database, settings, broker, engine, supervisor, reporter=reporter)
    adaptive = ai_first.trailing if ai_first is not None and settings.ai_adaptive_trailing_enabled else None
    from app.alerts import AlertCenter

    alerts = AlertCenter(
        database, settings, broker.clock, control=engine.control, account_key=lambda: engine.account_key
    )
    if ai_first is not None:
        ai_first.alerts = alerts
    return RuntimeResources(
        settings=settings,
        database=database,
        broker=broker,
        engine=engine,
        signals=SignalEngine(broker, database, settings, profile=profile),
        supervisor=supervisor,
        news=news,
        positions=PositionManager(engine, adaptive=adaptive),
        reporter=reporter,
        notices=RuntimeNotices(database, settings, broker.clock, reporter),
        ai_first=ai_first,
        alerts=alerts,
    )


def require_existing_database(settings):
    url = make_url(settings.database_url.get_secret_value())
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:" or url.query:
            raise TradingDisabled("managed runtime requires existing persistent SQLite state")
        path = settings.resolve_path(url.database)
        if not path.is_file() or path.is_symlink():
            raise TradingDisabled(
                "initialize the existing dedicated database explicitly before runtime startup"
            )
