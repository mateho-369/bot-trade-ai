"""Safe operator commands. This CLI never connects to MT5 or places orders."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from core.database import Database
from core.logging_setup import configure_logging
from core.settings import Settings, get_settings, live_trading_requested


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MT5 AI ReflexBot: safe non-trading operator commands")
    parser.add_argument("command", choices=("check-config", "init-db", "status", "migrate-db"))
    parser.add_argument("--env-file", type=Path, help="explicit .env path; must exist")
    args = parser.parse_args(argv)
    if live_trading_requested(args.env_file):
        print("LIVE_TRADING=true is refused in this build; live orders cannot be started.", file=sys.stderr)
        return 2
    if args.env_file and not args.env_file.is_file():
        print("Explicit environment file does not exist.", file=sys.stderr)
        return 2
    try:
        settings = Settings(_env_file=args.env_file) if args.env_file else get_settings()
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "mode/safety invariants"
                for error in exc.errors()
            }
        )
        # Do not print pydantic error dictionaries: they can contain raw inputs.
        print("Invalid configuration in: " + ", ".join(fields), file=sys.stderr)
        return 2
    except (SettingsError, ValueError) as exc:
        print(f"Configuration loading failed ({type(exc).__name__}); check .env syntax.", file=sys.stderr)
        return 2
    database: Database | None = None
    try:
        configure_logging(settings)
        if args.command == "check-config":
            print(json.dumps(settings.public_config(), indent=2))
            return 0
        database = Database(settings)
        if args.command == "init-db":
            database.initialize()
            from ai.decision_journal import ensure_journal_tables

            ensure_journal_tables(database)  # Additive AI journal/overlay tables; core schema unchanged.
            from app.alerts import ensure_alert_tables

            ensure_alert_tables(database)  # Additive Alert Center table.
        migration = {}
        if args.command == "migrate-db":
            from core.migrations import migrate_v1_to_v2

            backup = migrate_v1_to_v2(database)
            migration = {"migration": "1_to_2", "backup": str(backup.relative_to(settings.project_root))}
        print(json.dumps(database.status() | migration, indent=2))
        return 0
    except Exception as error:
        logging.getLogger("reflexbot.operator").error(
            "Operator command failed (%s); this CLI did not connect to a broker.", type(error).__name__
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
