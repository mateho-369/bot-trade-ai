"""Windows launchers select a usable interpreter and keep failure diagnostics visible."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def batch_text(path: Path) -> str:
    raw = path.read_bytes()
    assert raw.isascii()
    assert raw.count(b"\r\n") == raw.count(b"\n")  # CRLF is preserved by .gitattributes.
    return raw.decode("ascii").replace("\r\n", "\n")


def section(text: str, label: str, next_label: str) -> str:
    start = text.index(f"\n:{label}\n") + 1
    end = text.index(f"\n:{next_label}\n", start)
    return text[start:end]


def test_installer_probes_supported_x64_python_newest_first_and_pauses_failures():
    text = batch_text(ROOT / "install.bat")
    probes = [text.index(f"call :try_python {version}") for version in ("3.14", "3.13", "3.12", "3.11")]
    assert probes == sorted(probes)
    assert "py -%PYTHON_VERSION% -m venv .venv" in text
    assert "struct.calcsize('P') == 8" in text
    assert "No supported 64-bit CPython runtime was found" in text
    assert "py -3.11 -m venv .venv" not in text

    no_launcher = section(text, "nopython", "unsupportedpython")
    assert 'Python launcher "py" was not found' in no_launcher
    assert "goto :fail" not in no_launcher
    assert "pause\nexit /b 1" in no_launcher
    unsupported = section(text, "unsupportedpython", "fail")
    assert "goto :fail" in unsupported
    failure = section(text, "fail", "try_python")
    assert failure.index("pause") < failure.index("exit /b 1")


def test_start_batch_pauses_all_error_paths_and_nonzero_runtime_exit():
    text = batch_text(ROOT / "start.bat")
    for label, next_label, exit_line in (
        ("noinstall", "stopped", "exit /b 2"),
        ("stopped", "fail", "exit /b 2"),
        ("fail", None, "exit /b 2"),
    ):
        block = section(text, label, next_label) if next_label else text.split(f"\n:{label}\n", 1)[1]
        assert block.index("pause") < block.index(exit_line)
    assert 'if not "%CODE%"=="0" pause' in text
