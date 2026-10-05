"""Actual offline CLIs over fixtures, never model provenance, provider/broker/owner qualification."""

import json

import pytest

from scripts.backtest import main as backtest_cli
from scripts.make_backtest_model_fixture import main as fixture_cli
from scripts.verify_replay_model import main as verify_cli
from tests.replay_model_helpers import changed_input


def research_env(tmp_path, **changes):
    path = tmp_path / "research.env"
    values = {
        "DEMO_MODE": "true",
        "LIVE_TRADING": "false",
        "PAPER_TRADING": "true",
        "MT5_BACKEND": "mock",
        "MODEL_FILTER_ENABLED": "true",
        **changes,
    }
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    return path


def test_model_fixture_cli_new_explicit_artificial_and_never_overwrites(tmp_path, capsys):
    root = tmp_path / "new"
    assert fixture_cli(["--output", str(root), "--warmup-minutes", "60"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["fixture_only"] and not result["production_model_activated"]
    assert not result["promotion_eligible"] and (root / "ARTIFICIAL_research.env").is_file()
    old = (root / "manifest.json").read_bytes()
    assert fixture_cli(["--output", str(root), "--warmup-minutes", "60"]) == 1
    assert (root / "manifest.json").read_bytes() == old


def test_verifier_cli_reconstructs_without_any_ledger_selection(small_model_input, tmp_path, capsys):
    env = research_env(tmp_path)
    assert (
        verify_cli(["--manifest", str(small_model_input.root / "manifest.json"), "--env-file", str(env)]) == 0
    )
    observed = json.loads(capsys.readouterr().out)
    assert observed["reconstructed_from_frozen_past_corpus"] and not observed["ledger_created"]
    assert observed["model_sha256"] == small_model_input.manifest.model.artifact.sha256
    assert not observed["genuine_owner_authenticated"] and not observed["production_model_activated"]
    assert not (small_model_input.root / "UNCREATED_VERIFICATION_CONTEXT").exists()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"MODEL_FILTER_ENABLED": "false"},
        {"MODEL_ALGORITHM": "lightgbm"},
        {"MODEL_MIN_PROBABILITY": "0.75"},
        {"LIVE_TRADING": "true", "PAPER_TRADING": "false"},
    ],
)
def test_verifier_changed_policy_refuses_without_mutation_or_content(
    small_model_input, tmp_path, capsys, change
):
    env = research_env(tmp_path, **change)
    assert (
        verify_cli(["--manifest", str(small_model_input.root / "manifest.json"), "--env-file", str(env)]) == 1
    )
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "refused" and not observed["ledger_created"]
    assert not (tmp_path / "data").exists()


def test_verifier_missing_explicit_env_refuses(small_model_input, tmp_path, capsys):
    assert (
        verify_cli(
            [
                "--manifest",
                str(small_model_input.root / "manifest.json"),
                "--env-file",
                str(tmp_path / "missing.env"),
            ]
        )
        == 1
    )
    assert "missing.env" not in capsys.readouterr().out


def test_backtest_cli_uses_model_only_explicitly_and_rejects_reuse(model_input, tmp_path, capsys):
    env = research_env(tmp_path)
    args = [
        "--manifest",
        str(model_input.root / "manifest.json"),
        "--env-file",
        str(env),
        "--output",
        str(tmp_path / "run"),
        "--review-mode",
        "synthetic_research",
        "--simulate-orders",
        "--close-at-end",
    ]
    assert backtest_cli(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["closed_trades"] == 1 and not result["promotion_eligible"]
    old = (tmp_path / "run/report.json").read_bytes()
    assert backtest_cli(args) == 1
    assert (tmp_path / "run/report.json").read_bytes() == old


def test_cli_changed_corpus_digest_never_fits_or_creates_ledger(small_model_input, tmp_path, capsys):
    env = research_env(tmp_path)
    attacked = changed_input(small_model_input, artifact=lambda body: body.update(dataset_sha256="b" * 64))
    root = tmp_path / "invalid"
    root.mkdir()
    for name, raw in attacked.files.items():
        (root / name).write_bytes(raw)
    assert (
        backtest_cli(
            [
                "--manifest",
                str(root / "manifest.json"),
                "--env-file",
                str(env),
                "--output",
                str(tmp_path / "never"),
            ]
        )
        == 1
    )
    assert not (tmp_path / "never").exists()
    assert not json.loads(capsys.readouterr().out)["promotion_eligible"]
