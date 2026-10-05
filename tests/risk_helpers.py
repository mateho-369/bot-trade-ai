"""TEST ONLY synthetic contexts/owner. Never promotion evidence or real auth."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.database import Database
from core.settings import Settings
from trading.execution import ExecutionEngine
from trading.mock_mt5 import MockMT5Client
from trading.risk_types import DecisionContext, NewsWindow
from trading.types import ManualClock, Side, SourceKind

D = Decimal
MOMENT = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
OWNER = 42


def config(tmp_path, **kwargs):
    values = dict(
        _env_file=None,
        project_root=tmp_path,
        symbols=("EURUSD",),
        telegram_owner_id=OWNER,
        telegram_bot_token="123456789:TEST_ONLY_NEVER_CONTACT_TELEGRAM",
        max_slippage_points=2,
        atr_trailing_enabled=False,
    )
    if "max_daily_trades" in kwargs:
        values["min_daily_trades_target"] = min(6, kwargs["max_daily_trades"])
    values.update(kwargs)
    return Settings(**values)


def safe_context(clock, **kwargs):
    values = dict(
        observed_at=clock.now(),
        bar_closed_at=clock.now() - timedelta(seconds=60),
        source=SourceKind.SYNTHETIC,
        signal_score=90,
        ai_confidence=90,
        news=NewsWindow(True, True, clock.now(), clock.now(), clock.now() + timedelta(hours=1), "a" * 64),
    )
    values.update(kwargs)
    return DecisionContext(**values)


async def make_engine(tmp_path, *, clock=None, state_store=None, **kwargs):
    settings = config(tmp_path, **kwargs)
    database = Database(settings)
    database.initialize()
    broker = MockMT5Client(settings, clock=clock or ManualClock(MOMENT))
    engine = ExecutionEngine(broker, database, settings, state_store=state_store)
    await engine.initialize()
    return engine


async def open_one(engine, *, side=Side.BUY, key="1" * 64, resume=True):
    if resume:
        engine.control.resume(OWNER, account_key=engine.account_key)
    stop = D("1.09780") if side == Side.BUY else D("1.10232")
    plan = await engine.calculator.plan_market_order(
        "EURUSD", side, stop, strategy="TEST_SYNTHETIC", idempotency_key=key
    )
    assert plan is not None
    result = await engine.execute(plan, safe_context(engine.clock))
    return plan, result
