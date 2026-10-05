"""Explicit miniature source/DB fixtures, NEVER authentic release/stage/native/owner evidence."""

import hashlib
import json
from pathlib import Path

from core.database import Database
from core.settings import Settings


def digest(data):
    return hashlib.sha256(data).hexdigest()


def seal(root, *, changes=None):
    files = {}
    for item in sorted(root.rglob("*")):
        if (
            item.is_file()
            and item.name != ".env"
            and not item.relative_to(root).as_posix().startswith(("docs/", "data/", ".venv/"))
        ):
            files[item.relative_to(root).as_posix()] = {
                "sha256": digest(item.read_bytes()),
                "bytes": item.stat().st_size,
            }
    doc = {
        "release": "0.10.0",
        "database_schema": 2,
        "archive_root": "mt5_ai_reflex_bot",
        "files": files,
        "hashed_files": len(files),
        "packaged_files": len(files) + 1,
        "python_sources": sum(Path(key).suffix == ".py" for key in files),
        "manifest_self_hash_excluded": True,
    }
    if changes:
        changes(doc)
    path = root / "docs/RELEASE_15_MANIFEST.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return doc, digest(path.read_bytes())


def source_tree(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "core").mkdir()
    (root / "main.py").write_text('"""TEST ONLY miniature source, not runnable trading code."""\n')
    (root / "core/__init__.py").write_text('"""TEST ONLY fixture."""\n')
    (root / "requirements.txt").write_text(
        'pydantic==2.13.5\nMetaTrader5==5.0.6231; sys_platform == "win32"\n'
    )
    (root / ".env.example").write_text(
        "DEMO_MODE=true\nLIVE_TRADING=false\nPAPER_TRADING=true\nSYMBOLS=EURUSD\n"
    )
    return root, *seal(root)


def sqlite_fixture(root):
    cfg = Settings(_env_file=None, project_root=root)
    db = Database(cfg)
    db.initialize()
    db.close()
    return root / "data/reflexbot.db"


def inventory(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode)
        for p in root.rglob("*")
        if p.is_file()
    }
