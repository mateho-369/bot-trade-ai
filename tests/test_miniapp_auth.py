"""Every API read and mutation must derive owner from fresh signed initData."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.models import OwnerApproval
from scripts.synthetic_owner_fixtures import signed_fixture
from tests.owner_helpers import api_client, auth_header, owner_services

READS = ["dashboard", "positions", "trades", "signals", "news", "ai_suggestions", "settings", "logs"]
MUTATIONS = [
    "pause",
    "kill",
    "resume",
    "close_all",
    "close_position",
    "approve_suggestion",
    "reject_suggestion",
    "cancel_confirmation",
]


@pytest.mark.parametrize("path", READS)
async def test_all_reads_deny_absent_auth(tmp_path, path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/" + path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", MUTATIONS)
async def test_all_posts_deny_absent_auth(tmp_path, path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.post("/api/" + path, json={"request_id": str(uuid4())})
    assert response.status_code == 401
    with services.database.session() as session:
        assert not session.scalar(select(OwnerApproval.id))


@pytest.mark.parametrize("path", READS)
async def test_actual_fresh_local_hmac_owner_reads(tmp_path, path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/" + path, headers=auth_header(services))
    assert response.status_code == 200
    assert response.json()["meta"]["broker_read"] is False


@pytest.mark.parametrize("source", ["body", "query", "unsafe", "bearer", "cookie"])
async def test_no_unverified_identity_or_other_credential_fallback(tmp_path, source):
    services = owner_services(tmp_path)
    raw = signed_fixture(services.settings, services.clock)
    kwargs = {"json": {"request_id": str(uuid4()), "owner_id": 42}}
    path = "/api/pause"
    if source == "query":
        path += "?initData=" + raw
    if source == "unsafe":
        kwargs["json"]["initDataUnsafe"] = {"user": {"id": 42}}
    if source == "bearer":
        kwargs["headers"] = {"Authorization": "Bearer " + raw}
    if source == "cookie":
        kwargs["headers"] = {"Cookie": "initData=" + raw}
    async with api_client(services) as (client, _):
        response = await client.post(path, **kwargs)
    assert response.status_code in {400, 401, 414}
    assert raw not in response.text


async def test_signed_other_owner_denied(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/dashboard", headers=auth_header(services, user={"id": 43}))
    assert response.status_code == 403


async def test_exact_ttl_expiry_reopen_not_refresh(tmp_path):
    services = owner_services(tmp_path)
    header = auth_header(services)
    services.clock.advance(timedelta(seconds=300))
    async with api_client(services) as (client, _):
        response = await client.get("/api/dashboard", headers=header)
        fresh = await client.get("/api/dashboard", headers=auth_header(services))
    assert response.status_code == 401 and response.json()["error"]["code"] == "owner_session_expired"
    assert fresh.status_code == 200


async def test_no_live_or_generic_order_editor_route(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        for path in [
            "/api/order",
            "/api/open",
            "/api/enable_live",
            "/api/reset_kill",
            "/api/apply_suggestion",
            "/api/models/start",
            "/api/settings",
        ]:
            response = await client.post(
                path, json={"request_id": str(uuid4())}, headers=auth_header(services)
            )
            assert response.status_code in {404, 405}
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.get("/preview/data")).status_code == 404
        assert (await client.post("/telegram/webhook", json={})).status_code == 404
