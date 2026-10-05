"""Immutable ML evidence consistency; no fitting, registry activation or LightGBM/native-library import."""

from __future__ import annotations

import math
from datetime import timedelta

from ai.feature_engineering import FEATURE_NAMES, FEATURE_SCHEMA_HASH, FeatureEngineering
from ai.replay_binding import BINDING_KEYS
from backtesting.audit.contracts import digest, object_json, sha, stamp, valid_hash
from backtesting.audit.ledger import sql_json
from backtesting.audit.semantics import check, numeric
from core.settings import TIMEFRAME_MINUTES


class ModelObservation:
    def __init__(self, bundle, manifest, run, ledger):
        self.payload = self.binding = None
        self.checked_logistic, self.not_reexecuted_lightgbm = 0, 0
        selection = manifest.model
        rows = ledger["models"]
        if selection is None:
            check(not rows and run["replay_model"] is None, "bundle_unselected_model_present")
            check(
                not any(path.startswith("data/models/") for path in bundle.files),
                "bundle_unselected_model_present",
            )
            return
        check(
            run["audit_policy"]["model_filter_enabled"] and len(rows) == 1, "bundle_model_selection_missing"
        )
        row = rows[0]
        name = "data/models/" + selection.artifact.sha256 + ".json"
        raw = bundle.files["inputs/" + selection.artifact.path]
        check(
            bundle.files[name] == raw
            and sha(raw) == row["artifact_sha256"] == run["model_sha256"]
            and row["active"] == 1
            and row["deployment_scope"] == "backtest"
            and row["config_hash"] == run["strategy_config_hash"],
            "bundle_private_model_snapshot_mismatch",
        )
        check(
            {path for path in bundle.files if path.startswith("data/models/")} == {name},
            "bundle_model_snapshot_ambiguity",
        )
        payload = object_json(raw, limit=4194304)
        metadata = sql_json(row["metrics_json"])
        binding = metadata["replay_binding"]
        corpus = object_json(bundle.files["inputs/" + selection.learning_dataset.path], limit=16777216)
        check(
            set(binding) == BINDING_KEYS
            and binding["format"] == "reflex-replay-model-binding-v1"
            and binding["source"] == "historical"
            and binding["purpose"] == "research_only"
            and binding["promotion_eligible"] is False
            and binding["genuine_provenance_verified"] is False
            and binding["reconstruction"] == "exact_current_trainer"
            and binding["selection"] == selection.model_dump(mode="json")
            and digest(binding) == run["replay_model"]["binding_sha256"],
            "bundle_model_binding_mismatch",
        )
        for key in BINDING_KEYS:
            if key.endswith(("_hash", "_sha256")):
                valid_hash(binding[key])
        check(
            payload["format"] == "reflex-model-v1"
            and payload["source"] == corpus["source"] == "historical"
            and payload["code_hash"]
            == payload["feature_origin_code_hash"]
            == corpus["feature_code_hash"]
            == binding["code_hash"]
            == run["code_hash"]
            and payload["policy_hash"]
            == corpus["policy_hash"]
            == binding["policy_hash"]
            == run["strategy_config_hash"]
            and payload["feature_schema_hash"]
            == corpus["feature_schema_hash"]
            == binding["feature_schema_hash"]
            == FEATURE_SCHEMA_HASH
            and payload["dataset_sha256"] == binding["corpus_sha256"] == digest(corpus)
            and binding["dataset_sha256"] == run["dataset_sha256"]
            and binding["manifest_sha256"] == sha(bundle.files["inputs/" + run["input_manifest"]])
            and binding["model_sha256"] == run["model_sha256"]
            and binding["evaluation_sha256"] == digest(payload["evaluation"])
            and payload["evaluation"]["passed"] is True
            and payload["evaluation"] == metadata["evaluation"]
            and payload["origin"] == corpus["origin"] == binding["origin"],
            "bundle_model_corpus_identity_mismatch",
        )
        check(
            corpus["origin"] != "fixture" or manifest.origin.kind == "synthetic_fixture",
            "bundle_fixture_model_origin_disguised",
        )
        samples = corpus["samples"]
        check(
            type(payload["sample_count"]) is int
            and 1 <= len(samples) <= 5000
            and payload["sample_count"] == binding["sample_count"] == len(samples),
            "bundle_model_sample_count",
        )
        latest, horizon = None, None
        policy = run["audit_policy"]
        length = timedelta(
            minutes=TIMEFRAME_MINUTES[policy["primary_timeframe"]] * policy["model_label_horizon_bars"]
        )
        for sample in samples:
            times = [
                stamp(sample[key]) for key in ("decision_at", "entry_at", "exit_at", "label_available_at")
            ]
            check(times[0] <= times[1] < times[2] <= times[3], "bundle_model_label_chronology")
            latest = max(latest, times[3]) if latest else times[3]
            bound = max(times[2], times[0] + length)
            horizon = max(horizon, bound) if horizon else bound
        embargo = timedelta(
            minutes=TIMEFRAME_MINUTES[policy["primary_timeframe"]] * policy["model_embargo_bars"]
        )
        check(
            latest
            == stamp(payload["trained_through"])
            <= stamp(corpus["exported_at"])
            == stamp(binding["exported_at"])
            <= stamp(payload["created_at"])
            <= selection.available_at
            <= selection.selected_at
            <= manifest.replay_from
            and latest < manifest.replay_from - embargo
            and horizon == stamp(binding["label_horizon_through"]) < manifest.replay_from
            and stamp(binding["replay_from"]) == manifest.replay_from
            and stamp(binding["replay_until"]) == manifest.replay_until,
            "bundle_model_time_binding_mismatch",
        )
        check(
            run["replay_model"]["production_model_activated"] is False
            and run["replay_model"]["promotion_eligible"] is False,
            "bundle_model_privilege_claim",
        )
        self.payload, self.binding = payload, binding

    def gate(self, payload, *, when, approved, policy):
        gate = payload.get("model_gate")
        if not policy["model_filter_enabled"]:
            check(gate is None, "bundle_disabled_policy_has_model_gate")
            return
        if gate is None:
            check(not approved, "bundle_approved_model_gate_missing")
            return
        check(self.payload is not None, "bundle_gate_without_selected_model")
        check(
            set(gate)
            == {
                "format",
                "proposal_hash",
                "model_sha256",
                "feature_schema_hash",
                "feature_vector_sha256",
                "replay_binding_sha256",
                "source",
                "code_hash",
                "policy_hash",
                "config_hash",
                "threshold",
                "probability",
                "accepted",
            }
            and gate["format"] == "reflex-replay-model-gate-v1"
            and gate["source"] == "historical"
            and gate["proposal_hash"] == payload["proposal_hash"]
            and gate["model_sha256"] == payload["model_sha256"] == self.binding["model_sha256"]
            and gate["feature_schema_hash"] == FEATURE_SCHEMA_HASH
            and gate["replay_binding_sha256"] == digest(self.binding)
            and gate["code_hash"] == payload["code_hash"] == self.binding["code_hash"]
            and gate["policy_hash"] == payload["strategy_config_hash"] == self.binding["policy_hash"]
            and gate["config_hash"] == payload["config_hash"]
            and gate["threshold"] == policy["model_min_probability"],
            "bundle_model_gate_binding_mismatch",
        )
        numeric(gate["probability"], 0, 1)
        check(
            type(gate["accepted"]) is bool
            and gate["accepted"] == (gate["probability"] >= gate["threshold"])
            and (not approved or gate["accepted"]),
            "bundle_model_gate_probability_threshold",
        )
        check(digest(gate) == payload["model_gate_digest"], "bundle_model_gate_digest_mismatch")
        vector = FeatureEngineering.from_snapshot(payload["feature_snapshot"], payload["technical"])
        check(digest(vector.to_dict()) == gate["feature_vector_sha256"], "bundle_model_vector_mismatch")
        check(
            stamp(self.binding["replay_from"]) <= when <= stamp(self.binding["replay_until"])
            and when - stamp(self.payload["trained_through"]) <= timedelta(days=policy["model_max_age_days"]),
            "bundle_model_observation_time_invalid",
        )
        model = self.payload["model"]
        if model["algorithm"] == "logistic":
            # Cross-check portable numeric JSON with stdlib math, not numpy/sklearn/foreign libraries.
            check(
                set(model) == {"algorithm", "mean", "scale", "coefficients", "intercept"},
                "bundle_logistic_contract",
            )
            for key in ("mean", "scale", "coefficients"):
                check(
                    isinstance(model[key], list) and len(model[key]) == len(FEATURE_NAMES),
                    "bundle_logistic_vector_bounds",
                )
                for value in model[key]:
                    numeric(value, -1e6, 1e6)
                if key == "scale":
                    check(all(value > 0 for value in model[key]), "bundle_logistic_scale_invalid")
            numeric(model["intercept"], -1e6, 1e6)
            z = math.fsum(
                (value - mean) / scale * coefficient
                for value, mean, scale, coefficient in zip(
                    vector.values, model["mean"], model["scale"], model["coefficients"], strict=True
                )
            )
            z = min(700, max(-700, z + model["intercept"]))
            probability = 1 / (1 + math.exp(-z))
            check(abs(probability - gate["probability"]) <= 1e-12, "bundle_logistic_probability_mismatch")
            self.checked_logistic += 1
        else:
            check(model["algorithm"] == "lightgbm", "bundle_model_algorithm_invalid")
            import re

            check(set(model) == {"algorithm", "model_text", "library_version"}, "bundle_lightgbm_contract")
            text = model["model_text"]
            check(
                isinstance(text, str)
                and 1 <= len(text.encode()) <= 1048576
                and "\0" not in text
                and text.startswith("tree\n")
                and 1 <= text.count("\nTree=") <= 200
                and "max_feature_idx=31\n" in text
                and "feature_names=" + " ".join(FEATURE_NAMES) + "\n" in text
                and re.search(r"^objective=binary(?:\s|$)", text, re.M) is not None,
                "bundle_lightgbm_text_bounds",
            )
            leaves = re.findall(r"^num_leaves=(\d+)$", text, re.M)
            check(
                len(leaves) == text.count("\nTree=") and all(1 <= int(value) <= 31 for value in leaves),
                "bundle_lightgbm_tree_bounds",
            )
            self.not_reexecuted_lightgbm += 1  # Explicitly no foreign library/DLL loading in this auditor.
