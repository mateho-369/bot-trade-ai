"""Bounded HTTP, same HTTPS origin behind a proxy, generic errors, rate controls."""

from uuid import uuid4

import pytest

from tests.owner_helpers import api_client, auth_header, owner_services


@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("https://owner.example", True),
        ("https://evil.example", False),
        ("null", False),
        ("http://owner.example", False),
        ("https://owner.example/path", False),
        ("https://owner.example@evil.example", False),
        ("https://owner.example:443", True),
    ],
)
async def test_exact_origin_policy(tmp_path, origin, allowed):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/dashboard", headers=auth_header(services) | {"Origin": origin})
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert response.headers["access-control-allow-origin"] == "https://owner.example"


async def test_public_https_origin_accepted_behind_asgi_http(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services, base_url="http://owner.example") as (client, _):
        response = await client.post(
            "/api/pause",
            json={"request_id": str(uuid4())},
            headers=auth_header(services) | {"Origin": "https://owner.example", "X-Forwarded-Proto": "https"},
        )
    assert response.status_code == 200
    assert "max-age" in response.headers["strict-transport-security"]


async def test_explicit_cors_origin_no_cookie_credentials(tmp_path):
    services = owner_services(tmp_path, api_cors_origins=("https://console.example",))
    async with api_client(services) as (client, _):
        response = await client.options(
            "/api/dashboard",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-telegram-init-data",
            },
        )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://console.example"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    "change",
    [
        {"Origin": "https://evil.example"},
        {"Access-Control-Request-Method": "DELETE"},
        {"Access-Control-Request-Headers": "authorization"},
    ],
)
async def test_preflight_not_broadly_open(tmp_path, change):
    services = owner_services(tmp_path)
    headers = {"Origin": "https://owner.example", "Access-Control-Request-Method": "POST"}
    headers.update(change)
    async with api_client(services) as (client, _):
        assert (await client.options("/api/pause", headers=headers)).status_code == 403


async def test_cross_site_without_origin_denied(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get(
            "/api/dashboard", headers=auth_header(services) | {"Sec-Fetch-Site": "cross-site"}
        )
    assert response.status_code == 403


async def test_host_allowlist_no_forwarded_host_escape(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get(
            "/api/dashboard",
            headers=auth_header(services) | {"Host": "evil.example", "X-Forwarded-Host": "owner.example"},
        )
    assert response.status_code == 400
    assert "evil.example" not in response.text


@pytest.mark.parametrize(
    "header", ["X-Telegram-Init-Data", "Origin", "Host", "Content-Length", "Content-Type"]
)
async def test_duplicate_sensitive_headers_denied(tmp_path, header):
    services = owner_services(tmp_path)
    headers = [
        ("X-Telegram-Init-Data", auth_header(services)["X-Telegram-Init-Data"]),
        ("Origin", "https://owner.example"),
        ("Content-Type", "application/json"),
    ]
    value = dict(headers).get(header, "owner.example" if header == "Host" else "0")
    headers.append((header, value))
    if header in {"Host", "Content-Length"}:
        headers.append((header, value))
    async with api_client(services) as (client, _):
        response = await client.post("/api/pause", content=b"{}", headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "query",
    [
        "owner_id=42",
        "initData=SECRET_MARKER",
        "token=SECRET_MARKER",
        "limit=1&limit=2",
        "limit=%FF",
        "limit=%",
        "limit=NaN",
        "limit=1e3",
        "limit=1;offset=2",
        "x",
        "offset=-1",
    ],
)
async def test_bounded_allowlisted_queries_without_secrets(tmp_path, query):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/logs?" + query, headers=auth_header(services))
    assert response.status_code == 400
    assert "SECRET_MARKER" not in response.text


async def test_query_and_header_caps(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        assert (await client.get("/?" + "x" * 513)).status_code == 414
        assert (
            await client.get("/api/dashboard", headers={"X-Telegram-Init-Data": "x" * 8193})
        ).status_code == 431
        assert (await client.get("/", headers={"X-Padding": "x" * 17000})).status_code == 431


@pytest.mark.parametrize(
    "body",
    [
        b'{"request_id":"SECRET_MARKER","request_id":"again"}',
        b'{"request_id":NaN}',
        b'{"x":Infinity}',
        b"[]",
        b"null",
        b"{",
        b'{"x":"\xff"}',
    ],
)
async def test_strict_json_no_duplicates_nonfinite_or_reflection(tmp_path, body):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.post(
            "/api/pause", content=body, headers=auth_header(services) | {"Content-Type": "application/json"}
        )
    assert response.status_code == 400
    assert "SECRET_MARKER" not in response.text and "input" not in response.text


async def test_content_bounds_type_and_encoding(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        assert (
            await client.post(
                "/api/pause", content=b"x" * 16385, headers={"Content-Type": "application/json"}
            )
        ).status_code == 413
        assert (
            await client.post("/api/pause", content=b"{}", headers={"Content-Type": "text/plain"})
        ).status_code == 415
        assert (
            await client.post(
                "/api/pause",
                content=b"{}",
                headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            )
        ).status_code == 415
        assert (
            await client.request("GET", "/api/dashboard", content=b"{}", headers=auth_header(services))
        ).status_code == 400


async def test_chunked_body_bounded_even_without_content_length(tmp_path):
    services = owner_services(tmp_path)

    async def chunks():
        yield b"{" + b" " * 12000
        yield b" " * 12000 + b"}"

    async with api_client(services) as (client, _):
        response = await client.post(
            "/api/pause", content=chunks(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "SECRET_MARKER"},
        {"request_id": str(uuid4()), "owner_id": 42},
        {"request_id": str(uuid4()), "risk_percent": "100"},
        {"request_id": str(uuid4()), "live_trading": True},
    ],
)
async def test_sanitized_pydantic_422_never_echoes_input(tmp_path, body):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.post("/api/pause", json=body, headers=auth_header(services))
    assert response.status_code == 422
    assert "SECRET_MARKER" not in response.text and '"input"' not in response.text


async def test_csp_cache_and_privacy_headers_on_success_and_denial(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        responses = [
            await client.get("/"),
            await client.get("/api/dashboard"),
            await client.get("/api/dashboard", headers=auth_header(services)),
        ]
    for response in responses:
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert (
            "frame-ancestors 'self' https://web.telegram.org" in response.headers["content-security-policy"]
        )
        assert "'unsafe-inline'" not in response.headers["content-security-policy"]
        assert "set-cookie" not in response.headers


async def test_ordinary_budget_cannot_consume_owner_safe_stop_budget(tmp_path):
    services = owner_services(tmp_path, api_rate_limit_per_minute=5, api_safe_stop_rate_per_minute=2)
    header = auth_header(services)
    async with api_client(services) as (client, _):
        for _ in range(5):
            assert (await client.get("/api/dashboard", headers=header)).status_code == 200
        denied = await client.get("/api/dashboard", headers=header)
        assert denied.status_code == 429 and denied.headers["retry-after"] == "60"
        for _ in range(2):
            assert (
                await client.post("/api/pause", json={"request_id": str(uuid4())}, headers=header)
            ).status_code == 200
        assert (
            await client.post("/api/kill", json={"request_id": str(uuid4())}, headers=header)
        ).status_code == 429


async def test_generic_unexpected_failure_does_not_expose_exception_or_traceback(tmp_path, monkeypatch):
    services = owner_services(tmp_path)

    def broken():
        raise RuntimeError("SECRET_MARKER raw SQL password provider body")

    monkeypatch.setattr(services.views, "dashboard", broken)
    async with api_client(services) as (client, _):
        response = await client.get("/api/dashboard", headers=auth_header(services))
    assert response.status_code == 503
    assert "SECRET_MARKER" not in response.text and "Traceback" not in response.text
