"""Explicit offline/stdout-only preflight; no broker/DB original connection, daemon or owner approval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from readiness.runner import preflight  # noqa: E402 -- disable interpreter cache writes first


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only offline readiness observations, NEVER authorization"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="docs/RELEASE_15_MANIFEST.json")
    parser.add_argument("--trusted-manifest-sha256")
    parser.add_argument("--profile", choices=("development", "windows_native"), default="development")
    parser.add_argument(
        "--env-file",
        choices=(".env", ".env.example"),
        help="Explicit file in the source root; absence means file-only immutable defaults",
    )
    parser.add_argument(
        "--inspect-sqlite-snapshot",
        action="store_true",
        help=(
            "Bounded main-file in-memory COPY only; pending WAL/journal refuses, "
            "NEVER checkpoint/delete/reset"
        ),
    )
    args = parser.parse_args(argv)
    try:
        result = preflight(
            args.root,
            profile=args.profile,
            env_name=args.env_file,
            inspect_sqlite_snapshot=args.inspect_sqlite_snapshot,
            manifest_name=args.manifest,
            trusted_manifest_sha256=args.trusted_manifest_sha256,
        )
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if result["overall"] == "offline_checks_passed" else 2
    except Exception:
        print(
            '{"format":"reflex-readiness-v1","overall":"blocked","error":"inspection_refused","trading_authorized":false,"application_state_writes":0}'
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
