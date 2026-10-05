"""Read stored proposals; approve/reject NEVER applies settings or trades."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services
from miniapp.api.schemas import RejectSuggestionRequest, SuggestionRequest

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
