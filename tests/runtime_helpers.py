"""TEST ONLY composition. All markets synthetic; no real SDK/provider/network."""

from uuid import uuid4

from app.dependencies import compose
from app.health import RuntimeHealth
from app.scheduler import RuntimeScheduler
from core.database import Database
from tests.risk_helpers import MOMENT, config
from trading.mock_mt5 import MockMT5Client
from trading.types import ManualClock


async def runtime(tmp_path, **kwargs):
    values = dict(
        runtime_backup_enabled=False,
        ai_provider="disabled",
        ai_fallback_provider="disabled",
    )
    values.update(kwargs)
    settings = config(tmp_path, **values)
    database = Database(settings)
    database.initialize()
    resources = compose(settings, database, broker=MockMT5Client(settings, clock=ManualClock(MOMENT)))
    await resources.engine.initialize()
    await resources.signals.initialize()
    await resources.supervisor.initialize()
    await resources.news.initialize()
    health = RuntimeHealth(settings, str(uuid4()))
    health.resources, health.state = resources, "ready"
    jobs = RuntimeScheduler(resources, health)
    return resources, health, jobs


async def close_runtime(resources, jobs):
    jobs.stop_accepting()
    assert await jobs.drain(5)
    jobs.finish()
    await resources.engine.shutdown()
    await resources.supervisor.close()
    await resources.news.close()
    resources.database.close()
