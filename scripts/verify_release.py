"""Standard-library source/manifest verifier. Use -B; no configuration/SDK/network/state import."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from readiness.integrity import verify_release  # noqa: E402 -- disable interpreter cache writes first


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare release bytes/closure; hashes are not signatures or trading approval"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="docs/RELEASE_16_MANIFEST.json")
    parser.add_argument(
        "--trusted-manifest-sha256",
        help="Lowercase external trusted SHA; not a self-authenticating local checksum",
    )
    args = parser.parse_args(argv)
    result = verify_release(
        args.root, manifest_name=args.manifest, trusted_manifest_sha256=args.trusted_manifest_sha256
    )
    print(
        json.dumps({"format": "reflex-release-verification-v1", **result.to_dict()}, sort_keys=True, indent=2)
    )
    return 0 if result.integrity_verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
