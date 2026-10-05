"""Complete bounded file inventory; do not repair, follow links, chmod or create anything."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from backtesting.audit.contracts import MAX_FILES, MAX_SCAN_NODES
from readiness.contracts import InspectionError
from readiness.files import checked_path, relative_name


def inventory(root):
    root = checked_path(Path(root))
    if not root.is_dir():
        raise InspectionError("bundle_directory_required")
    files, aliases, stack, nodes = set(), set(), [root], 0
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                nodes += 1
                if nodes > MAX_SCAN_NODES:
                    raise InspectionError("bundle_scan_bound")
                path = checked_path(Path(entry.path), root=root)
                name = relative_name(path.relative_to(root).as_posix())
                if name.casefold() in aliases:
                    raise InspectionError("bundle_case_collision")
                aliases.add(name.casefold())
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files.add(name)
                    if len(files) > MAX_FILES:
                        raise InspectionError("bundle_file_count_bound")
                else:
                    raise InspectionError("bundle_nonregular_or_linked_file")
    return root, frozenset(files)
