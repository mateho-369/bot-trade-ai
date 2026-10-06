"""Release-manifest builder: TEST ONLY miniature trees; no runtime, credentials, network or broker."""

import json
import os

from readiness.integrity import verify_release
from scripts.build_release_manifest import MANIFEST, build, main


def miniature(tmp_path):
    root = tmp_path / "source"
    (root / "core").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "main.py").write_text('"""TEST ONLY miniature source."""\n')
    (root / "core/__init__.py").write_text('"""TEST ONLY fixture."""\n')
    (root / "requirements.txt").write_text("pydantic==2.13.5\n")
    (root / ".env.example").write_text("DEMO_MODE=true\nLIVE_TRADING=false\n")
    (root / "docs/NOTES.md").write_text("notes\n")
    (root / ".env").write_text("OPENAI_API_KEY=SENTINEL-NEVER-HASHED\n")
    (root / "data/reflexbot.db").write_bytes(b"SQLite format 3\x00")
    (root / "run.log").write_text("runtime log\n")
    for path in [root, *root.rglob("*")]:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    return root


def test_built_manifest_passes_the_read_only_release_verifier(tmp_path):
    root = miniature(tmp_path)
    assert main(["--pytest-passed", "7", "--root", str(root)]) == 0
    result = verify_release(root, manifest_name=MANIFEST)
    assert result.manifest_valid and result.integrity_verified, result.findings
    assert result.release == "0.14.0" and result.declared_files == result.verified_files
    document = json.loads((root / MANIFEST).read_text())
    assert document["pytest_passed"] == 7 and document["cumulative_parts"][-1] == 16
    assert document["predecessor_part15"]["source_hashes_verified"] is False
    assert document["integrity_is_not_signature_or_trading_permission"] is True


def test_secrets_runtime_state_logs_and_the_manifest_itself_are_never_hashed(tmp_path):
    root = miniature(tmp_path)
    (root / MANIFEST).write_text("{}")
    files = build(root, pytest_passed=1)["files"]
    assert {".env", "data/reflexbot.db", "run.log", MANIFEST}.isdisjoint(files)
    assert {"main.py", "core/__init__.py", ".env.example", "docs/NOTES.md"} <= set(files)
    assert "SENTINEL" not in json.dumps(files)


def test_any_later_source_edit_is_detected(tmp_path):
    root = miniature(tmp_path)
    main(["--pytest-passed", "3", "--root", str(root)])
    (root / "main.py").write_text('"""edited after sealing"""\n')
    result = verify_release(root, manifest_name=MANIFEST)
    assert not result.integrity_verified
    assert "declared_file_digest_mismatch" in {item.code for item in result.findings}


def test_nonpositive_test_count_is_refused(tmp_path):
    root = miniature(tmp_path)
    assert main(["--pytest-passed", "0", "--root", str(root)]) == 2
    assert not (root / MANIFEST).exists()
