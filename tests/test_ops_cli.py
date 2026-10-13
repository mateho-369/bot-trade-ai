"""Local ops CLI exercises typed transitions without a server or interactive prompts."""

import asyncio
import json
import socket
from datetime import datetime, timezone

from ai.suggestion_store import SuggestionStore
from app.process_guard import operator_stop_path, operator_stop_requested
from core.models import AISuggestion, BotState
from core.settings import Settings
from scripts.ops import main
from tests.risk_helpers import make_engine
from trading.types import SystemClock


def _forbid_listener(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("local ops must not open a network listener")

    monkeypatch.setattr(socket.socket, "bind", denied)
    monkeypatch.setattr(socket.socket, "listen", denied)


def _environment(root):
    path = root / ".env"
    path.write_text(
        "\n".join(
            (
                f"PROJECT_ROOT={root.as_posix()}",
                "SYMBOLS=EURUSD",
                "MAX_SLIPPAGE_POINTS=2",
                "ATR_TRAILING_ENABLED=false",
                "AUTONOMOUS_DEMO=true",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _health(settings, session_id):
    path = settings.resolve_path(settings.runtime_health_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "ready",
                "session_id": session_id,
                "config_hash": settings.safety_fingerprint(),
                "reconciled": True,
                "components_ready": True,
                "writes_quarantined": False,
                "ai_healthy": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_status_pause_and_resume_use_durable_local_transitions_without_listener(
    tmp_path, monkeypatch, capsys
):
    _forbid_listener(monkeypatch)
    env_file = _environment(tmp_path)
    engine = asyncio.run(
        make_engine(
            tmp_path,
            clock=SystemClock(),
            autonomous_demo=True,
            symbols=("EURUSD",),
            max_slippage_points=2,
            atr_trailing_enabled=False,
        )
    )
    settings = engine.settings
    try:
        assert settings.safety_fingerprint() == Settings(_env_file=env_file).safety_fingerprint()
        _health(settings, engine.control.session_id)

        assert main(["--env-file", str(env_file), "status"]) == 0
        status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert status["desired_state"] == "paused" and status["health"] == "ready"

        assert main(["--env-file", str(env_file), "resume"]) == 0
        resumed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert resumed == {"command": "resume", "status": "ok"}
        assert engine.database.status()["state"] == "running"

        assert main(["--env-file", str(env_file), "pause"]) == 0
        paused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert paused == {"command": "pause", "status": "ok"}
        with engine.database.session() as session:
            state = session.get(BotState, 1)
            assert state.desired_state == "paused" and not state.kill_switch_active
    finally:
        asyncio.run(engine.shutdown())
        engine.database.close()


def test_stop_marker_is_persistent_and_clear_only_removes_stop_request(tmp_path, monkeypatch, capsys):
    _forbid_listener(monkeypatch)
    env_file = _environment(tmp_path)
    settings = Settings(_env_file=env_file, project_root=tmp_path)
    path = operator_stop_path(settings)
    assert not operator_stop_requested(settings)

    assert main(["--env-file", str(env_file), "stop"]) == 0
    stop_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert stop_result == {"force_killed": False, "status": "stop_requested"}
    assert path.is_file() and operator_stop_requested(settings)

    assert main(["--env-file", str(env_file), "clear-stop"]) == 0
    cleared = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert cleared == {"runtime_starts_paused": True, "status": "cleared_only"}
    assert path.is_file() and not operator_stop_requested(settings)


def test_ops_refuses_live_configuration_with_sanitized_output(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text("LIVE_TRADING=true\nOPENAI_API_KEY=TEST_ONLY_SECRET\n", encoding="utf-8")
    assert main(["--env-file", str(path), "status"]) == 2
    output = capsys.readouterr().err
    assert "TradingDisabled" in output and "TEST_ONLY_SECRET" not in output


def test_proposal_review_cli_is_local_hash_fenced_and_never_applies_settings(tmp_path, monkeypatch, capsys):
    _forbid_listener(monkeypatch)
    env_file = _environment(tmp_path)
    engine = asyncio.run(
        make_engine(
            tmp_path,
            clock=SystemClock(),
            autonomous_demo=True,
            symbols=("EURUSD",),
            max_slippage_points=2,
            atr_trailing_enabled=False,
        )
    )
    try:
        store = SuggestionStore(engine.database, engine.settings, engine.clock, engine.profile)
        proposal = store.create(
            "reduce_risk",
            {"risk_percent": "0.2"},
            reason="Review the bounded risk reduction.",
            request_hash="a" * 64,
        )
        args = ["--env-file", str(env_file)]
        assert main([*args, "proposals", "--status", "pending"]) == 0
        listing = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert listing["proposals"][0]["suggestion_id"] == proposal.suggestion_id
        assert listing["read_only"]

        assert main([*args, "proposal-show", str(proposal.suggestion_id)]) == 0
        review = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert review["parameters"] == {"risk_percent": "0.2"}
        confirmation = review["approve_confirmation"]

        assert (
            main(
                [
                    *args,
                    "proposal-decide",
                    str(proposal.suggestion_id),
                    "--decision",
                    "approve",
                    "--confirm",
                    "incorrect-confirmation",
                ]
            )
            == 2
        )
        refusal = json.loads(capsys.readouterr().err)
        assert refusal["status"] == "refused" and not refusal["raw_error_printed"]
        assert store.get(proposal.suggestion_id).status == "pending"

        assert (
            main(
                [
                    *args,
                    "proposal-decide",
                    str(proposal.suggestion_id),
                    "--decision",
                    "approve",
                    "--confirm",
                    confirmation,
                ]
            )
            == 0
        )
        decided = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert decided == {
            "settings_applied": False,
            "status": "approved",
            "suggestion_id": proposal.suggestion_id,
            "trade_executed": False,
        }
        with engine.database.session() as session:
            row = session.get(AISuggestion, proposal.suggestion_id)
            state = session.get(BotState, 1)
            assert row.status == "approved" and not state.settings_overrides
    finally:
        asyncio.run(engine.shutdown())
        engine.database.close()
