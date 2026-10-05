import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from ai.ai_router import AIRouter
from ai.json_validation import AIInvalidResponse, AIUnavailable
from ai.prompt_templates import make_request
from core.database import Database
from core.models import AuditLog
from tests.ai_helpers import ScriptedProvider
from tests.risk_helpers import MOMENT, config
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


@pytest.fixture
def setup(tmp_path):
    cfg = config(tmp_path, ai_timeout_seconds=1, ai_max_concurrent=1)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    request = make_request(
        "entry",
        {},
        settings=cfg,
        profile=RuntimeProfile.current(cfg, SourceKind.SYNTHETIC),
        as_of=MOMENT,
        expires_at=MOMENT + timedelta(seconds=30),
        binding={"proposal_hash": "a" * 64, "news_hash": "b" * 64},
    )
    yield cfg, db, clock, request
    db.close()


async def test_availability_fallback_is_bound_and_audited(setup):
    cfg, db, clock, request = setup
    primary = ScriptedProvider(error=AIUnavailable())
    secondary = ScriptedProvider("openai")
    router = AIRouter(cfg, db, clock, providers=(primary, secondary))
    result = await router.complete(request)
    assert result.provider == "openai" and primary.calls == secondary.calls == 1
    with db.session() as s:
        assert {"ai.provider_unavailable", "ai.reviewed"} <= {r.action for r in s.scalars(select(AuditLog))}
    await router.close()


@pytest.mark.parametrize("changes", [{"decision": "reject"}, {"decision": "wait"}, {"confidence": 20}])
async def test_never_fallback_past_a_valid_veto_or_low_confidence(setup, changes):
    cfg, db, clock, request = setup
    primary = ScriptedProvider(changes=changes)
    secondary = ScriptedProvider("openai")
    router = AIRouter(cfg, db, clock, providers=(primary, secondary))
    result = await router.complete(request)
    assert result is not None and secondary.calls == 0
    await router.close()


@pytest.mark.parametrize(
    "error,changes",
    [
        (AIInvalidResponse(), {}),
        (None, {"proposal_hash": "c" * 64}),
        (None, {"volume": "1"}),
        (RuntimeError("password=TEST_SECRET"), {}),
    ],
)
async def test_invalid_or_unexpected_response_veto_without_fallback(setup, error, changes):
    cfg, db, clock, request = setup
    primary = ScriptedProvider(error=error, changes=changes)
    secondary = ScriptedProvider("openai")
    router = AIRouter(cfg, db, clock, providers=(primary, secondary))
    assert await router.complete(request) is None and secondary.calls == 0
    with db.session() as s:
        assert "TEST_SECRET" not in str([r.details for r in s.scalars(select(AuditLog))])
    await router.close()


async def test_timeout_reserves_fallback_budget(setup):
    cfg, db, clock, request = setup
    primary = ScriptedProvider(delay=2)
    secondary = ScriptedProvider("openai")
    router = AIRouter(cfg, db, clock, providers=(primary, secondary))
    assert (await router.complete(request)).provider == "openai" and primary.active == 0
    await router.close()


async def test_bounded_concurrency_cancel_and_circuit_breaker(setup):
    cfg, db, clock, request = setup
    provider = ScriptedProvider(delay=0.03)
    router = AIRouter(cfg, db, clock, providers=(provider,))
    results = await asyncio.gather(*(router.complete(request) for _ in range(8)))
    assert all(r is not None for r in results) and provider.maximum_active == 1
    provider.delay = 2
    task = asyncio.create_task(router.complete(request))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.active == 0
    provider.delay = 0
    provider.error = AIUnavailable()
    for _ in range(cfg.ai_circuit_failures):
        assert await router.complete(request) is None
    calls = provider.calls
    assert await router.complete(request) is None and provider.calls == calls
    await router.close()
    assert provider.closed


async def test_disabled_primary_and_expired_request_do_not_call_provider(tmp_path):
    cfg = config(tmp_path, ai_provider="disabled")
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    request = make_request(
        "market",
        {},
        settings=cfg,
        profile=RuntimeProfile.current(cfg, SourceKind.SYNTHETIC),
        as_of=MOMENT,
        expires_at=MOMENT + timedelta(seconds=30),
    )
    provider = ScriptedProvider("openai")
    router = AIRouter(cfg, db, clock, providers=(provider,))
    assert await router.complete(request) is None and provider.calls == 0
    await router.close()
    db.close()
