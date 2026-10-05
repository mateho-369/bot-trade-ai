"""TEST ONLY synthetic runs, rehashed hostile local bundles and invented labels; never genuine provenance."""

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from backtesting.audit.contracts import digest, sha
from backtesting.backtester import Backtester, ReplaySettings
from backtesting.contracts import BacktestOptions
from backtesting.dataset import HistoricalDataset
from scripts.make_backtest_fixture import make_fixture
from scripts.make_backtest_model_fixture import make_model_fixture


@pytest.fixture(scope="module")
def completed_replay(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_completed_research")
    path = make_fixture(root / "input", replay_minutes=1)
    opts = BacktestOptions(review_mode="synthetic_research", simulate_orders=True, close_at_end=True)
    asyncio.run(Backtester(HistoricalDataset.load(path), ReplaySettings(), options=opts).run(root / "run"))
    return root / "run"


@pytest.fixture(scope="module")
def completed_ml_replay(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_completed_ml_research")
    path = make_model_fixture(root / "input", replay_minutes=1)
    opts = BacktestOptions(review_mode="synthetic_research", simulate_orders=True, close_at_end=True)
    asyncio.run(
        Backtester(HistoricalDataset.load(path), ReplaySettings(model_filter_enabled=True), options=opts).run(
            root / "run"
        )
    )
    return root / "run"


def clone_bundle(source, root):
    # copyfile, not hardlink: each mutated TEST bundle has a new private DB/captured input tree.
    shutil.copytree(source, root)
    return root


def document(path):
    return json.loads(Path(path).read_bytes())


def write(path, body):
    Path(path).write_text(json.dumps(body, sort_keys=True, allow_nan=False) + "\n")


def lines(path):
    return [json.loads(row) for row in Path(path).read_text().splitlines()]


def write_lines(path, rows):
    Path(path).write_text("".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows))


def reseal_for_attack(root):
    """Deliberately forged LOCAL manifest. No user/external signature or provenance implied."""
    body = document(root / "bundle.json")
    body["files"] = {
        p.relative_to(root).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
        for p in root.rglob("*")
        if p.is_file() and p.name != "bundle.json"
    }
    write(root / "bundle.json", body)


def edit_json(root, name, change, *, rehash=True):
    body = document(root / name)
    change(body)
    write(root / name, body)
    if rehash:
        reseal_for_attack(root)


def edit_lines(root, name, change, *, rehash=True):
    body = lines(root / name)
    change(body)
    write_lines(root / name, body)
    if rehash:
        reseal_for_attack(root)


def edit_db(root, sql, parameters=()):
    # TEST SETUP writes ONLY this cloned disposable DB. Auditor must never make this connection.
    connection = sqlite3.connect(root / "data/replay.db")
    try:
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()
    reseal_for_attack(root)


def inventory(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in root.rglob("*")
        if p.is_file()
    }


def checkpoint_edit(root, change):
    body = document(root / "data/paper/state.json")
    change(body["state"])
    body["sha256"] = digest(body["state"])
    write(root / "data/paper/state.json", body)
    reseal_for_attack(root)
