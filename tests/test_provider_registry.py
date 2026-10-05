"""Multi-AI registry: labels, key names, failover only on timeout/429/5xx, AI approval required.

Offline only: every provider endpoint is an httpx.MockTransport.
"""

import json

import httpx
import pytest
from pydantic import ValidationError

from ai.ai_brain import NO_AI_APPROVAL, AIBrain
from ai.decision_journal import DecisionJournal
from ai.market_awareness import MarketAwarenessEngine, NewsContext
from ai.provider_registry import FailoverProvider, build_role, failover_eligible
from core.database import Database
from core.security import secret_values
from core.settings import AIProviderEntry, Settings
from tests.risk_helpers import MOMENT, config
from trading.mock_mt5 import MockMT5Client
from trading.types import ManualClock, Side

GROQ = "https://api.groq.com/openai/v1"


def entry(label, priority, model="qwen/qwen3.8-27b", key=None, **extra):
    return {
        "label": label,
        "kind": "openai_compatible",
        "base_url": GROQ,
        "model": model,
        "api_key_env": key or label.upper() + "_API_KEY",
        "role": "decision",
        "enabled": True,
        "priority": priority,
        **extra,
    }


def decision(**changes):
    values = dict(
        action="open_buy",
        confidence=85,
        reason="trend aligned",
        suggested_risk_percent=0.3,
        suggested_target_profit=5,
        suggested_sl_distance=0.0025,
        news_risk="low",
        market_condition="trending",
    )
    values.update(changes)
    return json.dumps(values)


class Endpoint:
    """One scripted OpenAI-compatible endpoint (status codes, timeouts or replies)."""

    def __init__(self, model="qwen/qwen3.8-27b", *, status=200, content=None, error=None):
        self.model, self.status, self.content, self.error, self.calls = model, status, content, error, 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        self.calls += 1
        assert "sk-" not in request.url.query.decode()  # Keys only ever travel in the header.
        if self.error is not None:
            raise self.error
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "scripted"}})
        envelope = {
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": self.content or decision()},
                }
            ],
        }
        return httpx.Response(200, json=envelope)


def registry_settings(tmp_path, entries, **changes):
    secrets = {e["label"]: "sk-test-" + e["label"] for e in entries}
    values = dict(
        symbols=("EURUSD",),
        ai_providers=entries,
        ai_provider_secrets=secrets,
        ai_provider="openai",
        ai_fallback_provider="disabled",
        ai_queue_min_interval_ms=0,
        ai_max_retries=0,
        ai_decision_cache_seconds=0,
        ai_timeout_seconds=5,
    )
    values.update(changes)
    return config(tmp_path, **values)


@pytest.fixture
async def market(tmp_path):
    clock = ManualClock(MOMENT)
    settings = config(tmp_path, symbols=("EURUSD",))
    broker = MockMT5Client(settings, clock=clock)
    await broker.initialize()
    snapshot = await MarketAwarenessEngine(broker, settings, clock).snapshot(
        "EURUSD", news=NewsContext(state="clear"), technical_side=Side.BUY, technical_score=85.0
    )
    return clock, snapshot


def brain_for(settings, clock, endpoints, *, notifier=None, events=None):
    database = Database(settings)
    database.initialize()
    journal = DecisionJournal(database, clock)
    listener = (lambda *e: events.append(e)) if events is not None else None
    brain = AIBrain.from_settings(
        settings,
        clock,
        transports={label: ep.transport for label, ep in endpoints.items()},
        provider_listener=listener,
        journal=journal,
        notifier=notifier,
    )
    return brain, journal


# -- registry / settings ---------------------------------------------------------------------------
def test_legacy_single_groq_setup_is_one_registry_entry(tmp_path):
    cfg = config(
        tmp_path,
        ai_provider="openai",
        ai_fallback_provider="disabled",
        openai_base_url=GROQ,
        openai_model="qwen/qwen3.8-27b",
        openai_api_key="sk-legacy",
    )
    (only,) = cfg.ai_registry()
    assert (only.label, only.kind, only.model, only.api_key_env) == (
        "groq",
        "openai_compatible",
        "qwen/qwen3.8-27b",
        "OPENAI_API_KEY",
    )
    assert cfg.ai_provider_key(only) == "sk-legacy"
    provider = build_role(cfg, "decision")
    assert isinstance(provider, FailoverProvider) and provider.labels == ("groq",)


def test_registry_has_no_two_provider_or_name_limit(tmp_path):
    entries = [entry(f"ai{i}", i) for i in range(6)]
    cfg = registry_settings(tmp_path, entries)
    assert [e.label for e in cfg.ai_registry()] == [f"ai{i}" for i in range(6)]
    assert build_role(cfg, "decision").labels == tuple(f"ai{i}" for i in range(6))


@pytest.mark.parametrize(
    "bad",
    [
        {"label": "rule_fallback"},  # reserved
        {"label": "unknown"},
        {"label": "Bad Label"},
        {"api_key_env": "sk-literal-key"},  # a literal key instead of a variable NAME
        {"api_key_env": ""},  # openai_compatible needs a key variable
        {"role": "admin"},
        {"kind": "anthropic"},
        {"priority": 101},
    ],
)
def test_invalid_registry_entries_are_rejected(bad):
    with pytest.raises(ValidationError):
        AIProviderEntry(**{**entry("groq", 0), **bad})


def test_custom_key_variable_is_read_from_env_and_never_dumped(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_KEY_SECOND", "sk-from-environment")
    env = tmp_path / ".env"
    env.write_text(
        "AI_PROVIDERS=" + json.dumps([entry("groq2", 0, key="GROQ_KEY_SECOND")]) + "\n",
        encoding="utf-8",
    )
    cfg = Settings(_env_file=env, project_root=tmp_path)
    (item,) = cfg.ai_registry()
    assert cfg.ai_provider_key(item) == "sk-from-environment"
    assert "sk-from-environment" not in json.dumps(cfg.model_dump(mode="json"), default=str)
    assert "sk-from-environment" not in repr(cfg)
    assert "sk-from-environment" in secret_values(cfg)  # Redaction covers registry keys.


def test_custom_key_variable_can_live_in_the_same_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_GROQ_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "AI_PROVIDERS="
        + json.dumps([entry("groq3", 0, key="LOCAL_GROQ_KEY")])
        + "\nLOCAL_GROQ_KEY=sk-dotenv\n",
        encoding="utf-8",
    )
    cfg = Settings(_env_file=env, project_root=tmp_path)
    assert cfg.ai_provider_key(cfg.ai_registry()[0]) == "sk-dotenv"


def test_failover_reasons_are_only_timeout_429_and_5xx():
    from ai.json_validation import AIInvalidResponse, AIUnavailable

    assert failover_eligible(AIUnavailable("http_429"))
    assert failover_eligible(AIUnavailable("http_5xx"))
    assert failover_eligible(TimeoutError())
    assert failover_eligible(httpx.ReadTimeout("slow"))
    assert not failover_eligible(AIUnavailable("http_auth_or_not_found"))
    assert not failover_eligible(AIInvalidResponse("invalid_openai_envelope"))
    assert not failover_eligible(ValueError("bad json"))


# -- failover through the real OpenAI-compatible client -------------------------------------------
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_primary_429_or_5xx_fails_over_and_records_the_real_label(tmp_path, market, status):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("groq2", 1, model="openai/gpt-oss-120b")])
    endpoints = {"groq": Endpoint(status=status), "groq2": Endpoint("openai/gpt-oss-120b")}
    events = []
    brain, journal = brain_for(cfg, clock, endpoints, events=events)
    result = await brain.decide(snapshot)
    assert result.source == "ai" and result.executable
    assert (endpoints["groq"].calls, endpoints["groq2"].calls) == (1, 1)
    row = journal.recent(kind="entry")[0]
    assert row["provider_label"] == "groq2" and row["model"] == "openai/gpt-oss-120b"
    assert ("failure", "groq", "http_429" if status == 429 else "http_5xx") in events
    assert all("sk-" not in str(e) for e in events)


async def test_primary_timeout_fails_over_within_one_decision_budget(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("backup", 1)])
    endpoints = {"groq": Endpoint(error=httpx.ReadTimeout("slow")), "backup": Endpoint()}
    brain, journal = brain_for(cfg, clock, endpoints)
    result = await brain.decide(snapshot)
    assert result.executable and journal.recent(kind="entry")[0]["provider_label"] == "backup"


@pytest.mark.parametrize(
    "primary",
    [
        Endpoint(status=401),  # auth error
        Endpoint(content="not json at all"),  # invalid reply
        Endpoint(content=decision(action="all_in")),  # schema violation
    ],
    ids=["auth", "invalid-json", "invalid-action"],
)
async def test_non_availability_errors_never_shop_to_another_ai(tmp_path, market, primary):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("groq2", 1)])
    second = Endpoint()
    brain, journal = brain_for(cfg, clock, {"groq": primary, "groq2": second})
    result = await brain.decide(snapshot)
    assert not result.executable and NO_AI_APPROVAL in result.reasons
    assert result.source == "ai_blocked" and second.calls == 0


@pytest.mark.parametrize("reply", [decision(action="wait", confidence=90), decision(confidence=40)])
async def test_a_valid_wait_or_low_confidence_reply_is_final(tmp_path, market, reply):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("groq2", 1)])
    second = Endpoint()
    brain, journal = brain_for(cfg, clock, {"groq": Endpoint(content=reply), "groq2": second})
    result = await brain.decide(snapshot)
    assert result.source == "ai" and not result.executable and second.calls == 0
    assert journal.recent(kind="entry")[0]["provider_label"] == "groq"


async def test_third_provider_answers_when_the_first_two_are_down(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("a", 0), entry("b", 1), entry("c", 2)])
    endpoints = {"a": Endpoint(status=503), "b": Endpoint(status=429), "c": Endpoint()}
    brain, journal = brain_for(cfg, clock, endpoints)
    assert (await brain.decide(snapshot)).executable
    assert journal.recent(kind="entry")[0]["provider_label"] == "c"


async def test_per_label_circuit_opens_and_is_skipped(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("groq2", 1)], ai_circuit_failures=2)
    endpoints = {"groq": Endpoint(status=503), "groq2": Endpoint()}
    events = []
    brain, _ = brain_for(cfg, clock, endpoints, events=events)
    for _ in range(4):
        assert (await brain.decide(snapshot)).executable
    assert endpoints["groq"].calls == 2  # Circuit open: the broken label is no longer called.
    assert ("circuit_open", "groq", "http_5xx") in events
    status = {s["label"]: s for s in brain.provider_status()}
    assert status["groq"]["circuit"] == "open" and status["groq2"]["circuit"] == "closed"


# -- AI approval required --------------------------------------------------------------------------
class Notes:
    def __init__(self):
        self.blocked_lines, self.decisions = [], []

    def decision(self, result, symbol):
        self.decisions.append(result.source)

    def circuit_opened(self, **kwargs):
        pass

    def circuit_closed(self, **kwargs):
        pass

    def blocked(self, symbol, reason):
        self.blocked_lines.append((symbol, reason))


async def test_every_ai_down_means_no_trade_and_one_owner_line(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(tmp_path, [entry("groq", 0), entry("groq2", 1)])
    notes = Notes()
    endpoints = {"groq": Endpoint(status=503), "groq2": Endpoint(error=httpx.ConnectError("down"))}
    brain, journal = brain_for(cfg, clock, endpoints, notifier=notes)
    result = await brain.decide(snapshot)
    assert result.source == "ai_blocked" and not result.executable
    assert NO_AI_APPROVAL in result.reasons
    row = journal.recent(kind="entry")[0]
    assert row["executed"] is False and NO_AI_APPROVAL in (row["rejection_reason"] or "")
    assert notes.blocked_lines == [("EURUSD", "all_providers_unavailable")]


async def test_require_approval_overrides_an_enabled_rule_fallback(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(
        tmp_path,
        [entry("groq", 0)],
        ai_rule_fallback_enabled=True,
        ai_fallback_mode="TECHNICAL_ONLY",
    )
    assert cfg.ai_require_approval is True
    brain, _ = brain_for(cfg, clock, {"groq": Endpoint(status=503)})
    result = await brain.decide(snapshot)
    assert result.source == "ai_blocked" and not result.executable


async def test_rule_fallback_when_explicitly_enabled_is_labelled_not_ai(tmp_path, market):
    clock, snapshot = market
    cfg = registry_settings(
        tmp_path,
        [entry("groq", 0)],
        ai_require_approval=False,
        ai_rule_fallback_enabled=True,
        ai_fallback_mode="TECHNICAL_ONLY",
    )
    brain, journal = brain_for(cfg, clock, {"groq": Endpoint(status=503)})
    result = await brain.decide(snapshot)
    assert result.source == "rule_fallback"
    assert journal.recent(kind="entry")[0]["provider_label"] == "RULE_FALLBACK"


# -- AI_DECISION_MODE=all_must_approve -------------------------------------------------------------
async def test_all_must_approve_needs_every_ai(tmp_path, market):
    clock, snapshot = market
    entries = [entry("groq", 0), entry("groq2", 1)]
    cfg = registry_settings(tmp_path, entries, ai_decision_mode="all_must_approve")
    both = {
        "groq": Endpoint(content=decision(confidence=90)),
        "groq2": Endpoint(content=decision(confidence=80)),
    }
    brain, journal = brain_for(cfg, clock, both)
    result = await brain.decide(snapshot)
    assert result.executable and result.decision.confidence == 80  # Most conservative reply.
    assert journal.recent(kind="entry")[0]["provider_label"] == "groq+groq2"

    one_waits = {"groq": Endpoint(), "groq2": Endpoint(content=decision(action="wait"))}
    strict = {"ai_decision_mode": "all_must_approve"}
    brain, _ = brain_for(registry_settings(tmp_path / "w", entries, **strict), clock, one_waits)
    assert not (await brain.decide(snapshot)).executable

    one_down = {"groq": Endpoint(), "groq2": Endpoint(status=503)}
    brain, _ = brain_for(registry_settings(tmp_path / "d", entries, **strict), clock, one_down)
    down = await brain.decide(snapshot)
    assert not down.executable and NO_AI_APPROVAL in down.reasons
