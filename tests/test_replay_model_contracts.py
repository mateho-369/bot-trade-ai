"""Strict research-only model/file/time declarations. NOT authentic availability or owner attestation."""

import copy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from backtesting.contracts import DatasetManifest, ReplayModelSelection
from tests.backtest_helpers import document, fixture_path, rewrite


def test_frozen_selection_detached_aware_declares_research_only(small_model_input):
    selection = small_model_input.manifest.model
    assert selection.purpose == "research_only"
    assert selection.available_at <= selection.selected_at <= small_model_input.manifest.replay_from
    with pytest.raises(ValidationError):
        selection.selected_at = selection.selected_at + timedelta(days=1)
    values = selection.model_dump(mode="json")
    values["artifact"]["sha256"] = "c" * 64
    assert selection.artifact.sha256 != "c" * 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "reflex-model-v1"),
        ("purpose", "owner_approved"),
        ("purpose", "production"),
        ("description", ""),
        ("description", "x" * 1025),
        ("available_at", "2024-01-01"),
        ("selected_at", "2024-01-01T00:00:00"),
        ("selected_at", None),
        ("selected_at", 1704067200),
        ("models", []),
        ("automatic_activation", True),
        ("genuine_owner_authenticated", True),
    ],
)
def test_bad_untyped_or_privilege_claim_selection(small_model_input, field, value):
    values = small_model_input.manifest.model.model_dump(mode="json")
    values[field] = value
    with pytest.raises(ValidationError):
        ReplayModelSelection.model_validate(values)


@pytest.mark.parametrize(
    "path",
    [
        "../model.json",
        "/model.json",
        "C:/model.json",
        "https://example.com/m.json",
        "data\\model.json",
        "a/../m.json",
        "a//m.json",
        "./m.json",
    ],
)
def test_model_is_confined_local_input_not_executable_path(small_model_input, path):
    values = small_model_input.manifest.model.model_dump(mode="json")
    values["artifact"]["path"] = path
    with pytest.raises(ValidationError):
        ReplayModelSelection.model_validate(values)


@pytest.mark.parametrize("role", ["artifact", "learning_dataset"])
@pytest.mark.parametrize("digest", ["0" * 63, "F" * 64, "g" * 64, True])
def test_model_and_corpus_exact_sha_required(small_model_input, role, digest):
    values = small_model_input.manifest.model.model_dump(mode="json")
    values[role]["sha256"] = digest
    with pytest.raises(ValidationError):
        ReplayModelSelection.model_validate(values)


def test_available_model_after_selection_and_selection_after_replay_refused(small_model_input):
    values = small_model_input.manifest.model.model_dump(mode="json")
    values["available_at"] = (small_model_input.manifest.model.selected_at + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError):
        ReplayModelSelection.model_validate(values)
    body = small_model_input.manifest.model_dump(mode="json")
    body["model"]["selected_at"] = (
        small_model_input.manifest.replay_from + timedelta(microseconds=1)
    ).isoformat()
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(body)


@pytest.mark.parametrize(
    "role,other",
    [("artifact", "artifact"), ("learning_dataset", "bars"), ("artifact", "news"), ("artifact", "ticks")],
)
def test_model_input_roles_not_aliases(small_model_input, role, other):
    body = small_model_input.manifest.model_dump(mode="json")
    if other == "artifact":
        body["model"]["learning_dataset"] = copy.deepcopy(body["model"]["artifact"])
    else:
        file = body[other][0] if other in {"bars", "ticks"} else body[other]
        body["model"][role]["path"] = file["path"].swapcase()  # Case-insensitive collision.
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(body)


def test_old_v1_manifest_without_model_remains_supported(tmp_path):
    path = fixture_path(tmp_path)
    body = document(path)
    assert "model" not in body
    parsed = DatasetManifest.model_validate(body)
    assert parsed.model is None
    body["model"] = None
    rewrite(path, body)
    assert DatasetManifest.model_validate(document(path)).model is None
