"""Explicit SYNTHETIC read-only UI fixture; does not load .env/owner credentials.

python -m scripts.preview_owner_ui --host 0.0.0.0 --port 8000
Public /preview/data exists ONLY in this fixture. Trading actions are denied.
The temporary database is isolated and deleted on normal exit; no genuine data.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import uvicorn

from app.owner_services import OwnerServices
from core.database import Database
from core.settings import Settings
from miniapp.server import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description="SYNTHETIC read-only owner UI, never an auth fallback")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    # Also ignore inherited environment variables: constructing with explicit safe
    # values alone is insufficient because pydantic-settings reads process env.
    # Empty source settings subclass is for this deliberate no-credential fixture.
    class PreviewSettings(Settings):
        @classmethod
        def settings_customise_sources(
            cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        ):
            return (init_settings,)

    scratch = Path(__file__).resolve().parents[1] / ".cache" / "owner_ui"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as folder:
        cfg = PreviewSettings(
            _env_file=None,
            project_root=Path(folder),
            api_host=args.host,
            api_port=args.port,
            api_trusted_hosts=("127.0.0.1", "localhost", "*.e2b.app"),
        )
        database = Database(cfg)
        try:
            database.initialize()
            services = OwnerServices(database, cfg, preview_only=True)
            app = create_app(cfg, services, preview_only=True)
            print("SYNTHETIC UI ONLY: artificial records, no owner/broker/providers, all mutations denied.")
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                workers=1,
                access_log=False,
                proxy_headers=False,
                log_config=None,
                timeout_keep_alive=5,
            )
        finally:
            database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
