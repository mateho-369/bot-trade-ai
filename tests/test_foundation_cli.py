import json

from main import main


def test_check_config_does_not_init_database(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text(f'PROJECT_ROOT="{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "paper" and output["startup"] == "paused"
    assert not (tmp_path / "data/reflexbot.db").exists()


def test_explicit_missing_env_file_fails(tmp_path):
    assert main(["check-config", "--env-file", str(tmp_path / "missing.env")]) == 2


def test_invalid_config_output_does_not_expose_input(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text('UNKNOWN_SETTING="not-an-id-secret"\n', encoding="utf-8")
    assert main(["check-config", "--env-file", str(path)]) == 2
    assert "not-an-id-secret" not in capsys.readouterr().err
