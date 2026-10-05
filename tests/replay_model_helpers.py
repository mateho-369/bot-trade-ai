"""TEST ONLY model/corpus/private principals/labels. Never real historical availability or qualification."""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from backtesting.artifacts import copy_inputs, new_run_directory, write_json
from backtesting.backtester import ReplaySettings, isolated_settings
from backtesting.broker import ReplayBroker
from backtesting.contracts import DatasetManifest
from backtesting.dataset import HistoricalDataset
from backtesting.market import HistoricalMarket
from backtesting.model_replay import VerifiedReplayModel
from backtesting.news import ReplayNews
from backtesting.reviews import ReplayReviewer
from core.database import Database
from core.security import canonical_json, sha256_json
from scripts.make_backtest_model_fixture import make_model_fixture
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.position_manager import PositionManager
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


@pytest.fixture(scope="module")
def model_input(tmp_path_factory):
    path = make_model_fixture(tmp_path_factory.mktemp("ARTIFICIAL_replay_model") / "input")
    return HistoricalDataset.load(path)


@pytest.fixture(scope="module")
def small_model_input(tmp_path_factory):
    path = make_model_fixture(tmp_path_factory.mktemp("ARTIFICIAL_small_model") / "input", warmup_minutes=60)
    return HistoricalDataset.load(path)


def configured(history, root, **changes):
    return isolated_settings(ReplaySettings(model_filter_enabled=True, **changes), history, Path(root))


def verified(history, root, **changes):
    cfg = configured(history, root, **changes)
    profile = RuntimeProfile.current(cfg, SourceKind.HISTORICAL)
    model = VerifiedReplayModel.verify(history, cfg, profile)
    return model, cfg, RuntimeProfile(profile.code_hash, model.digest, SourceKind.HISTORICAL)


def changed_input(history, *, artifact=None, corpus=None, manifest=None, artifact_raw=None, corpus_raw=None):
    """Attack captured bytes but recompute declarations; this is not just a raw hash-mismatch test."""
    files = dict(history.files)
    name = next(iter(files))
    body = json.loads(files[name])
    if manifest:
        manifest(body)
    for role, change, raw in (("artifact", artifact, artifact_raw), ("learning_dataset", corpus, corpus_raw)):
        declaration = body["model"][role]
        if change is not None:
            document = json.loads(files[declaration["path"]])
            change(document)
            raw = canonical_json(document).encode()
        if raw is not None:
            files[declaration["path"]] = raw
            declaration["sha256"] = hashlib.sha256(raw).hexdigest()
    files[name] = canonical_json(body).encode()
    parsed = DatasetManifest.model_validate(body)
    manifest_digest = hashlib.sha256(files[name]).hexdigest()
    records = [
        {"path": key, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for key, raw in files.items()
        if key != name
    ]
    return replace(
        history,
        manifest=parsed,
        files=MappingProxyType(files),
        manifest_sha256=manifest_digest,
        dataset_sha256=sha256_json(
            {"manifest_sha256": manifest_digest, "files": sorted(records, key=lambda row: row["path"])}
        ),
    )


def prepare_private(root, history, model, cfg, profile):
    new_run_directory(root)
    copy_inputs(history, root)
    write_json(
        root / "run.json",
        {
            "format": "reflex-replay-run-v1",
            "status": "created",
            "model_sha256": model.digest,
            "code_hash": profile.code_hash,
            "strategy_config_hash": cfg.strategy_fingerprint(),
            "safety_config_hash": cfg.safety_fingerprint(),
            "dataset_sha256": history.dataset_sha256,
            "input_manifest": next(iter(history.files)),
            "replay_model": model.summary(),
        },
    )
    db = Database(cfg)
    db.initialize()
    return db


@asynccontextmanager
async def model_runtime(tmp_path, history):
    root = tmp_path / "private"
    model, cfg, profile = verified(history, root)
    clock = ManualClock(history.manifest.replay_from)
    db = prepare_private(root, history, model, cfg, profile)
    model.install_private(db, cfg, clock, profile)
    market = HistoricalMarket(history, cfg, clock)
    await market.initialize()
    market.seed()
    engine = ExecutionEngine(
        ReplayBroker(market, cfg, ledger_id="ARTIFICIAL_PRIVATE_MODEL"), db, cfg, profile=profile
    )
    await engine.initialize()
    signals = SignalEngine(market, db, cfg, profile=profile)
    await signals.initialize()
    reviewer = ReplayReviewer(
        history.review_document, mode="synthetic_research", dataset=history, profile=profile, clock=clock
    )
    news = ReplayNews(history.news_document, cfg)
    try:
        yield engine, signals, market, news, reviewer, PositionManager(engine), model
    finally:
        await engine.shutdown()
        await market.shutdown()
        db.close()
