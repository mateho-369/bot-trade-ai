"""Rule-based fallback + Groq/OpenAI-compatible strict JSON. Offline scripted transports only.

No real provider, broker, network or order: every HTTP exchange is an httpx.MockTransport and every
fill comes from the SYNTHETIC simulated broker.
"""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from ai.ai_router import FALLBACK_ELIGIBLE_OUTCOMES, AIRouter
from ai.ai_supervisor import AISupervisor
from ai.json_validation import AIInvalidResponse, AIUnavailable
from ai.ollama_client import OllamaClient
from ai.openai_client import OpenAIClient
from ai.prompt_templates import make_request
from ai.schemas import REPLY_CLASSES, decode_reply, strict_output_schema
from core.database import Database
from core.models import AuditLog, Signal
from core.settings import OperatingMode
from strategy.base_strategy import AIEntryReview
from strategy.rule_fallback import (
    RULE_FALLBACK_MODEL,
    RULE_FALLBACK_PROVIDER,
    rule_fallback_permitted,
    rule_fallback_problems,
    rule_fallback_review,
)
from tests.ai_helpers import ScriptedHTTP, ScriptedProvider, scripted_reply
from tests.risk_helpers import MOMENT, OWNER, config
from tests.signal_helpers import make_signal_runtime, news
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind

GROQ = {
    "ai_provider": "openai",
    "ai_fallback_provider": "disabled",
    "openai_base_url": "https://api.groq.com/openai/v1",
    "openai_model": "qwen/qwen3.8-27b",
    "openai_response_format": "json_schema_strict",
}


def request_for(cfg, purpose, binding=None):
    return make_request(
        purpose,
        {},
        settings=cfg,
        profile=RuntimeProfile.current(cfg, SourceKind.SYNTHETIC),
        as_of=MOMENT,
        expires_at=MOMENT + timedelta(seconds=30),
        binding=binding,
    )


ENTRY_BINDING = {"proposal_hash": "a" * 64, "news_hash": "b" * 64}
# These tests exercise the TECHNICAL_ONLY owner mode; BLOCK_ON_AI_FAILURE (the default) is covered
# in tests/test_ai_fallback_mode.py.
TECH = {"ai_fallback_mode": "TECHNICAL_ONLY"}


async def supervised(tmp_path, providers_factory, **settings):
    signals, execution = await make_signal_runtime(tmp_path, **TECH, **settings)
    providers = providers_factory(execution.settings)
    router = AIRouter(execution.settings, execution.database, execution.clock, providers=providers)
    supervisor = AISupervisor(
        execution.database, execution.settings, execution.clock, execution.profile, router=router
    )
    await supervisor.initialize()
    return signals, execution, supervisor


async def shutdown(execution, supervisor=None):
    if supervisor is not None:
        await supervisor.close()
    await execution.shutdown()
    execution.database.close()


def audit_actions(execution):
    with execution.database.session() as session:
        return [row.action for row in session.scalars(select(AuditLog))]


# ---------------------------------------------------------------- settings / scope


def test_fallback_defaults_on_but_live_scope_needs_explicit_opt_in(tmp_path):
    cfg = config(tmp_path)
    assert cfg.ai_rule_fallback_enabled is True and cfg.ai_rule_fallback_allow_live is False
    assert cfg.ai_rule_fallback_min_score >= max(cfg.ai_confidence_threshold, cfg.min_signal_score)
    public = cfg.public_config()
    assert public["ai_rule_fallback_enabled"] is True and public["openai_response_format"] == "json_object"
    profile = SimpleNamespace(data_source=SourceKind.MT5)
    for mode, allow_live, expected in (
        (OperatingMode.PAPER, False, True),
        (OperatingMode.DEMO, False, True),
        (OperatingMode.LIVE, False, False),
        (OperatingMode.LIVE, True, True),
        (OperatingMode.BACKTEST, True, False),
    ):
        stub = SimpleNamespace(
            ai_rule_fallback_enabled=True,
            ai_rule_fallback_allow_live=allow_live,
            mode=mode,
            ai_fallback_mode="TECHNICAL_ONLY",
        )
        assert rule_fallback_permitted(stub, profile) is expected, mode
        # BLOCK_ON_AI_FAILURE (the default) puts the fallback out of scope in EVERY mode.
        assert rule_fallback_permitted(stub, profile, fallback_mode="BLOCK_ON_AI_FAILURE") is False
    assert cfg.ai_fallback_mode == "BLOCK_ON_AI_FAILURE"
    assert rule_fallback_permitted(cfg, profile) is False
    historical = SimpleNamespace(data_source=SourceKind.HISTORICAL)
    paper = SimpleNamespace(
        ai_rule_fallback_enabled=True,
        ai_rule_fallback_allow_live=False,
        mode=OperatingMode.PAPER,
        ai_fallback_mode="TECHNICAL_ONLY",
    )
    assert rule_fallback_permitted(paper, historical) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"ai_rule_fallback_min_score": 60},
        {"ai_rule_fallback_min_score": 75, "ai_confidence_threshold": 80},
        {"ai_rule_fallback_min_score": 75, "min_signal_score": 85},
    ],
)
def test_fallback_threshold_cannot_undercut_ai_or_signal_thresholds(tmp_path, changes):
    with pytest.raises(ValidationError):
        config(tmp_path, **changes)
    config(tmp_path, ai_rule_fallback_enabled=False, **changes)  # Irrelevant when disabled.


def test_groq_openai_compatible_configuration_is_accepted(tmp_path):
    cfg = config(tmp_path, openai_reasoning_effort="low", **GROQ)
    assert cfg.openai_base_url == "https://api.groq.com/openai/v1"
    assert cfg.openai_model == "qwen/qwen3.8-27b" and cfg.openai_response_format == "json_schema_strict"
    for model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
        assert config(tmp_path, **{**GROQ, "openai_model": model}).openai_model == model
    with pytest.raises(ValidationError):
        config(tmp_path, **{**GROQ, "openai_base_url": "http://api.groq.com/openai/v1"})  # HTTPS only.
    with pytest.raises(ValidationError):
        config(tmp_path, **{**GROQ, "openai_response_format": "text"})


# ---------------------------------------------------------------- strict JSON schema


@pytest.mark.parametrize("purpose", sorted(REPLY_CLASSES))
def test_strict_provider_schema_is_closed_and_fully_required(purpose):
    def walk(node):
        assert set(node) <= {
            "type",
            "enum",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "anyOf",
        }
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
        for child in node.get("properties", {}).values():
            walk(child)
        for child in node.get("anyOf", ()):
            walk(child)
        if "items" in node:
            walk(node["items"])

    walk(strict_output_schema(purpose))


def test_strict_schema_advertises_decimal_fields_as_text_matching_local_validation():
    entry = strict_output_schema("entry")["properties"]
    assert entry["risk_percent"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    weights = strict_output_schema("settings")["properties"]["weights"]["anyOf"][0]
    assert set(weights["properties"]) == {"trend", "mean_reversion", "breakout", "momentum"}


async def test_openai_client_sends_groq_strict_json_schema_and_reasoning_effort(tmp_path):
    cfg = config(tmp_path, openai_api_key="TEST_GROQ_KEY_ONLY", openai_reasoning_effort="low", **GROQ)
    fixture = ScriptedHTTP("openai")
    client = OpenAIClient(cfg, transport=fixture.transport)
    request = request_for(cfg, "market")
    result = await client.complete(request.messages(), request.schema())
    sent = json.loads(fixture.requests[0].content)
    assert fixture.requests[0].url.host == "api.groq.com"
    assert fixture.requests[0].url.path == "/openai/v1/chat/completions"
    assert sent["model"] == "qwen/qwen3.8-27b" and sent["reasoning_effort"] == "low"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["schema"] == strict_output_schema("market")
    assert "TEST_GROQ_KEY_ONLY" not in fixture.requests[0].content.decode()
    # Local strict decoding still runs on the provider text.
    decode_reply(result.content, "market", request.payload())
    await client.close()


async def test_default_openai_mode_stays_json_object_without_reasoning_parameter(tmp_path):
    cfg = config(tmp_path, openai_api_key="TEST_KEY")
    fixture = ScriptedHTTP("openai")
    client = OpenAIClient(cfg, transport=fixture.transport)
    request = request_for(cfg, "news")
    await client.complete(request.messages(), request.schema())
    sent = json.loads(fixture.requests[0].content)
    assert sent["response_format"] == {"type": "json_object"} and "reasoning_effort" not in sent
    await client.close()


async def test_strict_mode_refuses_to_send_an_unreviewed_schema(tmp_path):
    cfg = config(tmp_path, openai_api_key="TEST_KEY", **GROQ)
    fixture = ScriptedHTTP("openai")
    client = OpenAIClient(cfg, transport=fixture.transport)
    with pytest.raises(AIInvalidResponse):
        await client.complete([{"role": "user", "content": "x"}], {"type": "object"})
    assert fixture.requests == []
    await client.close()


# ---------------------------------------------------------------- router outcomes


@pytest.mark.parametrize(
    "providers,expected",
    [
        (lambda: (ScriptedProvider(error=AIUnavailable()),), "unavailable"),  # 429/5xx/network
        (lambda: (ScriptedProvider(configured=False),), "unavailable"),  # e.g. Groq key not set yet
        (lambda: (ScriptedProvider(error=AIInvalidResponse()),), "invalid"),  # invalid JSON
        (lambda: (ScriptedProvider(changes={"proposal_hash": "c" * 64}),), "invalid"),
        (lambda: (ScriptedProvider(error=RuntimeError("boom")),), "unexpected"),
        (lambda: (ScriptedProvider(),), "reviewed"),
        (lambda: (ScriptedProvider(changes={"decision": "reject"}),), "reviewed"),
    ],
)
async def test_router_reports_why_no_reply_was_produced(tmp_path, providers, expected):
    cfg = config(tmp_path, ai_timeout_seconds=1)
    db = Database(cfg)
    db.initialize()
    request = request_for(cfg, "entry", ENTRY_BINDING)
    router = AIRouter(cfg, db, ManualClock(MOMENT), providers=providers())
    routed, outcome = await router.complete_with_outcome(request)
    assert outcome == expected and (routed is not None) == (expected == "reviewed")
    assert (outcome in FALLBACK_ELIGIBLE_OUTCOMES) == (expected in {"unavailable", "invalid", "unexpected"})
    await router.close()
    db.close()


async def test_disabled_provider_is_not_fallback_eligible(tmp_path):
    cfg = config(tmp_path, ai_provider="disabled", ai_fallback_provider="disabled")
    db = Database(cfg)
    db.initialize()
    request = request_for(cfg, "entry", ENTRY_BINDING)
    router = AIRouter(cfg, db, ManualClock(MOMENT), providers=(ScriptedProvider(),))
    assert await router.complete_with_outcome(request) == (None, "disabled")
    assert "disabled" not in FALLBACK_ELIGIBLE_OUTCOMES
    await router.close()
    db.close()


# ---------------------------------------------------------------- supervisor (provider failure) path


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_groq_rate_limit_or_outage_uses_rule_fallback_and_can_execute(tmp_path, status):
    http = ScriptedHTTP("openai", status=status)
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OpenAIClient(cfg, transport=http.transport),),
        openai_api_key="TEST_GROQ_KEY",
        **GROQ,
    )
    try:
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert len(http.requests) == 1  # One bounded attempt; no blind retry storm.
        assert ready.approved, ready.reasons
        payload = ready.payload()
        assert payload["ai_review"]["provider"] == RULE_FALLBACK_PROVIDER
        assert payload["ai_review"]["provider_model"] == RULE_FALLBACK_MODEL
        assert (
            payload["ai_review"]["confidence"] == ready.score >= execution.settings.ai_rule_fallback_min_score
        )
        with execution.database.session() as session:
            assert session.get(Signal, ready.signal_id).reason == "technical_rule_fallback_news_approved"
        assert "ai.rule_fallback_review" in audit_actions(execution)
        # Still PAUSED: the fallback never resumes. Owner resume + ordinary pre-send risk recheck apply.
        assert execution.database.status()["state"] == "paused"
        execution.control.resume(OWNER, account_key=execution.account_key)
        outcome = await execution.execute_signal(ready.signal_id)
        assert outcome.status.value == "filled" and outcome.reason == "SIMULATED_ONLY"
        assert len(await execution.broker.get_positions()) == 1  # SYNTHETIC simulated fill only.
    finally:
        await shutdown(execution, supervisor)


async def test_missing_groq_key_uses_rule_fallback_without_any_http_request(tmp_path):
    http = ScriptedHTTP("openai")
    signals, execution, supervisor = await supervised(
        tmp_path, lambda cfg: (OpenAIClient(cfg, transport=http.transport),), **GROQ
    )
    try:
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert ready.approved and http.requests == []
        assert ready.payload()["ai_review"]["provider"] == RULE_FALLBACK_PROVIDER
    finally:
        await shutdown(execution, supervisor)


@pytest.mark.parametrize("raw", ["not json at all", '{"truncated": ', "[]"])
async def test_invalid_json_from_provider_uses_rule_fallback(tmp_path, raw):
    http = ScriptedHTTP("openai", raw=raw)
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OpenAIClient(cfg, transport=http.transport),),
        openai_api_key="TEST_GROQ_KEY",
        **GROQ,
    )
    try:
        ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert ready.approved and ready.payload()["ai_review"]["provider"] == RULE_FALLBACK_PROVIDER
    finally:
        await shutdown(execution, supervisor)


@pytest.mark.parametrize("changes", [{"decision": "reject"}, {"decision": "wait"}, {"confidence": 10}])
async def test_valid_ai_veto_is_final_and_never_replaced_by_the_rule_fallback(tmp_path, changes):
    http = ScriptedHTTP("openai", reply_changes=changes)
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OpenAIClient(cfg, transport=http.transport),),
        openai_api_key="TEST_GROQ_KEY",
        **GROQ,
    )
    try:
        result = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert result.state == "rejected"
        assert "ai.rule_fallback_review" not in audit_actions(execution)
        with execution.database.session() as session:
            assert "rule_fallback" not in session.get(Signal, result.signal_id).reason
    finally:
        await shutdown(execution, supervisor)


async def test_unsafe_news_blocks_even_when_the_provider_is_down(tmp_path):
    http = ScriptedHTTP("openai", status=429)
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OpenAIClient(cfg, transport=http.transport),),
        openai_api_key="TEST_GROQ_KEY",
        **GROQ,
    )
    try:
        result = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock, safe=False))
        assert result.state == "rejected" and http.requests == []
        assert "ai.rule_fallback_review" not in audit_actions(execution)
    finally:
        await shutdown(execution, supervisor)


async def test_provider_down_with_fallback_disabled_stays_a_veto(tmp_path):
    http = ScriptedHTTP("openai", status=429)
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OpenAIClient(cfg, transport=http.transport),),
        openai_api_key="TEST_GROQ_KEY",
        ai_rule_fallback_enabled=False,
        **GROQ,
    )
    try:
        result = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons
        assert "ai.rule_fallback_not_permitted" in audit_actions(execution)
    finally:
        await shutdown(execution, supervisor)


async def test_disabled_ai_provider_never_uses_the_rule_fallback(tmp_path):
    signals, execution, supervisor = await supervised(
        tmp_path,
        lambda cfg: (OllamaClient(cfg, transport=ScriptedHTTP().transport),),
        ai_provider="disabled",
        ai_fallback_provider="disabled",
    )
    try:
        result = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
        assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons
    finally:
        await shutdown(execution, supervisor)


# ---------------------------------------------------------------- signal-engine (timeout) path


class TooSlow:
    async def review(self, proposal, news_window):
        await asyncio.Event().wait()


class Broken:
    async def review(self, proposal, news_window):
        raise RuntimeError("unexpected reviewer defect")


async def test_reviewer_timeout_uses_rule_fallback(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, **TECH, ai_timeout_seconds=1)
    try:
        ready = await signals.evaluate("EURUSD", reviewer=TooSlow(), news=news(signals.clock))
        assert ready.approved and ready.payload()["ai_review"]["provider"] == RULE_FALLBACK_PROVIDER
        assert ready.context.ai_confidence == ready.score
        assert signals.store.approved_context(_row(execution, ready.signal_id)).signal_id == ready.signal_id
    finally:
        await shutdown(execution)


async def test_rule_fallback_below_its_minimum_score_waits(tmp_path):
    signals, execution = await make_signal_runtime(
        tmp_path, **TECH, ai_timeout_seconds=1, ai_rule_fallback_min_score=100
    )
    try:
        result = await signals.evaluate("EURUSD", reviewer=TooSlow(), news=news(signals.clock))
        assert result.score < 100 and result.state == "rejected"
        assert "rule_fallback_score_below_minimum" in result.reasons and "ai_veto_or_wait" in result.reasons
        assert await execution.broker.get_positions() == ()
    finally:
        await shutdown(execution)


async def test_unexpected_reviewer_defect_stays_fail_closed(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, **TECH)
    try:
        result = await signals.evaluate("EURUSD", reviewer=Broken(), news=news(signals.clock))
        assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons
    finally:
        await shutdown(execution)


async def test_timeout_with_unknown_news_never_falls_back(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, **TECH, ai_timeout_seconds=1)
    try:
        result = await signals.evaluate("EURUSD", reviewer=TooSlow(), news=news(signals.clock, known=False))
        assert result.state == "rejected" and "ai_unavailable_or_invalid" in result.reasons
    finally:
        await shutdown(execution)


# ---------------------------------------------------------------- binding / tamper checks


def _row(execution, signal_id):
    with execution.database.session() as session:
        row = session.get(Signal, signal_id)
        session.expunge(row)
        return row


async def test_fallback_review_cannot_fabricate_confidence_or_change_risk(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, **TECH)
    try:
        proposal = await signals.analyze("EURUSD")
        window = news(signals.clock)
        base = rule_fallback_review(
            proposal, window, settings=signals.settings, profile=signals.profile, now=signals.clock.now()
        )
        assert base.decision == "approve" and base.confidence == proposal.score
        cfg, profile = signals.settings, signals.profile
        assert rule_fallback_problems(base, signal_score=proposal.score, settings=cfg, profile=profile) == []
        inflated = replace(base, confidence=min(100.0, proposal.score + 5))
        assert "rule_fallback_score_mismatch" in rule_fallback_problems(
            inflated, signal_score=proposal.score, settings=cfg, profile=profile
        )
        risky = replace(base, risk_percent=Decimal("0.1"))
        assert "rule_fallback_risk_change" in rule_fallback_problems(
            risky, signal_score=proposal.score, settings=cfg, profile=profile
        )
        relabeled = replace(base, provider_model="gpt-oss")
        assert "rule_fallback_unbound" in rule_fallback_problems(
            relabeled, signal_score=proposal.score, settings=cfg, profile=profile
        )
        result = await signals.finalize(proposal.signal_id, review=inflated, news=window)
        assert result.state == "rejected" and "rule_fallback_score_mismatch" in result.reasons
    finally:
        await shutdown(execution)


async def test_stored_fallback_approval_is_rechecked_against_current_policy(tmp_path):
    signals, execution = await make_signal_runtime(tmp_path, **TECH, ai_timeout_seconds=1)
    try:
        ready = await signals.evaluate("EURUSD", reviewer=TooSlow(), news=news(signals.clock))
        assert ready.approved
        row = _row(execution, ready.signal_id)
        disabled = signals.settings.model_copy(update={"ai_rule_fallback_enabled": False})
        stored = AIEntryReview.from_dict(ready.payload()["ai_review"])
        assert (
            rule_fallback_problems(
                stored, signal_score=row.score, settings=signals.settings, profile=signals.profile
            )
            == []
        )
        problems = rule_fallback_problems(
            stored, signal_score=row.score, settings=disabled, profile=signals.profile
        )
        assert "rule_fallback_disabled_or_out_of_scope" in problems
        assert "rule_fallback_score_mismatch" in rule_fallback_problems(
            stored, signal_score=row.score - 1, settings=signals.settings, profile=signals.profile
        )
    finally:
        await shutdown(execution)


def test_scripted_strict_reply_round_trips_local_validation():
    body = {
        "purpose": "entry",
        "request_hash": "a" * 64,
        "source": "synthetic",
        "code_hash": "b" * 64,
        "model_sha256": "c" * 64,
        "proposal_hash": "d" * 64,
        "news_hash": "e" * 64,
    }
    reply = scripted_reply(body)
    assert set(reply) == set(strict_output_schema("entry")["properties"])
    decode_reply(json.dumps(reply), "entry", body)
