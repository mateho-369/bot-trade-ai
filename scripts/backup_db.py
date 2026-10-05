"""Explicit SQLite backup, no restore, broker login, trading or schema creation."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.backups import create_backup
from core.database import Database
from core.settings import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite backup; stopped recovery bundle unless --db-only")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--db-only", action="store_true", help="ONLINE database only; not coherent paper recovery"
    )
    args = parser.parse_args(argv)
    database = None
    try:
        if not args.env_file.is_file():
            print("Existing reviewed .env required; no secrets in command arguments.")
            return 2
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        database = Database(settings)
        result = create_backup(settings, database, coherent=not args.db_only)
        print("Created " + result.name + "; manual reconciliation required, never automatic resume/reset.")
        return 0
    except Exception:
        print(
            "Backup refused/failed. Stop runtime cleanly; check SQLite/schema/artifacts and private storage."
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
