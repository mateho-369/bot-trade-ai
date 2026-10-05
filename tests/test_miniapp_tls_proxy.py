"""HTTPS must be real ASGI TLS or attested by an explicitly trusted proxy PEER."""

import httpx
import pytest
from pydantic import ValidationError

from miniapp.server import create_app
from tests.owner_helpers import auth_header, owner_services
from tests.risk_helpers import config


@pytest.mark.parametrize(
    "peer,proto,allowed",
    [
        ("127.0.0.1", "https", True),
        ("::1", "https", True),
        ("127.0.0.1", None, False),
        ("127.0.0.1", "http", False),
        ("127.0.0.1", "https,http", False),
        ("203.0.113.10", "https", False),
        ("203.0.113.10", None, False),
    ],
)
async def test_tls_proxy_uses_actual_peer_not_forwarded_ip(tmp_path, peer, proto, allowed):
    services = owner_services(tmp_path)
    app = create_app(services.settings, services)
    headers = auth_header(services) | {
        "Origin": "https://owner.example",
        "X-Forwarded-For": "127.0.0.1",
        "X-Forwarded-Host": "owner.example",
    }
    if proto is not None:
        headers["X-Forwarded-Proto"] = proto
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            base_url="http://owner.example", transport=httpx.ASGITransport(app=app, client=(peer, 54321))
        ) as client:
            response = await client.get("/api/dashboard", headers=headers)
    assert response.status_code == (200 if allowed else 403)
    if not allowed:
        assert response.json()["error"]["code"] == "https_transport_required"


async def test_direct_tls_does_not_need_or_trust_forwarded_proto(tmp_path):
    services = owner_services(tmp_path)
    app = create_app(services.settings, services)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            base_url="https://owner.example",
            transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 54321)),
        ) as client:
            response = await client.get(
                "/api/dashboard", headers=auth_header(services) | {"X-Forwarded-Proto": "http"}
            )
    assert response.status_code == 200


async def test_configured_exact_remote_proxy_attestation(tmp_path):
    services = owner_services(tmp_path, api_trusted_proxy_ips=("203.0.113.10",))
    app = create_app(services.settings, services)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            base_url="http://owner.example",
            transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 54321)),
        ) as client:
            response = await client.get(
                "/api/dashboard", headers=auth_header(services) | {"X-Forwarded-Proto": "https"}
            )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "ips",
    [
        ("*",),
        ("127.0.0.0/8",),
        ("localhost",),
        ("127.0.0.1", "127.0.0.1"),
        ("::1", "0:0:0:0:0:0:0:1"),
        tuple(f"192.0.2.{i}" for i in range(1, 10)),
    ],
)
def test_exact_bounded_proxy_ip_only(tmp_path, ips):
    with pytest.raises(ValidationError):
        config(tmp_path, api_trusted_proxy_ips=ips)
