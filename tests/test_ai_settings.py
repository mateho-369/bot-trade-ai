import pytest
from pydantic import ValidationError

from tests.risk_helpers import config


@pytest.mark.parametrize(
    "name,value",
    [
        ("ai_max_response_bytes", True),
        ("ai_max_response_bytes", 1024),
        ("ai_max_output_tokens", 4096),
        ("ai_circuit_failures", 0),
        ("ai_suggestion_ttl_seconds", 0),
        ("model_min_probability", True),
        ("model_min_probability", 0.99),
        ("model_min_eval_auc", float("nan")),
        ("model_min_brier_skill", -1),
        ("model_random_seed", True),
        ("model_max_dataset_rows", 100),
        ("model_max_artifact_bytes", 1),
        ("model_max_age_days", 0),
        ("model_algorithm", "pickle"),
        ("model_min_labelled_trades", 10001),
        ("ollama_model", "bad\nmodel"),
        ("openai_model", "bad model"),
        ("openai_base_url", "https://example.com/v1?api_key=secret"),
        ("ollama_base_url", "http://localhost:11434?token=secret"),
        ("openai_base_url", "https://user:password@example.com/v1"),
        ("openai_base_url", "http://remote.example.com/v1"),
    ],
)
def test_ai_ml_config_limits_and_url_secrets_fail_closed(tmp_path, name, value):
    with pytest.raises(ValidationError):
        config(tmp_path, **{name: value})


def test_ai_policy_hashes_change_but_secrets_do_not_leak(tmp_path):
    cfg = config(tmp_path)
    changed = config(tmp_path, model_filter_enabled=True)
    assert (
        cfg.safety_fingerprint() != changed.safety_fingerprint()
        and cfg.strategy_fingerprint() != changed.strategy_fingerprint()
    )
    keyed = config(tmp_path, openai_api_key="TEST_HIDDEN_SECRET")
    assert (
        "TEST_HIDDEN_SECRET" not in str(keyed.public_config())
        and keyed.safety_fingerprint() == cfg.safety_fingerprint()
    )
    assert not cfg.model_filter_enabled and not cfg.auto_adapt_strategy_weights and not cfg.auto_reduce_risk
