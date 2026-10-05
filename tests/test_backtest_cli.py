"""Actual offline CLIs. No native SDK/provider/Telegram request or legitimate credential is supplied."""

import hashlib

from sqlalchemy import select

from core.models import BotState, DeploymentEvidence
from scripts.backtest import main as backtest_cli
from scripts.make_backtest_fixture import main as fixture_cli
from scripts.stage_report import main as stage_cli
from scripts.synthetic_owner_fixtures import signed_fixture
from tests.backtest_helpers import fixture_path
from tests.test_backtest_promotion import sample as invented_stage  # noqa: F401 -- explicit pytest fixture


def test_fixture_cli_declares_artificial_and_never_overwrites(tmp_path, capsys):
    assert fixture_cli(["--output", str(tmp_path / "fixture"), "--warmup-minutes", "60"]) == 0
    assert '"synthetic_only": true' in capsys.readouterr().out
    before = (tmp_path / "fixture/manifest.json").read_bytes()
    assert fixture_cli(["--output", str(tmp_path / "fixture"), "--warmup-minutes", "60"]) == 1
    assert (tmp_path / "fixture/manifest.json").read_bytes() == before


def test_replay_cli_runs_no_credential_file_and_refuses_existing_output(tmp_path, capsys):
    path = fixture_path(tmp_path)
    arguments = ["--manifest", str(path), "--output", str(tmp_path / "run"), "--max-events", "1000"]
    assert backtest_cli(arguments) == 0
    assert '"promotion_eligible": false' in capsys.readouterr().out
    assert (tmp_path / "run/report.json").is_file()
    assert backtest_cli(arguments) == 1
    assert (tmp_path / "run/report.json").is_file()


def test_cli_bad_environment_does_not_initialize_or_copy_native_state(tmp_path, capsys):
    path = fixture_path(tmp_path)
    assert (
        backtest_cli(
            [
                "--manifest",
                str(path),
                "--output",
                str(tmp_path / "no"),
                "--env-file",
                str(tmp_path / "not-present.env"),
            ]
        )
        == 1
    )
    assert not (tmp_path / "no").exists()
    assert "not-present" not in capsys.readouterr().out


def test_stage_cli_requires_existing_schema_and_never_initializes_it(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("DEMO_MODE=true\nLIVE_TRADING=false\nPAPER_TRADING=true\nMT5_BACKEND=mock\n")
    assert (
        stage_cli(
            [
                "--env-file",
                str(env),
                "export",
                "--stage",
                "paper",
                "--account-key",
                "TEST",
                "--from",
                "2024-01-01T00:00:00Z",
                "--until",
                "2024-01-15T00:00:00Z",
                "--output",
                "not-created.json",
            ]
        )
        == 1
    )
    assert not (tmp_path / "not-created.json").exists()
    assert not (tmp_path / "data").exists()  # No empty DB/parent is created to probe a nonexistent schema.
    assert '"grants_live": false' in capsys.readouterr().out


def test_stage_cli_accepts_only_signed_fixture_digest_as_local_software_test(request, monkeypatch, capsys):
    # Artificial file/native flags/bearer. This is NOT actual owner/stage qualification.
    cfg, db, clock, _, path = request.getfixturevalue("invented_stage")
    env = cfg.project_root / ".env"
    env.write_text(
        "DEMO_MODE=true\nLIVE_TRADING=false\nPAPER_TRADING=true\nMT5_BACKEND=real\n"
        "SYMBOLS=EURUSD\nTELEGRAM_OWNER_ID=42\n"
        "TELEGRAM_BOT_TOKEN=123456789:TEST_ONLY_NEVER_CONTACT_TELEGRAM\n"
        "MAX_SLIPPAGE_POINTS=2\nATR_TRAILING_ENABLED=false\n"
    )
    signed = cfg.project_root / "TEST_ONLY_OWNER.initdata"
    signed.write_text(signed_fixture(cfg, clock) + "\n")
    monkeypatch.setattr("scripts.stage_report.SystemClock", lambda: clock)
    assert (
        stage_cli(
            [
                "--env-file",
                str(env),
                "import",
                "--report",
                str(path),
                "--confirm-sha256",
                hashlib.sha256(path.read_bytes()).hexdigest(),
                "--owner-initdata-file",
                str(signed),
            ]
        )
        == 0
    )
    assert '"grants_live": false' in capsys.readouterr().out
    with db.session() as session:
        assert session.scalar(select(DeploymentEvidence)) is not None
        state = session.get(BotState, 1)
        assert state.desired_state == "paused" and state.session_id is None
