"""Read-only completed RESEARCH bundle audit. Use -B; no inherited .env or Settings/credential loading."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from backtesting.audit.runner import audit_bundle  # noqa: E402 -- disable cache writes before imports


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bounded read-only research consistency, NEVER trading authorization"
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="Explicit completed PRIVATE replay bundle directory"
    )
    parser.add_argument(
        "--trusted-bundle-sha256", help="Optional externally trusted lowercase manifest SHA, not a signature"
    )
    args = parser.parse_args(argv)
    result = audit_bundle(args.run, trusted_sha256=args.trusted_bundle_sha256)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["internal_consistency_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
