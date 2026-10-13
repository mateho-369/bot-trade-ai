"""Release-builder tool: write docs/RELEASE_16_MANIFEST.json from the CURRENT source tree.

Run this ONLY after reviewing the exact source you intend to release and after the full test suite
passes on that same source. Supply the actual passing-test count from that run. The builder hashes
bytes; it does not sign anything, prove provenance, start a runtime, read .env/credentials, open a
database, contact a network/broker or grant any permission.

Then verify the result with the ordinary read-only checker:

    python -B -m scripts.verify_release
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

from readiness.files import MAX_DECLARED_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from readiness.integrity import IGNORED, _secret_file

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docs/RELEASE_16_MANIFEST.json"
PREDECESSOR = "docs/RELEASE_15_MANIFEST.json"
RELEASE = "0.14.0"


def collect(root: Path, manifest_name: str) -> dict[str, dict[str, object]]:
    records, total = {}, 0
    for directory, folders, files in os.walk(root, topdown=True, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in IGNORED)
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == manifest_name or _secret_file(relative) or path.is_symlink():
                continue  # Never hash secrets/runtime state/logs or the manifest itself.
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise SystemExit(f"refusing oversized file: {relative}")
            total += len(data)
            records[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if not 1 <= len(records) <= MAX_DECLARED_FILES or total > MAX_TOTAL_BYTES:
        raise SystemExit("source tree outside the bounded manifest contract")
    return dict(sorted(records.items()))


def build(root: Path, *, pytest_passed: int, manifest_name: str = MANIFEST) -> dict:
    records = collect(root, manifest_name)
    predecessor = root / PREDECESSOR
    previous = json.loads(predecessor.read_text()) if predecessor.is_file() else {}
    return {
        "release": RELEASE,
        "database_schema": 2,
        "date": date.today().isoformat(),
        "cumulative_parts": list(range(1, 17)),
        "archive_root": "mt5_ai_reflex_bot",
        "packaged_files": len(records) + 1,
        "hashed_files": len(records),
        "python_sources": sum(Path(name).suffix == ".py" for name in records),
        "pytest_passed": pytest_passed,
        "part16_scope": [
            "shared_resume_gates_and_autonomous_demo_recovery",
            "local_operator_controls_and_ops_cli",
            "remove_telegram_controls_mini_app_and_owner_api",
            "outbound_only_telegram_reporting_and_local_reports",
            "prompt_free_windows_demo_startup_and_preflight",
            "readiness_docs_tests_and_release_manifest",
        ],
        "evidence": (
            "offline synthetic/software/scripted-HTTP fixture regression; NOT genuine "
            "history/local-operator/windows-native/provider/strategy qualification"
        ),
        "predecessor_part15": {
            "manifest_sha256": hashlib.sha256(predecessor.read_bytes()).hexdigest()
            if predecessor.is_file()
            else None,
            "actual_pytest_passed": previous.get("pytest_passed"),
            "source_hashes_verified": False,
            "note": "Part 15 bytes were superseded by Part 16 edits; its manifest is historical only.",
        },
        "manifest_self_hash_excluded": True,
        "integrity_is_not_signature_or_trading_permission": True,
        "files": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write the current-release byte manifest (builders only).")
    parser.add_argument("--pytest-passed", type=int, required=True, help="passed count from the full suite")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.pytest_passed <= 0:
        print("A positive full-suite passed count is required.", file=sys.stderr)
        return 2
    root = args.root.resolve()
    document = build(root, pytest_passed=args.pytest_passed)
    text = json.dumps(document, indent=2, sort_keys=False) + "\n"
    (root / MANIFEST).write_bytes(text.encode("utf-8"))
    print(
        json.dumps(
            {
                "manifest": MANIFEST,
                "hashed_files": document["hashed_files"],
                "python_sources": document["python_sources"],
                "manifest_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "trading_permission": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
