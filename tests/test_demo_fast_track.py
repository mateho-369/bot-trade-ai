"""DEMO_FAST_TRACK: DEMO broker orders without stage evidence ONLY on a terminal-verified DEMO account.

Proves it fails closed for REAL, CONTEST/unknown account types, non-MT5 provenance, PAPER and LIVE,
that fast-track trades are never promotion evidence, and that demo sizing is the broker minimum lot.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.database import Database
from core.settings import Settings
from tests.risk_helpers import MOMENT, D, config, make_engine
from trading.risk_types import RuntimeProfile
from trading.stage_gate import StageGate
from trading.types import AccountInfo, AccountKind, ManualClock, Side, SourceKind, TradingDisabled

MT5 = RuntimeProfile("c" * 64, "d" * 64, SourceKind.MT5)


def account(kind=AccountKind.DEMO, source=SourceKind.MT5):
    return AccountInfo(7, "Exness-MT5Trial", "USD", kind, source, D("1000"), D("1000"), D("0"), D("1000"))


def demo_settings(tmp_path, **changes):
    values = dict(paper_trading=False, mt5_backend="real", demo_fast_track=True)
    values.update(changes)
    return config(tmp_path, **values)


@pytest.fixture
def gate_for(tmp_path):
    databases = []

    def build(settings, profile=MT5):
        database = Database(settings)
        database.initialize()
        databases.append(database)
        return StageGate(database, settings, ManualClock(MOMENT), profile), database

    yield build
    for database in databases:
        database.close()


def test_defaults_keep_the_fast_track_off(tmp_path):
    cfg = config(tmp_path)
    assert cfg.demo_fast_track is False and cfg.news_unavailable_policy == "block"
    assert cfg.demo_min_lot_only is False


def test_demo_terminal_account_is_the_only_eligible_case(tmp_path, gate_for):
    gate, database = gate_for(demo_settings(tmp_path))
    assert gate.demo_fast_track_eligible(account())
    with database.session() as session:
        evidence, fast = gate.entry_evidence(session, account())
    assert evidence == () and fast is True


@pytest.mark.parametrize(
    "kind",
    [AccountKind.REAL, AccountKind.CONTEST, AccountKind.SIMULATED],
    ids=["real", "contest", "simulated"],
)
def test_real_or_unknown_account_types_fail_closed(tmp_path, gate_for, kind):
    gate, database = gate_for(demo_settings(tmp_path))
    assert not gate.demo_fast_track_eligible(account(kind))
    with database.session() as session:
        with pytest.raises(TradingDisabled):
            gate.entry_evidence(session, account(kind))


def test_unknown_terminal_account_mode_never_reaches_the_gate(tmp_path):
    """The native client refuses an unknown trade_mode before any account object exists."""
    from trading.mt5_client import MT5Client
    from trading.types import ConnectionUnavailable

    raw = SimpleNamespace(trade_mode=7, login=1, server="x", currency="USD")
    client = MT5Client.__new__(MT5Client)
    client._api = SimpleNamespace(account_info=lambda: raw)
    with pytest.raises(ConnectionUnavailable, match="unknown broker account mode"):
        client._account_sync()


@pytest.mark.parametrize("source", [SourceKind.PAPER, SourceKind.SYNTHETIC, SourceKind.TEST_SDK])
def test_non_terminal_account_provenance_fails_closed(tmp_path, gate_for, source):
    gate, _ = gate_for(demo_settings(tmp_path))
    assert not gate.demo_fast_track_eligible(account(source=source))


@pytest.mark.parametrize("source", [SourceKind.SYNTHETIC, SourceKind.TEST_SDK, SourceKind.PAPER])
def test_non_mt5_runtime_profile_fails_closed(tmp_path, gate_for, source):
    gate, _ = gate_for(demo_settings(tmp_path), replace(MT5, data_source=source))
    assert not gate.demo_fast_track_eligible(account())


def test_flag_off_or_paper_mode_fails_closed(tmp_path, gate_for):
    off, database = gate_for(demo_settings(tmp_path / "off", demo_fast_track=False))
    assert not off.demo_fast_track_eligible(account())
    with database.session() as session:
        with pytest.raises(TradingDisabled):
            off.entry_evidence(session, account())
    paper, _ = gate_for(config(tmp_path / "paper", demo_fast_track=True))
    assert not paper.demo_fast_track_eligible(account())


def test_live_mode_cannot_be_configured_with_the_fast_track(tmp_path):
    with pytest.raises(ValidationError, match="DEMO_FAST_TRACK is impossible with LIVE_TRADING"):
        config(tmp_path, live_trading=True, paper_trading=False, mt5_backend="real", demo_fast_track=True)
    with pytest.raises(ValidationError, match="min_lot_demo is DEMO-only"):
        config(
            tmp_path,
            live_trading=True,
            paper_trading=False,
            mt5_backend="real",
            news_unavailable_policy="min_lot_demo",
        )


def test_live_mode_fails_closed_even_if_validation_were_bypassed(tmp_path, gate_for):
    live = Settings.model_construct(
        **{**demo_settings(tmp_path).model_dump(), "live_trading": True, "demo_fast_track": True}
    )
    gate, database = gate_for(demo_settings(tmp_path))
    gate.settings = live
    for kind in (AccountKind.DEMO, AccountKind.REAL):
        assert not gate.demo_fast_track_eligible(account(kind))
        with database.session() as session:
            with pytest.raises(TradingDisabled):
                gate.entry_evidence(session, account(kind))


def test_fast_track_trades_are_never_promotion_evidence(tmp_path, gate_for):
    gate, _ = gate_for(demo_settings(tmp_path))
    intent = SimpleNamespace(id=1, state="reconciled", request={"demo_fast_track": True, "authorized": True})
    calls = iter([[intent], []])

    class Session:
        def scalars(self, _query):
            return SimpleNamespace(all=lambda: next(calls))

    evidence = SimpleNamespace(account_key="mt5:demo", started_at=MOMENT, finished_at=MOMENT)
    trade = SimpleNamespace(order_intent_id=1, features_json={"execution": {"entry_deal_tickets": []}})
    with pytest.raises(TradingDisabled, match="not promotion evidence"):
        gate._verify_trade_proofs(Session(), evidence, "demo", [trade])


def test_demo_min_lot_scope(tmp_path):
    assert demo_settings(tmp_path).demo_min_lot_only
    news_only = demo_settings(tmp_path, demo_fast_track=False, news_unavailable_policy="min_lot_demo")
    assert news_only.demo_min_lot_only
    assert not demo_settings(tmp_path, demo_fast_track=False).demo_min_lot_only
    assert not config(tmp_path, news_unavailable_policy="min_lot_demo").demo_min_lot_only  # PAPER


async def test_demo_sizing_is_capped_at_the_broker_minimum_lot(tmp_path, monkeypatch):
    engine = await make_engine(tmp_path, max_risk_percent_per_trade=D("1.0"))
    try:
        meta = await engine.broker.get_symbol_info("EURUSD")
        normal = await engine.calculator.calculate_lot_size("EURUSD", Side.BUY, D("1.10000"), D("1.09950"))
        assert normal.volume > meta.volume_min  # Without the cap the budget allows more.
        monkeypatch.setattr(Settings, "demo_min_lot_only", property(lambda self: True))
        capped = await engine.calculator.calculate_lot_size("EURUSD", Side.BUY, D("1.10000"), D("1.09950"))
        assert capped.volume == meta.volume_min
    finally:
        await engine.shutdown()
        engine.database.close()


# -- preflight: the owner-approved demo stage is the ONLY accepted PAPER_TRADING=false -------------
def _env_findings(tmp_path, **changes):
    from readiness.preflight_checks import REQUIRED_KEYS, inspect_env_keys

    values = {name: "SENTINEL_VALUE" for name in REQUIRED_KEYS}
    values.update(
        LIVE_TRADING="false",
        PAPER_TRADING="true",
        START_PAUSED="true",
        DEMO_MODE="true",
        BACKTEST_MODE="false",
        AUTONOMOUS_DEMO="false",
        MT5_BACKEND="real",
    )
    values.update(changes)
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    findings, _ = inspect_env_keys(tmp_path, env_name=".env")
    return {f.code: f.status for f in findings}


def test_preflight_accepts_paper_off_only_for_a_paused_non_live_demo_stage(tmp_path):
    found = _env_findings(tmp_path, PAPER_TRADING="false", AUTONOMOUS_DEMO="true")
    assert found["demo_broker_stage"] == "warning"
    assert "safe_default_changed_in_file" not in found


@pytest.mark.parametrize(
    "changes",
    [
        {"PAPER_TRADING": "false"},  # no fast-track opt-in
        {"PAPER_TRADING": "false", "DEMO_FAST_TRACK": "true", "LIVE_TRADING": "true"},
        {"PAPER_TRADING": "false", "DEMO_FAST_TRACK": "true", "START_PAUSED": "false"},
        {"PAPER_TRADING": "false", "DEMO_FAST_TRACK": "true", "DEMO_MODE": "false"},
    ],
    ids=["no-opt-in", "live", "not-paused", "not-demo"],
)
def test_preflight_still_blocks_everything_else(tmp_path, changes):
    assert _env_findings(tmp_path, **changes)["safe_default_changed_in_file"] == "blocked"


# -- shipped demo deliverables ---------------------------------------------------------------------
ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_demo_env_template_is_valid_safe_and_secret_free(tmp_path):
    from news.evidence import configured_source_ids

    text = (ROOT / ".env.demo.example").read_text(encoding="utf-8")
    filled = text.replace(
        "TELEGRAM_BOT_TOKEN=\n", "TELEGRAM_BOT_TOKEN=123456789:TEST_ONLY_NEVER_CONTACT_TELEGRAM_xx\n"
    ).replace("TELEGRAM_REPORT_CHAT_ID=\n", "TELEGRAM_REPORT_CHAT_ID=123456789\n")
    (tmp_path / ".env").write_text(filled, encoding="utf-8")
    cfg = Settings(_env_file=tmp_path / ".env", project_root=tmp_path)
    assert cfg.mode.value == "demo" and not cfg.live_trading and cfg.start_paused
    assert not cfg.demo_fast_track and not cfg.demo_min_lot_only and cfg.news_unavailable_policy == "block"
    assert cfg.autonomous_demo and cfg.ai_require_approval and cfg.start_paused
    assert cfg.ai_require_approval and not cfg.ai_rule_fallback_enabled
    assert [e.label for e in cfg.ai_registry()] == ["groq"]  # Single Groq key = one registry entry.
    assert set(configured_source_ids(cfg)) == set(cfg.news_source_coverage)  # Reviewed default feed.
    assert cfg.calendar_provider == "faireconomy" and cfg.calendar_faireconomy_reviewed
    for key in ("OPENAI_API_KEY", "MT5_PASSWORD", "TELEGRAM_BOT_TOKEN"):
        assert f"\n{key}=\n" in text  # Template never ships a credential.


def test_setup_demo_script_order_and_guards():
    raw = (ROOT / "scripts" / "setup_demo.ps1").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and raw.isascii()  # CRLF, ASCII for PowerShell 5.1.
    text = raw.decode()
    order = [
        "venv",
        "requirements.txt",
        "check-config",
        "init-db",
        "scripts.resolve_symbols",
        "scripts.check_mt5_readonly",
        "scripts.inspect_news_sources",
        "scripts.preflight",
        "watchdog.py",
    ]
    body = text[text.index("Step '1/9") :]  # The header comment also names the steps.
    positions = [body.index(item) for item in order]
    assert positions == sorted(positions)
    assert "Test-Path -LiteralPath 'data\\reflexbot.db'" in text  # init-db only without a database
    assert "actual_account_kind -ne 'demo'" in text  # terminal-reported DEMO account required
    assert "not s.live_trading and not s.paper_trading" in text
    assert "s.start_paused and s.autonomous_demo" in text
    assert "python -m scripts.ops" in text
    assert "OPENAI_API_KEY=" not in text and "Get-Content" not in text  # never reads/prints secrets


def test_demo_quickstart_is_one_page_and_covers_the_essentials():
    text = (ROOT / "docs" / "DEMO_QUICKSTART.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 90
    for needle in (
        "setup_demo.ps1",
        "AUTONOMOUS_DEMO=true",
        "AI_REQUIRE_APPROVAL=true",
        "scripts.ops",
        "operator-stop.json",
        "scripts.live_view",
        "actions.log",
        "sendMessage",
    ):
        assert needle in text
