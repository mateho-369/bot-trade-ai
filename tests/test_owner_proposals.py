"""Confirmation-controlled proposals are decisions ONLY, never settings/trades."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_identity import OwnerInterfaceError
from core.models import AISuggestion, BotState, OrderIntent
from tests.owner_helpers import actor, api_client, auth_header, owner_runtime


@pytest.fixture
async def services(tmp_path):
    value = await owner_runtime(tmp_path)
    yield value
    await value.execution.shutdown()
    value.database.close()


def proposal(services):
    return services.suggestions.create(
        "reduce_risk", {"risk_percent": "0.4"}, reason="TEST_ONLY proposal", request_hash="b" * 64
    )


async def test_approve_confirmation_records_only_no_configuration_application(services, monkeypatch):
    stored = proposal(services)
    who = actor(services)
    key = str(uuid4())
    params = {"suggestion_id": stored.suggestion_id}

    def forbidden(*args, **kwargs):
        raise AssertionError("Owner interface must not apply")

    monkeypatch.setattr(services.suggestions, "apply", forbidden)
    q = await services.action(who, "approve_suggestion", params, key)
    assert services.suggestions.get(stored.suggestion_id).status == "pending"
    result = await services.action(who, "approve_suggestion", params, key, q["confirmation_token"])
    assert result["status"] == "completed" and result["proposal_status"] == "approved"
    assert result["settings_applied"] is False and result["trade_executed"] is False
    assert services.settings.effective_risk_percent == services.execution.settings.effective_risk_percent
    with services.database.session() as session:
        assert session.get(BotState, 1).settings_overrides == {}
        assert not session.scalar(select(OrderIntent.id))


async def test_reject_direct_and_one_way(services):
    stored = proposal(services)
    who = actor(services)
    params = {"suggestion_id": stored.suggestion_id}
    result = await services.action(who, "reject_suggestion", params, str(uuid4()))
    assert result["proposal_status"] == "rejected"
    with pytest.raises(OwnerInterfaceError, match="proposal_not_pending"):
        await services.action(who, "approve_suggestion", params, str(uuid4()))


async def test_expired_or_tampered_proposal_not_approved(services):
    stored = proposal(services)
    who = actor(services)
    key = str(uuid4())
    params = {"suggestion_id": stored.suggestion_id}
    q = await services.action(who, "approve_suggestion", params, key)
    with services.database.session() as session:
        row = session.get(AISuggestion, stored.suggestion_id)
        payload = dict(row.suggestion)
        payload["parameters"] = {"risk_percent": "100"}
        row.suggestion = payload
    result = await services.action(who, "approve_suggestion", params, key, q["confirmation_token"])
    assert result["status"] == "rejected"
    with services.database.session() as session:
        assert session.get(AISuggestion, stored.suggestion_id).status == "pending"


async def test_api_approval_cannot_accept_owner_or_risk_body_fields(services):
    stored = proposal(services)
    async with api_client(services) as (client, _):
        response = await client.post(
            "/api/approve_suggestion",
            json={
                "request_id": str(uuid4()),
                "suggestion_id": stored.suggestion_id,
                "owner_id": 42,
                "risk_percent": "99",
            },
            headers=auth_header(services),
        )
    assert response.status_code == 422
    assert services.suggestions.get(stored.suggestion_id).status == "pending"


async def test_close_position_suggestion_approval_not_a_close(services):
    stored = services.suggestions.create(
        "close_position",
        {"trade_id": 1, "position_identifier": 1, "position_hash": "f" * 64, "fraction": "1"},
        reason="TEST_ONLY close proposal, not an actual position",
        request_hash="e" * 64,
    )
    who = actor(services)
    key = str(uuid4())
    params = {"suggestion_id": stored.suggestion_id}
    q = await services.action(who, "approve_suggestion", params, key)
    result = await services.action(who, "approve_suggestion", params, key, q["confirmation_token"])
    assert result["proposal_status"] == "approved" and not result["trade_executed"]
    assert await services.execution.broker.get_positions() == ()
