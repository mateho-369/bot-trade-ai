"""Bounded stable reads. No directory creation, logs, chmod, URI fetch or executable loading."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath

from readiness.contracts import InspectionError

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_DECLARED_FILES = 1500
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_SCAN_NODES = 20000
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def relative_name(name: str) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= 240 or "\\" in name or ":" in name:
        raise InspectionError("invalid_relative_path")
    parts = name.split("/")
    if (
        any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", part)
            or part in {".", ".."}
            or part.endswith(".")
            or part.split(".", 1)[0].casefold() in RESERVED
            for part in parts
        )
        or PurePosixPath(name).is_absolute()
    ):
        raise InspectionError("invalid_relative_path")
    return name


def _linked(info):
    # Reject NTFS junction/reparse components as well as POSIX symbolic links.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def absolute_path(path: Path) -> Path:
    path = Path(path)
    if any(part == ".." for part in path.parts):
        raise InspectionError("traversing_path")
    return path.absolute()


def checked_path(path: Path, *, root: Path | None = None, missing: bool = False) -> Path:
    path = absolute_path(path)
    if root is not None:
        base = absolute_path(root)
        if not path.is_relative_to(base):
            raise InspectionError("outside_root")
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise InspectionError("missing_file") from None
        except OSError:
            raise InspectionError("unreadable_path") from None
        if _linked(info):
            raise InspectionError("linked_path")
    return path


def identity(info):
    if os.name == "nt":
        # Windows st_ctime_ns is the creation time; NTFS refines it after the first open,
        # so lstat and fstat can differ by a few milliseconds with no real mutation.
        # Change detection stays intact via dev/ino/size/mtime.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_bytes(path: Path, *, root: Path | None = None, limit: int = MAX_FILE_BYTES) -> bytes:
    if type(limit) is not int or not 1 <= limit <= 128 * 1024 * 1024:
        raise InspectionError("invalid_read_limit")
    path = checked_path(path, root=root)
    handle = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise InspectionError("regular_unlinked_file_required")
        if before.st_size > limit:
            raise InspectionError("file_too_large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        handle = os.open(path, flags)
        opened = os.fstat(handle)
        if identity(before) != identity(opened) or not stat.S_ISREG(opened.st_mode):
            raise InspectionError("file_changed_during_read")
        with os.fdopen(handle, "rb", closefd=True) as stream:
            handle = None
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if len(data) > limit:
            raise InspectionError("file_too_large")
        if identity(opened) != identity(after) or identity(after) != identity(path.lstat()):
            raise InspectionError("file_changed_during_read")
        checked_path(path, root=root)
        return data
    except InspectionError:
        raise
    except OSError:
        raise InspectionError("file_read_refused") from None
    finally:
        if handle is not None:
            os.close(handle)


def strict_json(data: bytes, *, limit: int = MAX_MANIFEST_BYTES):
    if not isinstance(data, bytes) or not data or len(data) > limit:
        raise InspectionError("invalid_json_size")
    try:
        text = data.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > 16:
                    raise ValueError
            elif char in "]}":
                depth -= 1

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result or len(key) > 256:
                    raise ValueError
                result[key] = value
            return result

        def integer(value):
            if len(value) > 12:
                raise ValueError
            return int(value)

        def forbidden(_):
            raise ValueError

        def number(value):
            if len(value) > 80 or not math.isfinite(float(value)):
                raise ValueError
            return float(value)

        return json.loads(
            text, object_pairs_hook=unique, parse_int=integer, parse_float=number, parse_constant=forbidden
        )
    except (ValueError, UnicodeError, RecursionError):
        raise InspectionError("invalid_json_document") from None
