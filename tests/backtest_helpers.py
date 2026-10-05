"""TEST ONLY artificial prices, stamps, declarations and signatures; never genuine stage qualification."""

import csv
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from backtesting.backtester import isolated_settings
from backtesting.broker import ReplayBroker
from backtesting.dataset import HistoricalDataset
from backtesting.market import HistoricalMarket
from backtesting.news import ReplayNews
from backtesting.reviews import ReplayReviewer
from core.database import Database
from core.settings import Settings
from scripts.make_backtest_fixture import make_fixture
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.position_manager import PositionManager
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


def fixture_path(tmp_path, *, mode="ticks", warmup=60, minutes=1):
    return make_fixture(tmp_path / "fixture", quote_mode=mode, warmup_minutes=warmup, replay_minutes=minutes)


def document(path):
    return json.loads(Path(path).read_text())


def rewrite(path, body):
    Path(path).write_text(json.dumps(body, allow_nan=False))


def patch_manifest(path, **changes):
    body = document(path)
    body.update(changes)
    rewrite(path, body)
    return body


def patch_csv(manifest, role="bars", change=None):
    body = document(manifest)
    declaration = body[role][0]
    path = Path(manifest).parent / declaration["path"]
    reader = csv.DictReader(io.StringIO(path.read_text()))
    fields, rows = reader.fieldnames, list(reader)
    if change:
        change(rows)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    raw = buffer.getvalue().encode()
    path.write_bytes(raw)
    declaration["sha256"] = hashlib.sha256(raw).hexdigest()
    rewrite(manifest, body)
    return rows


def patch_payload(manifest, role, body):
    data = document(manifest)
    path = Path(manifest).parent / (data.get(role, {}).get("path") or role + ".json")
    rewrite(path, body)
    data[role] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    rewrite(manifest, data)


@asynccontextmanager
async def runtime(tmp_path, dataset, *, options=None, mode="synthetic_research", **settings):
    root = tmp_path / "runtime"
    root.mkdir()
    cfg = isolated_settings(Settings(_env_file=None, **settings), dataset, root)
    clock = ManualClock(dataset.manifest.replay_from)
    market = HistoricalMarket(dataset, cfg, clock)
    await market.initialize()
    market.seed()
    db = Database(cfg)
    db.initialize()
    profile = RuntimeProfile.current(cfg, SourceKind.HISTORICAL)
    broker = ReplayBroker(market, cfg, ledger_id="TEST_ONLY_REPLAY")
    engine = ExecutionEngine(broker, db, cfg, profile=profile)
    await engine.initialize()
    signals = SignalEngine(market, db, cfg, profile=profile)
    await signals.initialize()
    reviewer = ReplayReviewer(
        dataset.review_document, mode=mode, dataset=dataset, profile=profile, clock=clock
    )
    news = ReplayNews(dataset.news_document, cfg)
    manager = PositionManager(engine)
    try:
        yield engine, signals, market, news, reviewer, manager
    finally:
        await engine.shutdown()
        db.close()


async def finalized(signals, news, reviewer):
    return await signals.evaluate(
        "EURUSD", reviewer=reviewer, news=news.window("EURUSD", signals.clock.now())
    )


def step(market, seconds):
    market.clock.advance(timedelta(seconds=seconds))
    while (
        market._cursor < len(market.event_times) and market.event_times[market._cursor] <= market.clock.now()
    ):
        closing, opening = market.begin_batch(market.event_times[market._cursor])
        market.finish_batch(opening)
    return market.clock.now()


def clone_dataset(dataset, path, **documents):
    path.mkdir()
    manifest = None
    for name, raw in dataset.files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        if manifest is None:
            manifest = target
    for role, body in documents.items():
        if body is None:
            current = document(manifest)
            current.pop(role, None)
            rewrite(manifest, current)
        else:
            patch_payload(manifest, role, body)
    return HistoricalDataset.load(manifest)
