"""Local MockTransport only: redirects, quotas, byte/media/encoding limits and URL redaction."""

import gzip
import logging

import httpx
import pytest

from core.settings import Settings
from news.http_client import NewsHTTP
from news.types import NewsInvalid, NewsUnavailable


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404, 408, 429, 500, 503])
async def test_non_success_is_never_followed_or_logged_as_raw_body(status, caplog):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            status, headers={"location": "https://evil.example/steal"}, content=b"RAW_PRIVATE_PROVIDER_ERROR"
        )

    client = NewsHTTP(Settings(_env_file=None), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises((NewsInvalid, NewsUnavailable)) as error:
            await client.get("https://example.com/news")
        assert len(calls) == 1 and "RAW_PRIVATE_PROVIDER_ERROR" not in str(error.value) + caplog.text
    finally:
        await client.close()


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"content-type": "text/html"}, b"{}"),
        ({"content-type": "application/json", "content-encoding": "gzip"}, gzip.compress(b"{}")),
        ({"content-type": "application/json", "content-length": "1048577"}, b"{}"),
        ({"content-type": "application/json", "content-length": "-1"}, b"{}"),
        ({"content-type": "application/json", "age": "901"}, b"{}"),
        ({"content-type": "application/json", "age": "NaN"}, b"{}"),
        ({"content-type": "application/json", "etag": "x" * 257}, b"{}"),
        ({"content-type": "application/json"}, b"x" * 1048577),
    ],
)
async def test_media_encoding_byte_age_validator_limits(headers, body):
    client = NewsHTTP(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, content=body)),
    )
    try:
        with pytest.raises(NewsInvalid):
            await client.get("https://example.com/news")
    finally:
        await client.close()


async def test_not_modified_needs_conditional_contract_and_reuses_no_body():
    client = NewsHTTP(
        Settings(_env_file=None, news_request_spacing_seconds=0.2),
        transport=httpx.MockTransport(lambda _: httpx.Response(304)),
    )
    try:
        with pytest.raises(NewsInvalid):
            await client.get("https://example.com/news")
        data = await client.get("https://example.com/news", conditional=True, headers={"If-None-Match": "ok"})
        assert data.status == 304 and data.raw == b"" and client.fixture_only
    finally:
        await client.close()


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("RAW_PRIVATE_EXCEPTION"), httpx.ConnectError("RAW_PRIVATE_EXCEPTION")]
)
async def test_network_exceptions_are_generic_and_no_retry(error):
    count = 0

    def handler(_):
        nonlocal count
        count += 1
        raise error

    client = NewsHTTP(Settings(_env_file=None), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(NewsUnavailable) as failure:
            await client.get("https://example.com/news")
        assert count == 1 and "RAW_PRIVATE_EXCEPTION" not in str(failure.value)
    finally:
        await client.close()


async def test_mandated_query_token_redacted_from_httpx_info_logs(caplog):
    secret = "FAKE_PRIVATE_TEST_TOKEN/PLUS+"
    cfg = Settings(_env_file=None, cryptopanic_api_key=secret)
    client = NewsHTTP(cfg, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []})))
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            await client.get(
                "https://cryptopanic.com/api/growth/v2/posts/", params={"auth_token": secret, "page": 1}
            )
        assert (
            secret not in caplog.text
            and "FAKE_PRIVATE_TEST_TOKEN" not in caplog.text
            and "[REDACTED]" in caplog.text
        )
    finally:
        await client.close()
