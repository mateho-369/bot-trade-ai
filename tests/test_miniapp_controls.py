"""Actual ASGI two-phase controls, strict IDs, no body-owned identity or orders."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from core.models import OwnerApproval
from tests.owner_helpers import api_client, auth_header, owner_runtime, owner_services
from tests.risk_helpers import open_one


async def test_api_resume_two_phase_and_duplicate_result(tmp_path):
    services = await owner_runtime(tmp_path)
    header = auth_header(services)
    request_id = str(uuid4())
    async with api_client(services) as (client, _):
        q = await client.post("/api/resume", json={"request_id": request_id}, headers=header)
        assert q.status_code == 200 and q.json()["status"] == "confirmation_required"
        assert services.database.status()["state"] == "paused"
        payload = {"request_id": request_id, "confirmation_token": q.json()["confirmation_token"]}
        for replayed in [False, True]:
            out = await client.post("/api/resume", json=payload, headers=header)
            assert out.status_code == 200 and out.json()["replayed"] is replayed
    assert services.database.status()["state"] == "running"
    await services.execution.shutdown()
    services.database.close()


async def test_api_close_captured_owned_once(tmp_path):
    services = await owner_runtime(tmp_path)
    await open_one(services.execution)
    owned = services.execution.logger.owned(services.execution.account_key)[0]
    payload = {"request_id": str(uuid4()), "ticket": owned.ticket, "position_identifier": owned.identifier}
    async with api_client(services) as (client, _):
        q = await client.post("/api/close_position", json=payload, headers=auth_header(services))
        assert q.json()["status"] == "confirmation_required"
        assert len(services.execution.broker._positions) == 1
        payload["confirmation_token"] = q.json()["confirmation_token"]
        out = await client.post("/api/close_position", json=payload, headers=auth_header(services))
        assert out.json()["status"] == "completed" and not out.json()["broker_account_flat_claimed"]
    await services.execution.shutdown()
    services.database.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("ticket", True),
        ("ticket", "100001"),
        ("ticket", 1.5),
        ("ticket", 0),
        ("position_identifier", -1),
        ("position_identifier", None),
        ("position_identifier", 2**63),
    ],
)
async def test_close_ids_strict_not_bool_float_string(tmp_path, field, value):
    services = owner_services(tmp_path)
    body = {"request_id": str(uuid4()), "ticket": 1, "position_identifier": 1}
    body[field] = value
    async with api_client(services) as (client, _):
        response = await client.post("/api/close_position", json=body, headers=auth_header(services))
    assert response.status_code == 422
    with services.database.session() as session:
        assert not session.scalar(select(OwnerApproval.id))


@pytest.mark.parametrize("action", ["resume", "close_all", "approve_suggestion"])
async def test_unattached_upward_actions_fail_closed(tmp_path, action):
    services = owner_services(tmp_path)
    payload = {"request_id": str(uuid4())}
    if action == "approve_suggestion":
        payload["suggestion_id"] = 1
    async with api_client(services) as (client, _):
        response = await client.post("/api/" + action, json=payload, headers=auth_header(services))
    assert response.status_code == 503
    assert services.control.session_id is None


async def test_cancel_endpoint_cannot_execute_nonce(tmp_path):
    services = await owner_runtime(tmp_path)
    header = auth_header(services)
    async with api_client(services) as (client, _):
        q = (await client.post("/api/resume", json={"request_id": str(uuid4())}, headers=header)).json()
        result = await client.post(
            "/api/cancel_confirmation", json={"confirmation_token": q["confirmation_token"]}, headers=header
        )
        assert result.json()["status"] == "canceled"
        denied = await client.post(
            "/api/resume",
            json={"request_id": q["request_id"], "confirmation_token": q["confirmation_token"]},
            headers=header,
        )
        assert denied.status_code == 409
    assert services.database.status()["state"] == "paused"
    await services.execution.shutdown()
    services.database.close()


async def test_unknown_nonce_cannot_resume(tmp_path):
    services = await owner_runtime(tmp_path)
    async with api_client(services) as (client, _):
        out = await client.post(
            "/api/resume",
            json={"request_id": str(uuid4()), "confirmation_token": "A" * 43},
            headers=auth_header(services),
        )
    assert out.status_code == 409 and services.database.status()["state"] == "paused"
    await services.execution.shutdown()
    services.database.close()
