"""TEST ONLY provider/learning fixtures; no HTTP network/native SDK/promotion evidence."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import numpy as np

from ai.ai_router import AIRouter
from ai.ai_supervisor import AISupervisor
from ai.dataset import LearningDataset, TradeSample
from ai.feature_engineering import FEATURE_NAMES, FeatureVector
from ai.ollama_client import OllamaClient, ProviderContent
from ai.openai_client import OpenAIClient
from core.security import sha256_json
from tests.risk_helpers import MOMENT
from tests.signal_helpers import make_signal_runtime
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind


def body_from_messages(messages):
    text = messages[-1]["content"]
    return json.loads(text.removeprefix("BEGIN_UNTRUSTED_CONTEXT\n").removesuffix("\nEND_UNTRUSTED_CONTEXT"))


def scripted_reply(body, **changes):
    purpose = body["purpose"]
    values = {
        "format": f"reflex-ai-{purpose}-v1",
        **{k: body[k] for k in ("request_hash", "source", "code_hash", "model_sha256")},
        "confidence": 90,
        "reason_codes": ["quality_confirmed"],
        "rationale": "Scripted fixture; not an AI assessment.",
    }
    if purpose == "entry":
        values.update(
            proposal_hash=body["proposal_hash"],
            news_hash=body["news_hash"],
            decision="approve",
            risk_percent=None,
        )
    elif purpose == "market":
        values.update(regime="trend", sentiment="bullish")
    elif purpose == "news":
        values.update(sentiment=0.1, impact="unknown")
    elif purpose == "position":
        values.update(
            position_hash=body["position_hash"],
            news_hash=body["news_hash"],
            decision="hold",
            momentum_continues=True,
            volatility_safe=True,
            close_fraction=None,
        )
    elif purpose == "settings":
        values.update(action="no_change", risk_percent=None, weights=None)
    values.update(changes)
    return values


class ScriptedHTTP:
    def __init__(
        self, name="ollama", *, status=200, reply_changes=None, envelope_changes=None, delay=0, raw=None
    ):
        self.name, self.status, self.reply_changes, self.envelope_changes = (
            name,
            status,
            reply_changes or {},
            envelope_changes or {},
        )
        self.delay, self.raw, self.requests = delay, raw, []
        self.started = asyncio.Event()
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.requests.append(request)
        self.started.set()
        await asyncio.sleep(self.delay)
        payload = json.loads(request.content)
        body = body_from_messages(payload["messages"])
        reply = scripted_reply(body, **self.reply_changes)
        content = self.raw if self.raw is not None else json.dumps(reply)
        if self.name == "ollama":
            envelope = {
                "model": payload["model"],
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
            }
        else:
            envelope = {
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            }
        envelope.update(self.envelope_changes)
        return httpx.Response(self.status, json=envelope)


class ScriptedProvider:
    def __init__(self, name="ollama", *, error=None, changes=None, delay=0, configured=True, simulated=True):
        self.name, self.error, self.changes, self.delay = name, error, changes or {}, delay
        self.configured, self.simulated, self.calls, self.closed = configured, simulated, 0, False
        self.active, self.maximum_active = 0, 0

    async def complete(self, messages, schema):
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            body = body_from_messages(messages)
            return ProviderContent(
                self.name, "fixture-model", json.dumps(scripted_reply(body, **self.changes)), self.simulated
            )
        finally:
            self.active -= 1

    async def close(self):
        self.closed = True


async def make_ai_runtime(tmp_path, *, providers=None, **settings):
    signals, execution = await make_signal_runtime(tmp_path, **settings)
    primary = ScriptedHTTP()
    secondary = ScriptedHTTP("openai")
    selected = (
        providers
        if providers is not None
        else (
            OllamaClient(execution.settings, transport=primary.transport),
            OpenAIClient(execution.settings, transport=secondary.transport),
        )
    )
    router = AIRouter(execution.settings, execution.database, execution.clock, providers=selected)
    supervisor = AISupervisor(
        execution.database, execution.settings, execution.clock, execution.profile, router=router
    )
    await supervisor.initialize()
    return signals, execution, supervisor, primary, secondary


def fixture_dataset(cfg, *, profile=None, count=480, seed=42, source=SourceKind.SYNTHETIC, origin="fixture"):
    profile = profile or RuntimeProfile.current(cfg, source)
    rng = np.random.default_rng(seed)
    rows = []
    zero_one = {
        "technical_score",
        "coverage",
        "agreement",
        "p_rsi",
        "p_adx",
        "p_body",
        "p_close_location",
        "p_efficiency",
        "h_rsi",
        "h_adx",
        "t_rsi",
        "t_adx",
    }
    for i in range(count):
        now = MOMENT - timedelta(days=10) + timedelta(minutes=15 * i)
        values = rng.normal(0, 1, len(FEATURE_NAMES))
        for j, name in enumerate(FEATURE_NAMES):
            if name in zero_one:
                values[j] = rng.uniform(0.1, 0.9)
        values[0] = 1 if i % 2 == 0 else -1
        values[FEATURE_NAMES.index("p_di_balance")] = rng.uniform(-1, 1)
        values[FEATURE_NAMES.index("p_atr_percent")] = 0.04
        values[FEATURE_NAMES.index("p_volume_ratio")] = 1.2
        values[-2:] = [np.sin(2 * np.pi * now.hour / 24), np.cos(2 * np.pi * now.hour / 24)]
        win = values[FEATURE_NAMES.index("p_fast_gap_atr")] + rng.normal(0, 0.9) > 0
        rows.append(
            TradeSample(
                sha256_json({"fixture": True, "i": i, "seed": seed}),
                now,
                now + timedelta(seconds=1),
                now + timedelta(minutes=5),
                now + timedelta(minutes=6),
                "EURUSD",
                "buy" if values[0] > 0 else "sell",
                FeatureVector(tuple(float(v) for v in values)),
                Decimal("6") if win else Decimal("-5"),
                Decimal("5"),
                sha256_json({"fixture_proof": i}),
            )
        )
    return LearningDataset(
        source,
        origin,
        cfg.strategy_fingerprint(),
        profile.code_hash,
        sha256_json("fixture-account"),
        MOMENT,
        tuple(sorted(rows, key=lambda s: (s.decision_at, s.sample_id))),
    )
