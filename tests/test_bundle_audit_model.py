"""Frozen model/vector/logistic evidence across real fixture approval; no genuine model availability claim."""

import copy

import pytest

from backtesting.audit.contracts import digest
from backtesting.audit.runner import audit_bundle
from tests.bundle_audit_helpers import (
    clone_bundle,
    document,
    edit_db,
    edit_json,
    edit_lines,
    lines,
    reseal_for_attack,
    write_lines,
)
from tests.test_bundle_audit_integrity import blocked
from trading.risk_types import DecisionContext


def test_actual_portable_logistic_probability_rechecked_not_retrained(completed_ml_replay):
    result = audit_bundle(completed_ml_replay)
    assert result["internal_consistency_verified"]
    assert result["observations"]["logistic_probability_observations_recomputed"] == 1
    assert result["observations"]["lightgbm_probability_observations_not_reexecuted"] == 0
    assert result["observations"]["training_reconstruction_reexecuted"] is False
    assert not result["production_model_activated"] and not result["historical_provenance_verified"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("probability", 0.9),
        ("threshold", 0.6),
        ("accepted", False),
        ("source", "mt5"),
        ("model_sha256", "a" * 64),
        ("code_hash", "b" * 64),
        ("policy_hash", "c" * 64),
        ("feature_vector_sha256", "d" * 64),
        ("feature_schema_hash", "e" * 64),
        ("replay_binding_sha256", "f" * 64),
    ],
)
def test_hash_recomputed_caller_gate_does_not_recompute_real_weights(
    completed_ml_replay, tmp_path, field, value
):
    root = clone_bundle(completed_ml_replay, tmp_path / "run")
    traces = lines(root / "signals.jsonl")
    payload = next(row["payload"] for row in traces if row["state"] == "approved")
    payload["model_gate"][field] = value
    payload["model_gate_digest"] = digest(payload["model_gate"])
    payload["decision_context"]["features"]["model_gate"] = copy.deepcopy(payload["model_gate"])
    payload["decision_digest"] = DecisionContext.from_dict(payload["decision_context"]).digest
    write_lines(root / "signals.jsonl", traces)
    reseal_for_attack(root)
    result = blocked(root)
    if field == "probability":
        assert "bundle_logistic_probability_mismatch" in {row["code"] for row in result["findings"]}


@pytest.mark.parametrize("file", ["inputs/ARTIFICIAL_model.json", "inputs/ARTIFICIAL_learning.json"])
def test_captured_training_bytes_are_in_bundle_not_ignored(completed_ml_replay, tmp_path, file):
    root = clone_bundle(completed_ml_replay, tmp_path / "run")
    # Resolve actual declared path: fixture names are an implementation detail, not a role guess.
    role = "artifact" if "model" in file else "learning_dataset"
    manifest = document(root / "inputs/manifest.json")
    declared = "inputs/" + manifest["model"][role]["path"]
    (root / declared).write_bytes((root / declared).read_bytes() + b"\n")
    blocked(root, "bundle_file_hash_or_length_changed")


@pytest.mark.parametrize(
    "fault",
    ["inactive", "scope", "missing_model", "proof", "snapshot", "corpus", "age", "label_time", "promotion"],
)
def test_rehashed_selected_snapshot_and_causal_model_proof_checked(completed_ml_replay, tmp_path, fault):
    root = clone_bundle(completed_ml_replay, tmp_path / "run")
    if fault == "inactive":
        edit_db(root, "UPDATE model_versions SET active=0")
    elif fault == "scope":
        edit_db(root, "UPDATE model_versions SET deployment_scope='paper'")
    elif fault == "missing_model":
        edit_db(root, "DELETE FROM model_versions")
    elif fault == "proof":
        edit_db(root, "UPDATE model_versions SET metrics_json='{}'")
    elif fault == "snapshot":
        file = next((root / "data/models").glob("*.json"))
        file.write_text("{}")
        reseal_for_attack(root)
    elif fault == "corpus":
        manifest = document(root / "inputs/manifest.json")
        edit_json(
            root,
            "inputs/" + manifest["model"]["learning_dataset"]["path"],
            lambda body: body.update(feature_code_hash="a" * 64),
        )
    elif fault == "age":
        edit_json(root, "run.json", lambda body: body["audit_policy"].update(model_max_age_days=0))
    elif fault == "label_time":
        manifest = document(root / "inputs/manifest.json")
        edit_json(
            root,
            "inputs/" + manifest["model"]["learning_dataset"]["path"],
            lambda body: body["samples"][-1].update(label_available_at="2030-01-01T00:00:00+00:00"),
        )
    else:
        edit_json(root, "run.json", lambda body: body["replay_model"].update(production_model_activated=True))
    blocked(root)


def test_missing_approved_gate_is_not_an_ml_policy_pass(completed_ml_replay, tmp_path):
    root = clone_bundle(completed_ml_replay, tmp_path / "run")

    def remove(rows):
        next(row["payload"] for row in rows if row["state"] == "approved").pop("model_gate")

    edit_lines(root, "signals.jsonl", remove)
    blocked(root, "bundle_approved_model_gate_missing")
