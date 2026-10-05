"""Explicit Windows/VPS deployment preflight. Read-only by default; NEVER trading authorization.

Use -B to suppress normal interpreter cache writes. This CLI never connects a broker, launches
the MT5 terminal, registers a task, creates/migrates the database, resumes control state or
prints a secret value. Optional flags are the only way to probe writes, send ONE read-only AI
provider listing request, or query Windows power settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from readiness.deployment_preflight import deployment_preflight  # noqa: E402 -- disable cache writes first


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only deployment preflight observations; NEVER authorization to trade"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="docs/RELEASE_15_MANIFEST.json")
    parser.add_argument(
        "--trusted-manifest-sha256",
        help="Lowercase external trusted SHA of the release manifest; not a self-authenticating checksum",
    )
    parser.add_argument("--profile", choices=("development", "windows_native"), default="development")
    parser.add_argument(
        "--env-file",
        choices=(".env", ".env.example"),
        help="Explicit file in the source root; absence means immutable defaults only",
    )
    parser.add_argument(
        "--probe-writes",
        action="store_true",
        help="Create/remove temporary probe files in data/log/backup dirs; never opens the database file",
    )
    parser.add_argument(
        "--check-ai-provider",
        action="store_true",
        help="ONE bounded read-only provider listing GET; never a completion, broker, Telegram or news call",
    )
    parser.add_argument(
        "--check-power-settings",
        action="store_true",
        help="Windows-only informational powercfg query; settings are never changed",
    )
    parser.add_argument(
        "--allow-native-import",
        action="store_true",
        help="Opt-in import of the Windows native SDK module; it does NOT connect, login or place orders",
    )
    parser.add_argument(
        "--inspect-sqlite-snapshot",
        action="store_true",
        help="Bounded in-memory COPY of the main database file only; never checkpoint/delete/reset",
    )
    args = parser.parse_args(argv)
    try:
        result = deployment_preflight(
            args.root,
            profile=args.profile,
            env_name=args.env_file,
            manifest_name=args.manifest,
            trusted_manifest_sha256=args.trusted_manifest_sha256,
            probe_writes=args.probe_writes,
            check_ai_provider=args.check_ai_provider,
            check_power_settings=args.check_power_settings,
            allow_native_import=args.allow_native_import,
            inspect_sqlite_snapshot=args.inspect_sqlite_snapshot,
        )
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if result["overall"] == "preflight_checks_passed" else 2
    except Exception:
        # Fixed redacted refusal: never echo raw configuration, paths, SQL or exception text.
        print(
            '{"format":"reflex-preflight-v1","overall":"blocked","error":"preflight_refused",'
            '"trading_authorized":false,"broker_connected":false,"mt5_launched":false,'
            '"orders_placed":0,"network_requests":0,"write_probes_performed":0,'
            '"database_created":false,"database_file_modified":false,"configuration_modified":false,'
            '"secret_values_printed":false}'
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
