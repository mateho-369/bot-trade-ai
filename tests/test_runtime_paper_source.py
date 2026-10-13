"""Read-only marked FakeSDK inside paper composition; not real MT5 evidence."""

from app.dependencies import compose
from core.database import Database
from tests.fake_mt5_sdk import FakeSDK
from tests.risk_helpers import MOMENT, config
from trading.mt5_client import MT5Client
from trading.paper_mt5 import PaperMT5Client
from trading.types import ManualClock, SourceKind


async def test_paper_profile_binds_actual_market_not_paper_execution_label(tmp_path):
    cfg = config(tmp_path, mt5_backend="real")
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    sdk = FakeSDK(clock)
    native = MT5Client(cfg, sdk=sdk, clock=clock)
    paper = PaperMT5Client(native, cfg)
    r = compose(cfg, db, broker=paper)
    try:
        assert paper.source_kind == SourceKind.PAPER
        assert r.engine.profile.data_source == SourceKind.TEST_SDK
        assert r.signals.market is native and r.engine.broker is paper
        assert r.engine.profile == r.signals.profile == r.supervisor.profile == r.news.profile
        assert not sdk.calls and not sdk.requests
    finally:
        await r.supervisor.close()
        await r.news.close()
        await paper.shutdown()
        db.close()
