"""Async bounded POST transport: fixed owner-configured origin, no redirects/proxies/retries."""

from __future__ import annotations

import httpx

from ai.json_validation import AIInvalidResponse, AIUnavailable, strict_json
from core.settings import Settings


class JSONTransport:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.simulated = isinstance(transport, httpx.MockTransport)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.ai_timeout_seconds),
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
                limits=httpx.Limits(
                    max_connections=self.settings.ai_max_concurrent,
                    max_keepalive_connections=self.settings.ai_max_concurrent,
                ),
            )
        return self._client

    async def post(self, url: str, payload: dict, *, headers: dict | None = None) -> dict:
        try:
            request_headers = {"Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})}
            async with self._get_client().stream(
                "POST", url, json=payload, headers=request_headers, follow_redirects=False
            ) as response:
                if response.status_code in {408, 429, 500, 502, 503, 504, 404, 401, 403}:
                    raise AIUnavailable("http_unavailable")
                if response.status_code != 200:
                    raise AIInvalidResponse("unexpected_http_status")
                if (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                    != "application/json"
                ):
                    raise AIInvalidResponse("unsupported_content_type")
                if response.headers.get("content-encoding", "identity").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise AIInvalidResponse("unsupported_content_encoding")
                limit = self.settings.ai_max_response_bytes
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        count = int(length)
                    except (ValueError, OverflowError):
                        raise AIInvalidResponse("invalid_content_length") from None
                    if not 0 <= count <= limit:
                        raise AIInvalidResponse("oversized_response")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > limit:
                        raise AIInvalidResponse("oversized_response")
                    raw.extend(chunk)
            return strict_json(bytes(raw), max_bytes=limit)
        except (httpx.HTTPError, OSError):
            raise AIUnavailable("network_or_timeout") from None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
