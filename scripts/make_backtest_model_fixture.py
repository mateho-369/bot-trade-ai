"""Generate a NEW explicitly artificial model+corpus+history input, never a production registry."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from ai.model_trainer import ModelTrainer
from backtesting.artifacts import write_json
from backtesting.backtester import ReplaySettings, isolated_settings
from backtesting.dataset import HistoricalDataset
from core.security import canonical_json
from scripts.make_backtest_fixture import make_fixture
from scripts.synthetic_replay_model import artificial_corpus
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind


def make_model_fixture(
    output,
    *,
    warmup_minutes=12240,
    replay_minutes=2,
    quote_mode="ticks",
    algorithm="logistic",
    relationship="quality",
    settings=None,
):
    base = (
        settings
        if settings is not None
        else ReplaySettings(model_filter_enabled=True, model_algorithm=algorithm)
    )
    if not base.model_filter_enabled or base.live_trading:
        raise ValueError("explicit enabled ML and nonlive research policy required")
    manifest = make_fixture(
        output, warmup_minutes=warmup_minutes, replay_minutes=replay_minutes, quote_mode=quote_mode
    )
    history = HistoricalDataset.load(manifest)
    cfg = isolated_settings(base, history, manifest.parent)
    profile = RuntimeProfile.current(cfg, SourceKind.HISTORICAL)
    corpus = artificial_corpus(cfg, profile, history.manifest.replay_from, relationship=relationship)
    created = corpus.exported_at + timedelta(minutes=1)
    artifact = ModelTrainer(cfg, profile).train(corpus, as_of=created)
    if artifact.payload()["evaluation"]["passed"] is not True:
        raise ValueError("engineered fixture does not qualify its own fixed OOS structural gate; no fallback")
    corpus_raw, artifact_raw = canonical_json(corpus.to_dict()).encode(), artifact.artifact_json.encode()
    (manifest.parent / "ARTIFICIAL_learning.json").write_bytes(corpus_raw)
    (manifest.parent / "ARTIFICIAL_model.json").write_bytes(artifact_raw)
    body = json.loads(manifest.read_bytes())
    body["model"] = {
        "format": "reflex-replay-model-selection-v1",
        "purpose": "research_only",
        "description": "ARTIFICIAL model/labels; NOT actual historical availability or predictive skill",
        "available_at": (created + timedelta(minutes=1)).isoformat(),
        "selected_at": (created + timedelta(minutes=2)).isoformat(),
        "artifact": {"path": "ARTIFICIAL_model.json", "sha256": hashlib.sha256(artifact_raw).hexdigest()},
        "learning_dataset": {
            "path": "ARTIFICIAL_learning.json",
            "sha256": hashlib.sha256(corpus_raw).hexdigest(),
        },
    }
    # Only the NEW fixture manifest is rewritten, never caller financial/model state.
    manifest.write_text(json.dumps(body, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_json(
        manifest.parent / "ARTIFICIAL_NOTICE.json",
        {
            "fixture_only": True,
            "engineered_relationship": relationship,
            "model_sha256": artifact.digest,
            "genuine_model_provenance_verified": False,
            "historical_availability_verified": False,
            "predictive_skill_demonstrated": False,
            "promotion_eligible": False,
        },
    )
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="ARTIFICIAL research ML fixture; never stage evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-minutes", type=int, default=12240)
    parser.add_argument("--replay-minutes", type=int, default=2)
    parser.add_argument("--quote-mode", choices=("ticks", "ohlc_conservative"), default="ticks")
    parser.add_argument("--algorithm", choices=("logistic", "lightgbm"), default="logistic")
    parser.add_argument("--relationship", choices=("quality", "inverse_quality"), default="quality")
    args = parser.parse_args(argv)
    try:
        manifest = make_model_fixture(
            args.output,
            warmup_minutes=args.warmup_minutes,
            replay_minutes=args.replay_minutes,
            quote_mode=args.quote_mode,
            algorithm=args.algorithm,
            relationship=args.relationship,
        )
        # Explicit file for the ordinary backtest CLI; no credentials or production registry paths.
        env_file = manifest.parent / "ARTIFICIAL_research.env"
        with env_file.open("x", encoding="utf-8") as stream:
            stream.write(
                "# ARTIFICIAL engineering replay only; NOT a production environment\n"
                "DEMO_MODE=true\nLIVE_TRADING=false\nPAPER_TRADING=true\nMT5_BACKEND=mock\n"
                f"MODEL_FILTER_ENABLED=true\nMODEL_ALGORITHM={args.algorithm}\n"
            )
        print(
            json.dumps(
                {
                    "manifest": str(manifest),
                    "env_file": str(env_file),
                    "fixture_only": True,
                    "production_model_activated": False,
                    "promotion_eligible": False,
                }
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps({"status": "refused", "error_kind": type(exc).__name__, "promotion_eligible": False})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
