"""OS-held locks, local health/stop files, and interactive Windows validation.

PID files are evidence, never authority to kill a process. Locks are not deleted:
removing a locked inode would let another process acquire a different inode.
"""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from core.security import canonical_json


class ProcessLock:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self):
        if self.handle is not None:
            raise RuntimeError("lock is already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError("symlink lock refused")
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle = handle
        except BaseException:
            handle.close()
            raise RuntimeError("another process owns this local lock") from None
        return self

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("symlink output refused")
    fd, name = tempfile.mkstemp(prefix=".runtime-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(canonical_json(data))
            out.flush()
            os.fsync(out.fileno())
        for attempt in range(3):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                # Windows readers hold the target without FILE_SHARE_DELETE for the
                # microseconds of a bounded read; retry briefly, then fail closed.
                import time

                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path, *, max_bytes=65536):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("bounded regular local JSON required")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("local JSON exceeds bound")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate local JSON key")
            result[key] = value
        return result

    data = json.loads(
        raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
    )
    if not isinstance(data, dict):
        raise ValueError("local JSON object required")
    return data


def managed_id(value: str):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("canonical managed UUID required")
    return value


def stop_path(settings, identity: str):
    return settings.resolve_path(settings.runtime_health_file).parent / f"stop-{managed_id(identity)}.json"


def request_stop(settings, identity: str):
    # Local operator/supervisor seam, NOT an HTTP trading endpoint.
    atomic_json(stop_path(settings, identity), {"managed_id": managed_id(identity), "stop": True})


def stop_requested(settings, identity: str):
    path = stop_path(settings, identity)
    if not path.exists():
        return False
    # Presence of a generation-scoped stop marker is downward-only authority.
    # Malformed/false/wrong-generation JSON inside it cannot keep a child trading.
    return True


def require_interactive_native(settings):
    if settings.mt5_backend != "real":
        return
    if os.name != "nt":
        raise RuntimeError("native MT5 requires Windows and a logged-on interactive user")
    session = ctypes.c_uint32()
    if (
        not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
        or session.value == 0
    ):
        raise RuntimeError("native MT5 cannot run in Session 0/service context")


def operator_stop_path(settings):
    return settings.resolve_path(settings.runtime_health_file).parent / "operator-stop.json"


def operator_stop_requested(settings):
    path = operator_stop_path(settings)
    if not path.exists():
        return False
    try:
        data = read_json(path, max_bytes=1024)
        return data != {"stop": False}  # malformed/unknown requests fail downward.
    except (OSError, ValueError):
        return True


def request_operator_stop(settings):
    atomic_json(operator_stop_path(settings), {"stop": True})
