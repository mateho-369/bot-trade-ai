"""Owner auth on EVERY protected endpoint; no cookies/query/body auth fallback."""

from __future__ import annotations

from fastapi import Request

from app.owner_identity import OwnerInterfaceError
from telegram_bot.miniapp_auth import validate_init_data


async def owner(request: Request):
    services = request.app.state.owner_services
    raw = request.headers.get("x-telegram-init-data", "")
    identity = validate_init_data(raw, services.settings, services.clock)
    services.limit_actor(
        identity, safe_stop=request.method == "POST" and request.url.path in {"/api/pause", "/api/kill"}
    )
    return identity


def services(request: Request):
    result = getattr(request.app.state, "owner_services", None)
    if result is None:
        raise OwnerInterfaceError("owner_services_unavailable", 503)
    return result
