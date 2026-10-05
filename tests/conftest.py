"""Isolate tests from any real credentials or mode flags in the host environment."""

import pytest

from core.settings import Settings

pytest_plugins = ("tests.replay_model_helpers", "tests.bundle_audit_helpers")


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # Env-file loading is disabled explicitly in each test.
    import os

    names = {name.lower() for name in Settings.model_fields}
    names |= {
        field.validation_alias.lower()
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }
    for name in list(os.environ):
        if name.lower() in names:
            monkeypatch.delenv(name)
