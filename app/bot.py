"""EXPLICIT trading-runtime launcher, unlike the safe non-trading main.py CLI.

python -m app.bot --env-file .env
Uses the configured guarded broker; starts PAUSED. Do not run before reviewing
broker identity, persistent ledger, news entitlements and deployment instructions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.lifecycle import RuntimeLifecycle
from app.process_guard import managed_id
from core.logging_setup import configure_logging
from core.settings import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit ReflexBot runtime; restart always PAUSED")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--managed-id", type=managed_id)
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            print("Reviewed existing .env required. Initialize the DB explicitly before starting.")
            return 2
        settings = Settings(_env_file=args.env_file, project_root=args.env_file.resolve().parent)
        if not (settings.project_root / "main.py").is_file():
            raise ValueError("environment file must be in the deployed source root")
        configure_logging(settings)
        asyncio.run(RuntimeLifecycle(settings, identity=args.managed_id).run())
        return 0
    except Exception:
        logging.getLogger("reflexbot.runtime").error(
            "Runtime unavailable; raw configuration/SDK/provider errors suppressed. No automatic resume."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
