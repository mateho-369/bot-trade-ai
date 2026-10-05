"""Read stored proposals; approve/reject NEVER applies settings or trades."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services
from miniapp.api.schemas import DirectRequest, FallbackModeRequest, RejectSuggestionRequest, SuggestionRequest

router = APIRouter(prefix="/api", tags=["proposals"])
Actor = Annotated[OwnerIdentity, Depends(owner)]


@router.get("/ai_suggestions")
async def suggestions(
    request: Request,
    identity: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    return await services(request).read(identity, "suggestions", limit=limit, offset=offset)


@router.post("/approve_suggestion")
async def approve(request: Request, body: SuggestionRequest, identity: Actor):
    return await services(request).action(
        identity,
        "approve_suggestion",
        {"suggestion_id": body.suggestion_id},
        body.request_id,
        body.confirmation_token,
    )


@router.post("/reject_suggestion")
async def reject(request: Request, body: RejectSuggestionRequest, identity: Actor):
    return await services(request).action(
        identity, "reject_suggestion", {"suggestion_id": body.suggestion_id}, body.request_id
    )


@router.get("/ai_journal")
async def ai_journal(
    request: Request,
    identity: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    """AI Decision Journal: every AI/rule decision, trailing lock-first action and lesson."""
    return await services(request).read(identity, "ai_journal", limit=limit, offset=offset)


@router.get("/ai_fallback")
async def ai_fallback(request: Request, identity: Actor):
    """Owner AI_FALLBACK_MODE, AI health and the limits currently enforced."""
    return await services(request).read(identity, "ai_fallback")


@router.post("/ai_fallback")
async def set_ai_fallback(request: Request, body: FallbackModeRequest, identity: Actor):
    """Owner-only, audited. Never touches the kill switch, risk checks or the news block."""
    action = "ai_fallback_block" if body.mode == "BLOCK_ON_AI_FAILURE" else "ai_fallback_technical"
    return await services(request).action(identity, action, {}, body.request_id)


@router.get("/limits")
async def limits(request: Request, identity: Actor):
    """AI-dynamic limits (effective), owner defaults, AI bounds and hard caps."""
    return await services(request).read(identity, "limits")


@router.post("/ai_reset")
async def ai_reset(request: Request, body: DirectRequest, identity: Actor):
    """Reset every AI adjustment to the owner defaults (audited)."""
    return await services(request).action(identity, "ai_reset", {}, body.request_id)


@router.get("/ai_stats")
async def ai_stats(request: Request, identity: Actor):
    """Per-AI-label approvals, rejections, trades, win rate, net profit, avg confidence, failures."""
    return await services(request).read(identity, "ai_stats")


@router.get("/audit")
async def trade_audit(request: Request, identity: Actor):
    """Read-only trade audit: every trade must match a valid AI approval."""
    return await services(request).read(identity, "audit")
