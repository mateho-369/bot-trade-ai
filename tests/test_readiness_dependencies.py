import importlib.metadata

import pytest

from readiness.contracts import InspectionError
from readiness.dependencies import direct_pins, inspect_dependencies
from tests.readiness_helpers import source_tree


def test_distribution_metadata_only_never_imports_a_native_sdk(tmp_path):
    root, _, _ = source_tree(tmp_path)
    calls = []

    def version(name):
        calls.append(name)
        return "2.13.5" if name == "pydantic" else "5.0.6231"

    findings, data = inspect_dependencies(root, windows=False, version_reader=version)
    assert calls == ["pydantic"] and data == {"required": 1, "matching": 1, "missing": 0, "different": 0}
    assert any(item.code == "transitive_audit_not_performed" for item in findings)
    calls.clear()
    _, data = inspect_dependencies(root, windows=True, version_reader=version)
    assert calls == ["pydantic", "MetaTrader5"] and data["matching"] == 2


@pytest.mark.parametrize("kind", ["missing", "changed", "exception"])
def test_metadata_failures_do_not_install_or_echo_sensitive_version_urls(tmp_path, kind):
    root, _, _ = source_tree(tmp_path)

    def version(name):
        if kind == "missing":
            raise importlib.metadata.PackageNotFoundError(name)
        if kind == "exception":
            raise RuntimeError("TEST_SECRET_PRIVATE_INDEX")
        return "TEST_SECRET_PRIVATE_INDEX"

    findings, data = inspect_dependencies(root, version_reader=version)
    assert any(item.status == "blocked" for item in findings)
    assert "TEST_SECRET" not in str(findings) + str(data)


@pytest.mark.parametrize(
    "text",
    [
        "pydantic>=2",
        "pydantic==2.13.5 # comment",
        "-r private.txt",
        "https://private.invalid/token",
        "--extra-index-url https://private.invalid",
        'thing==1; platform_system == "Windows"',
        "",
        "Thing==1\nthing==1",
    ],
)
def test_exact_bounded_direct_pins_no_remote_index_or_recursive_loader(tmp_path, text):
    root, _, _ = source_tree(tmp_path)
    (root / "requirements.txt").write_text(text)
    with pytest.raises(InspectionError):
        direct_pins(root, windows=False)
