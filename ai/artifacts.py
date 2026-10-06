"""Confined immutable JSON artifacts; no pickle, joblib loading, arbitrary paths or overwrites."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ai.json_validation import AIInvalidResponse, strict_json
from core.settings import Settings
from trading.types import TradingDisabled, valid_key


def artifact_path(settings: Settings, digest: str, *, category: str = "models") -> Path:
    valid_key(digest)
    if category not in {"models", "candles"}:
        raise ValueError("unsupported artifact category")
    root = settings.project_root.resolve()
    data = settings.data_dir
    if data.is_absolute() or ".." in data.parts:
        raise TradingDisabled("artifact data path must be confined and relative")
    relative = data / category / (digest + ".json")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise TradingDisabled("symlink artifact paths are forbidden")
    path = settings.resolve_path(relative)
    if os.name == "nt":
        # Windows Path.resolve() can emit the extended-length prefix (\\?\) under
        # concurrent parent creation; compare normalized so a path genuinely inside
        # the project is never falsely rejected. Containment itself is unchanged.
        def _normalize(text: str) -> str:
            if text.startswith("\\\\?\\UNC\\"):
                text = "\\\\" + text[8:]
            elif text.startswith("\\\\?\\"):
                text = text[4:]
            return text.casefold()

        normalized = _normalize(str(path))
        normalized_root = _normalize(str(root))
        if normalized != normalized_root and not normalized.startswith(normalized_root + os.sep):
            raise TradingDisabled("artifact path escaped project")
        return path
    if not path.is_relative_to(root):
        raise TradingDisabled("artifact path escaped project")
    return path


def read_bytes(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
        raise TradingDisabled("artifact missing/oversized/symlinked")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise TradingDisabled("artifact exceeds bound")
    return raw


def save_immutable(settings: Settings, raw: bytes, *, category="models", max_bytes=None) -> tuple[Path, str]:
    limit = max_bytes or settings.model_max_artifact_bytes
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise TradingDisabled("invalid artifact size")
    digest = hashlib.sha256(raw).hexdigest()
    path = artifact_path(settings, digest, category=category)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Revalidate after mkdir. Local administrator/filesystem races are not a hostile-host sandbox.
    artifact_path(settings, digest, category=category)
    # O_EXCL on the final file exposed zero/partial bytes to concurrent readers.
    # Flush a private same-volume temp first, then atomically publish a hard link
    # WITHOUT overwriting an existing immutable digest. NTFS/Linux support this;
    # unsupported filesystems fail closed, never fall back to a partial publish.
    descriptor, name = tempfile.mkstemp(prefix="." + digest + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        artifact_path(settings, digest, category=category)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path, max_bytes=limit) != raw:
                raise TradingDisabled("immutable artifact collision/corruption") from None
        except OSError:
            raise TradingDisabled("atomic immutable artifact publication unavailable") from None
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return path, digest


def read_model(settings: Settings, digest: str) -> dict:
    path = artifact_path(settings, digest)
    raw = read_bytes(path, max_bytes=settings.model_max_artifact_bytes)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise TradingDisabled("model artifact integrity mismatch")
    try:
        return strict_json(
            raw,
            max_bytes=settings.model_max_artifact_bytes,
            max_depth=16,
            max_nodes=20000,
            max_string=settings.model_max_artifact_bytes,
        )
    except AIInvalidResponse:
        raise TradingDisabled("invalid model JSON; contents suppressed") from None
