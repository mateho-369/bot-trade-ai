from collections import namedtuple

from readiness.host import inspect_host
from tests.readiness_helpers import source_tree


def test_development_os_observation_is_not_a_native_session_acl_or_broker_probe(tmp_path):
    root, _, _ = source_tree(tmp_path)
    findings, data = inspect_host(
        root, profile="development", terminal_path="/PRIVATE_TEST_ONLY_NOT_OPENED.exe"
    )
    assert data["interpreter_bits"] in {32, 64} and not data["terminal_file_observed"]
    assert any(item.code == "development_platform_observed" for item in findings)
    assert "PRIVATE_TEST_ONLY" not in str(findings)


def test_native_profile_unknowns_remain_blocking_never_guessed_from_environment(tmp_path, monkeypatch):
    root, _, _ = source_tree(tmp_path)
    monkeypatch.setenv("SESSIONNAME", "Console")
    findings, _ = inspect_host(root, profile="windows_native")
    assert {
        "interactive_session_and_ntfs_acl_unverified",
        "native_feed_contract_and_local_operator_checks_unverified",
    }.issubset({item.code for item in findings if item.status == "blocked"})
    native_check = next(item for item in findings if item.code.endswith("local_operator_checks_unverified"))
    assert "local-operator" in native_check.message and "Telegram" not in native_check.message


def test_read_only_disk_floor_and_unknown_do_not_probe_write_or_create_root(tmp_path, monkeypatch):
    root, _, _ = source_tree(tmp_path)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("readiness.host.shutil.disk_usage", lambda root: Usage(1, 1, 0))
    findings, data = inspect_host(root, profile="development")
    assert not data["free_disk_meets_512mib_floor"] and any(item.status == "blocked" for item in findings)

    def unavailable(_):
        raise OSError("TEST_ONLY_SECRET_DRIVE_DETAILS")

    monkeypatch.setattr("readiness.host.shutil.disk_usage", unavailable)
    findings, data = inspect_host(root, profile="development")
    assert data["free_disk_meets_512mib_floor"] is None and "TEST_ONLY_SECRET" not in str(findings)
