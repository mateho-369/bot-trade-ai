"""Bounded fixed-origin GET; no page/link redirects, proxy/retry, body/error URL logging."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx

from core.security import sanitize_text, secret_values
from core.settings import Settings, valid_news_url
from news.types import NewsInvalid, NewsUnavailable

_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:auth_token|access_token|token|api_?key|key|secret|password)=)[^&\s]+"
)


class _HTTPRedaction(logging.Filter):
    def filter(self, record):
        record.msg = _SECRET_QUERY.sub(r"\1[REDACTED]", sanitize_text(record.getMessage()))
        record.args = ()
        return True


@dataclass(frozen=True, slots=True)
class HTTPPayload:
    status: int
    raw: bytes
    etag: str | None = None
    last_modified: str | None = None
    age_seconds: int = 0


class NewsHTTP:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport
        self.fixture_only = isinstance(transport, httpx.MockTransport)
        self._client = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        # CryptoPanic mandates query auth. Suppress credential-bearing httpx/httpcore URL logs.
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            if not any(isinstance(f, _HTTPRedaction) for f in logger.filters):
                logger.addFilter(_HTTPRedaction())
        self.secrets = secret_values(settings)

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.news_http_timeout_seconds),
                limits=httpx.Limits(
                    max_connections=self.settings.news_max_concurrent, max_keepalive_connections=2
                ),
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                headers={"Accept-Encoding": "identity", "User-Agent": "MT5-AI-ReflexBot/0.6.0"},
            )
        return self._client

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        xml=False,
        conditional=False,
    ):
        valid_news_url(url)
        async with self._lock:
            delay = self.settings.news_request_spacing_seconds - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
        try:
            extra = {
                "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml"
                if xml
                else "application/json"
            }
            extra.update(headers or {})
            async with self._get_client().stream("GET", url, params=params, headers=extra) as response:
                if response.status_code == 304:
                    if not conditional:
                        raise NewsInvalid("unexpected_not_modified")
                    age = response.headers.get("age", "0")
                    if not re.fullmatch(r"\d{1,12}", age) or int(age) > self.settings.news_max_age_seconds:
                        raise NewsInvalid("old_or_invalid_304_age")
                    return HTTPPayload(304, b"", age_seconds=int(age))
                if response.status_code != 200:
                    if response.status_code in {401, 403, 404, 408, 429} or response.status_code >= 500:
                        raise NewsUnavailable("http_unavailable")
                    raise NewsInvalid("http_contract")
                media = response.headers.get("content-type", "").split(";")[0].strip().lower()
                accepted = (
                    {"application/xml", "text/xml", "application/rss+xml", "application/atom+xml"}
                    if xml
                    else {"application/json"}
                )
                if media not in accepted:
                    raise NewsInvalid("unsupported_media")
                if response.headers.get("content-encoding", "identity").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise NewsInvalid("unsupported_encoding")
                limit = self.settings.news_http_max_bytes
                for name in ("content-length", "age"):
                    value = response.headers.get(name)
                    if value is not None and (not re.fullmatch(r"\d{1,12}", value) or int(value) > limit):
                        raise NewsInvalid("invalid_length_or_age")
                age = int(response.headers.get("age", "0"))
                if age > self.settings.news_max_age_seconds:
                    raise NewsInvalid("old_http_cache")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > limit:
                        raise NewsInvalid("oversized_response")
                    raw.extend(chunk)
                validators = []
                for name in ("etag", "last-modified"):
                    value = response.headers.get(name)
                    if value is not None and (
                        len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    ):
                        raise NewsInvalid("unsafe_cache_validator")
                    validators.append(value)
                return HTTPPayload(200, bytes(raw), *validators, age)
        except (httpx.HTTPError, OSError):
            raise NewsUnavailable("network_or_timeout") from None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
