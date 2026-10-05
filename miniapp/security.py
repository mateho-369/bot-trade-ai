"""Pure ASGI request bounds, exact-origin policy, safe errors and response headers.

The public HTTPS Origin is compared to configured origin/Host, NOT the ASGI http
scheme behind a TLS proxy. Never trust X-Forwarded-Host/IP from arbitrary peers.
Signed initData goes in one header only. This is not an edge DDoS/slowloris guard.
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import parse_qsl, urlsplit

from app.owner_identity import OwnerInterfaceError
from app.rate_limits import WindowLimiter
from telegram_bot.miniapp_auth import BAD_PERCENT, strict_json

ALERT_LEVELS = frozenset({"INFO", "WARNING", "ERROR", "CRITICAL"})  # Only /api/alerts filter.
SENSITIVE_HEADERS = {
    b"host",
    b"origin",
    b"content-length",
    b"content-type",
    b"content-encoding",
    b"x-telegram-init-data",
    b"x-telegram-bot-api-secret-token",
    b"authorization",
    b"x-forwarded-proto",
}
SAFE_METHODS = {"GET", "POST", "OPTIONS", "HEAD"}


def error_content(code):
    return {
        "error": {
            "code": code,
            "message": "Request denied. Reopen from the owner bot if your session expired.",
        }
    }


def origin_of(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += ":" + str(port)
    return parsed.scheme + "://" + host


class OwnerSecurityMiddleware:
    def __init__(self, app, *, settings, clock, preview_only=False):
        self.app, self.settings, self.clock, self.preview_only = app, settings, clock, preview_only
        self.limiter = WindowLimiter(settings.api_max_rate_limit_keys)
        self.stop_limiter = WindowLimiter(min(128, settings.api_max_rate_limit_keys))
        self.origins = {origin_of(o) for o in settings.api_cors_origins}
        self.public_origin = (
            origin_of(settings.telegram_miniapp_url) if settings.telegram_miniapp_url else None
        )
        if self.public_origin:
            self.origins.add(self.public_origin)

    def _headers(self, scope):
        raw = scope.get("headers", [])
        if sum(len(k) + len(v) + 4 for k, v in raw) > self.settings.api_max_header_bytes:
            raise OwnerInterfaceError("request_headers_too_large", 431)
        result = {}
        for key, value in raw:
            key = key.lower()
            if key in SENSITIVE_HEADERS and key in result:
                raise OwnerInterfaceError("duplicate_security_header", 400)
            if b"\x00" in value or b"\r" in value or b"\n" in value:
                raise OwnerInterfaceError("invalid_request_header", 400)
            result[key] = value.decode("latin-1")
        if len(result.get(b"x-telegram-init-data", "")) > self.settings.telegram_initdata_max_bytes:
            raise OwnerInterfaceError("request_headers_too_large", 431)
        return result

    def _query(self, scope):
        raw = scope.get("query_string", b"")
        if len(raw) > 512:
            raise OwnerInterfaceError("request_query_too_large", 414)
        try:
            query = raw.decode("ascii")
            if BAD_PERCENT.search(query) or any(ord(c) < 33 or ord(c) == 127 for c in query):
                raise ValueError
            pairs = parse_qsl(
                query,
                strict_parsing=True,
                keep_blank_values=True,
                max_num_fields=8,
                encoding="utf-8",
                errors="strict",
            )
            keys = [key for key, _ in pairs]
            if len(keys) != len(set(keys)) or any(key not in {"limit", "offset", "level"} for key in keys):
                raise ValueError
            if any(
                value not in ALERT_LEVELS
                if key == "level"
                else not value.isascii() or not value.isdigit() or len(value) > 5
                for key, value in pairs
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise OwnerInterfaceError("invalid_request_query", 400) from None

    def _origin(self, headers):
        raw = headers.get(b"origin")
        if not raw:
            if headers.get(b"sec-fetch-site") == "cross-site":
                raise OwnerInterfaceError("origin_not_allowed", 403)
            return None
        try:
            parsed = urlsplit(raw)
            normalized = origin_of(raw)
            if parsed.path not in {"", "/"}:
                raise ValueError
            host = headers.get(b"host", "").lower()
            same_host = urlsplit(normalized).netloc == host
            if normalized in self.origins:
                return normalized
            # Preview and loopback diagnostics do not claim HTTPS deployment.
            local = parsed.hostname in {"localhost", "127.0.0.1"}
            if (
                self.public_origin is None
                and same_host
                and (parsed.scheme == "https" or self.preview_only or local)
            ):
                return normalized
        except ValueError:
            pass
        raise OwnerInterfaceError("origin_not_allowed", 403)

    def _require_tls(self, scope, headers):
        if self.preview_only or scope.get("scheme") == "https":
            return
        client = scope.get("client")
        try:
            peer = str(ipaddress.ip_address(client[0])) if client else None
        except ValueError:
            peer = None
        configured_https = bool(self.public_origin or self.settings.telegram_webhook_url)
        if (
            configured_https
            and peer in self.settings.api_trusted_proxy_ips
            and headers.get(b"x-forwarded-proto") == "https"
        ):
            return
        # Explicit loopback diagnostics without a public deployment URL only.
        host = headers.get(b"host", "").split(":", 1)[0]
        if not configured_https and peer in {"127.0.0.1", "::1"} and host in {"localhost", "127.0.0.1"}:
            return
        raise OwnerInterfaceError("https_transport_required", 403)

    def response_headers(self, origin=None):
        ancestors = "*" if self.preview_only else "'self' https://web.telegram.org https://*.telegram.org"
        csp = (
            "default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; object-src 'none'; frame-ancestors " + ancestors
        )
        result = [
            (b"cache-control", b"no-store, max-age=0"),
            (b"pragma", b"no-cache"),
            (b"content-security-policy", csp.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
        ]
        if self.public_origin and not self.preview_only:
            result.append((b"strict-transport-security", b"max-age=31536000"))
        if origin is not None:
            result.extend([(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")])
        return result

    async def deny(self, scope, send, code, status, *, origin=None):
        body = json.dumps(error_content(code), separators=(",", ":")).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            *self.response_headers(origin),
        ]
        if status == 429:
            headers.append((b"retry-after", b"60"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        origin = None
        try:
            if method not in SAFE_METHODS or len(path) > 256:
                raise OwnerInterfaceError("method_or_path_not_allowed", 405)
            headers = self._headers(scope)
            self._query(scope)
            api = path.startswith("/api/")
            webhook = path == "/telegram/webhook"
            if api or webhook:
                self._require_tls(scope, headers)
            if api:
                if b"authorization" in headers or b"cookie" in headers:
                    raise OwnerInterfaceError("unsupported_authentication_transport", 400)
                origin = self._origin(headers)
                if self.preview_only and method == "POST":
                    raise OwnerInterfaceError("synthetic_preview_is_read_only", 403)
            if webhook and b"origin" in headers:
                raise OwnerInterfaceError("browser_webhook_not_allowed", 403)
            client = scope.get("client")
            key = str(client[0])[:128] if client else "unknown_client"
            stop = api and method == "POST" and path in {"/api/pause", "/api/kill"}
            (self.stop_limiter if stop else self.limiter).require(
                key,
                now=self.clock.now().timestamp(),
                budget="http",
                limit=max(40, self.settings.api_safe_stop_rate_per_minute * 4)
                if stop
                else max(120, self.settings.api_rate_limit_per_minute * 3),
            )
            if method == "OPTIONS" and api:
                if origin is None or headers.get(b"access-control-request-method") not in {"GET", "POST"}:
                    raise OwnerInterfaceError("preflight_not_allowed", 403)
                requested = {
                    v.strip().lower()
                    for v in headers.get(b"access-control-request-headers", "").split(",")
                    if v.strip()
                }
                if not requested.issubset({"content-type", "x-telegram-init-data"}):
                    raise OwnerInterfaceError("preflight_not_allowed", 403)
                await send(
                    {
                        "type": "http.response.start",
                        "status": 204,
                        "headers": [
                            *self.response_headers(origin),
                            (b"access-control-allow-methods", b"GET, POST"),
                            (b"access-control-allow-headers", b"Content-Type, X-Telegram-Init-Data"),
                            (b"access-control-max-age", b"300"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            length = headers.get(b"content-length")
            if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 10):
                raise OwnerInterfaceError("invalid_content_length", 400)
            if length is not None and int(length) > self.settings.api_max_request_bytes:
                raise OwnerInterfaceError("request_body_too_large", 413)
            if headers.get(b"content-encoding", "identity").lower() != "identity":
                raise OwnerInterfaceError("compressed_request_not_allowed", 415)
            body = bytearray()
            if method == "POST":
                if headers.get(b"content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise OwnerInterfaceError("json_body_required", 415)
                while True:
                    chunk = await receive()
                    if chunk["type"] == "http.disconnect":
                        return
                    if chunk["type"] != "http.request":
                        raise OwnerInterfaceError("invalid_request_body", 400)
                    body.extend(chunk.get("body", b""))
                    if len(body) > self.settings.api_max_request_bytes:
                        raise OwnerInterfaceError("request_body_too_large", 413)
                    if not chunk.get("more_body", False):
                        break
                if length is not None and len(body) != int(length):
                    raise OwnerInterfaceError("content_length_mismatch", 400)
                try:
                    decoded = strict_json(bytes(body).decode("utf-8"))
                    if type(decoded) is not dict:
                        raise ValueError
                except (ValueError, UnicodeError, TypeError, RecursionError):
                    raise OwnerInterfaceError("invalid_json_body", 400) from None
            elif length is not None and int(length) > 0:
                raise OwnerInterfaceError("body_not_allowed_for_method", 400)
        except OwnerInterfaceError as error:
            await self.deny(scope, send, error.code, error.status, origin=origin)
            return
        sent_body = False

        async def bounded_receive():
            nonlocal sent_body
            if method != "POST":
                return await receive()
            if sent_body:
                return await receive()
            sent_body = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        started = False

        async def secure_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                keys = {k for k, _ in self.response_headers(origin)}
                existing = [(k, v) for k, v in message.get("headers", []) if k.lower() not in keys]
                message = {**message, "headers": [*existing, *self.response_headers(origin)]}
            await send(message)

        try:
            await self.app(scope, bounded_receive, secure_send)
        except Exception:
            # Do not let uvicorn print provider/SQL/request exception values.
            if not started:
                await self.deny(scope, send, "owner_services_unavailable", 503, origin=origin)
            else:
                raise RuntimeError("owner interface response failed") from None
