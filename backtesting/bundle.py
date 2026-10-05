"""Only the NEW completed private runner publishes a closure manifest; auditor never seals/repairs a run."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from backtesting.audit.contracts import (
    BUNDLE_FORMAT,
    BUNDLE_NAME,
    HASH_FIELDS,
    MAX_DATABASE_BYTES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    REQUIRED_FILES,
    object_json,
    sha,
    valid_hash,
)
from backtesting.audit.files import inventory
from backtesting.dataset import DatasetError
from readiness.files import read_bytes
from readiness.sqlite_snapshot import sidecar_state


def seal_completed_run(root):
    """Bounded immutable completion marker AFTER runner shutdown and private Database.close()."""
    try:
        root, names = inventory(root)
        if BUNDLE_NAME in names or "failure.json" in names or not REQUIRED_FILES.issubset(names):
            raise ValueError
        sidecar_state(root / "data/replay.db", root=root)  # Never checkpoint, replay or remove a WAL/journal.
        if any(name.endswith(("-wal", "-shm", "-journal")) for name in names):
            raise ValueError
        run = object_json(read_bytes(root / "run.json", root=root))
        completion = object_json(read_bytes(root / "completion.json", root=root))
        if (
            run.get("format") != "reflex-replay-run-v1"
            or run.get("source") != "historical"
            or completion != {"status": "completed", "promotion_eligible": False}
        ):
            raise ValueError
        from backtesting.contracts import DatasetManifest

        manifest = DatasetManifest.model_validate(
            object_json(read_bytes(root / "inputs" / run["input_manifest"], root=root, limit=1048576))
        )
        inputs = {
            *[item.path for item in (*manifest.bars, *manifest.ticks)],
            *[item.path for item in (manifest.news, manifest.reviews) if item],
            run["input_manifest"],
        }
        models = set()
        if manifest.model:
            inputs.update((manifest.model.artifact.path, manifest.model.learning_dataset.path))
            models.add("data/models/" + manifest.model.artifact.sha256 + ".json")
        if names != REQUIRED_FILES | {"inputs/" + item for item in inputs} | models:
            raise ValueError
        records, total = {}, 0
        for name in sorted(names):
            limit = MAX_DATABASE_BYTES if name == "data/replay.db" else MAX_FILE_BYTES
            raw = read_bytes(root / name, root=root, limit=limit)
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ValueError
            records[name] = {"sha256": sha(raw), "bytes": len(raw)}
        bindings = {name: valid_hash(run[name]) for name in HASH_FIELDS}
        body = {
            "format": BUNDLE_FORMAT,
            "purpose": "research_only",
            "source": "historical",
            "completed": True,
            "promotion_eligible": False,
            "genuine_owner_authenticated": False,
            "production_model_activated": False,
            "manifest_self_hash_excluded": True,
            "bindings": bindings,
            "files": records,
        }
        raw = (json.dumps(body, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(raw) > 1048576 or total + len(raw) > MAX_TOTAL_BYTES:
            raise ValueError
        # Flush a private same-volume temporary file, then publish without replacing any existing bundle.
        descriptor, name = tempfile.mkstemp(prefix=".bundle-", suffix=".tmp", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, root / BUNDLE_NAME)
        finally:
            temporary.unlink(missing_ok=True)
        if os.name != "nt":
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return sha(raw)
    except Exception:
        raise DatasetError(
            "completed private research bundle could not be sealed; keep the original files"
        ) from None
