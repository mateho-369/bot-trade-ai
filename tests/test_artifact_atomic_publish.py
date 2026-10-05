"""Concurrent immutable digest publish must never expose zero/partial model bytes."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event

import pytest

from ai.artifacts import artifact_path, read_bytes, save_immutable
from tests.risk_helpers import config
from trading.types import TradingDisabled


def test_digest_absent_until_full_flushed_temporary_is_linked(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    raw = b'{"test":"' + b"x" * 64000 + b'"}'
    digest = sha256(raw).hexdigest()
    path = artifact_path(cfg, digest)
    entered, release = Event(), Event()
    import ai.artifacts as module

    original = module.os.link

    def blocked(source, destination):
        assert source.read_bytes() == raw
        assert not destination.exists()  # no partially published final name
        entered.set()
        assert release.wait(5)
        return original(source, destination)

    monkeypatch.setattr(module.os, "link", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(save_immutable, cfg, raw)
        assert entered.wait(3)
        assert not path.exists()
        release.set()
        assert future.result()[0] == path
    assert read_bytes(path, max_bytes=cfg.model_max_artifact_bytes) == raw
    assert list(path.parent.glob("*.tmp")) == []


def test_parallel_same_digest_no_partial_collision_and_one_final_file(tmp_path):
    cfg = config(tmp_path)
    raw = b'{"test":"' + b"x" * 100000 + b'"}'
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: save_immutable(cfg, raw), range(48)))
    assert len({path for path, _ in results}) == 1
    path = results[0][0]
    assert path.read_bytes() == raw
    assert list(path.parent.glob("*.tmp")) == []
    assert len(list(path.parent.glob("*.json"))) == 1


def test_existing_corrupt_digest_never_overwritten(tmp_path):
    cfg = config(tmp_path)
    raw = b'{"test":"immutable"}'
    path = artifact_path(cfg, sha256(raw).hexdigest())
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt")
    with pytest.raises(TradingDisabled, match="collision"):
        save_immutable(cfg, raw)
    assert path.read_bytes() == b"corrupt" and not list(path.parent.glob("*.tmp"))


def test_unsupported_atomic_publish_fails_closed_cleans_temp(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    raw = b'{"test":"immutable"}'
    import ai.artifacts as module

    def unsupported(*args):
        raise OSError("TEST_ONLY filesystem unsupported")

    monkeypatch.setattr(module.os, "link", unsupported)
    with pytest.raises(TradingDisabled, match="atomic immutable"):
        save_immutable(cfg, raw)
    path = artifact_path(cfg, sha256(raw).hexdigest())
    assert not path.exists() and not list(path.parent.glob("*.tmp"))
