"""Run the REAL locked-by-default owner API only, never a trading daemon.

python -m scripts.run_owner_interface --env-file .env
First run python main.py init-db; no auto-create/migrate/reset occurs here.
Resume/close stay disabled without explicit Part-10 runtime composition.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from app.owner_services import OwnerServices
from core.database import Database
from core.logging_setup import configure_logging
from core.settings import Settings
from miniapp.server import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Owner Mini App API ONLY; no broker/bot startup")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    database = None
    try:
        if not args.env_file.is_file():
            print("Explicit .env file required. Copy/review .env.example; do not put secrets in CLI URLs.")
            return 2
        settings = Settings(_env_file=args.env_file)
        configure_logging(settings)
        database = Database(settings)
        database.verify_schema()
        services = OwnerServices(database, settings)
        application = create_app(settings, services)
        uvicorn.run(
            application,
            host=settings.api_host,
            port=settings.api_port,
            workers=1,
            access_log=False,
            proxy_headers=False,
            log_config=None,
            timeout_keep_alive=5,
        )
        return 0
    except Exception:
        # No pydantic inputs / SQL URLs / tokens / initData / raw exceptions in console.
        logging.getLogger("reflexbot.owner_interface").error(
            "Owner interface unavailable; check configuration/schema/TLS composition. No broker was started."
        )
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
