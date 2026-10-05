"""FastAPI factory; import/startup verifies SQL only, never starts trading/Telegram.

Standalone owner API is locked without Telegram credentials. An injected current
ExecutionEngine is required for resume/close, not auto-created by this factory.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.owner_identity import OwnerInterfaceError
from miniapp.api import ai, control, dashboard, logs, news, settings, trades
from miniapp.security import OwnerSecurityMiddleware, error_content

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"styles.css": "text/css", "app.js": "text/javascript", "telegram_loader.js": "text/javascript"}


def create_app(configuration, owner_services, *, preview_only=False, telegram_transport=None):
    if configuration.safety_fingerprint() != owner_services.settings.safety_fingerprint():
        raise ValueError("API/owner settings diverge")
    if preview_only != owner_services.preview_only:
        raise ValueError("preview mode must be explicitly matched in trusted composition")
    if telegram_transport is not None and (
        telegram_transport.services is not owner_services
        or not configuration.telegram_use_webhook
        or preview_only
    ):
        raise ValueError("explicit webhook-only owner transport required")

    @asynccontextmanager
    async def lifespan(application):
        await asyncio.to_thread(owner_services.database.verify_schema)
        application.state.ready = True
        try:
            yield
        finally:
            application.state.ready = False
            # Injected engine/bot/database lifetimes belong to their explicit composer.
            # API shutdown cannot secretly release a running broker lease.

    application = FastAPI(
        title="MT5 AI ReflexBot owner interface",
        version="0.7.0",
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.owner_services = owner_services
    application.state.ready = False

    @application.exception_handler(OwnerInterfaceError)
    async def owner_error(request: Request, error: OwnerInterfaceError):
        return JSONResponse(
            error_content(error.code),
            status_code=error.status,
            headers={"Retry-After": "60"} if error.status == 429 else None,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(error_content("invalid_request"), status_code=422)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(
            error_content("not_found" if error.status_code == 404 else "request_denied"),
            status_code=error.status_code,
        )

    @application.get("/healthz", include_in_schema=False)
    async def healthz():
        return JSONResponse(
            {
                "status": "ok" if application.state.ready else "starting",
                "interface_only": True,
                "not_trading_health": True,
            },
            status_code=200 if application.state.ready else 503,
        )

    @application.get("/", include_in_schema=False)
    async def index():
        template = await asyncio.to_thread((STATIC / "index.html").read_text, encoding="utf-8")
        return HTMLResponse(template.replace("__PREVIEW__", "true" if preview_only else "false"))

    @application.get("/static/{name}", include_in_schema=False)
    async def asset(name: str):
        if name not in ASSETS or not (STATIC / name).is_file() or (STATIC / name).is_symlink():
            raise HTTPException(404)
        return FileResponse(STATIC / name, media_type=ASSETS[name])

    for router in (
        dashboard.router,
        trades.router,
        news.router,
        ai.router,
        settings.router,
        logs.router,
        control.router,
    ):
        application.include_router(router)

    if preview_only:
        from miniapp.preview_data import preview_data

        @application.get("/preview/data", include_in_schema=False)
        async def fixture_data():
            # Constant/artificial records, never reads the owner database or issues auth.
            return preview_data()

    if telegram_transport is not None:

        @application.post("/telegram/webhook", include_in_schema=False)
        async def webhook(request: Request):
            return await telegram_transport.feed_webhook(request)

    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(configuration.api_trusted_hosts), www_redirect=False
    )
    application.add_middleware(
        OwnerSecurityMiddleware, settings=configuration, clock=owner_services.clock, preview_only=preview_only
    )
    return application
