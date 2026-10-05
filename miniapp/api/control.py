"""Downward safe stop; fenced resume; captured bot-owned close confirmations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services
from miniapp.api.schemas import CancelRequest, ClosePositionRequest, ConfirmationRequest, DirectRequest

router = APIRouter(prefix="/api", tags=["owner_control"])
Actor = Annotated[OwnerIdentity, Depends(owner)]


@router.post("/pause")
async def pause(request: Request, body: DirectRequest, identity: Actor):
    return await services(request).action(identity, "pause", {}, body.request_id)


@router.post("/kill")
async def kill(request: Request, body: DirectRequest, identity: Actor):
    return await services(request).action(identity, "kill", {}, body.request_id)


@router.post("/resume")
async def resume(request: Request, body: ConfirmationRequest, identity: Actor):
    return await services(request).action(identity, "resume", {}, body.request_id, body.confirmation_token)


@router.post("/close_position")
async def close_position(request: Request, body: ClosePositionRequest, identity: Actor):
    return await services(request).action(
        identity,
        "close_position",
        {"ticket": body.ticket, "position_identifier": body.position_identifier},
        body.request_id,
        body.confirmation_token,
    )


@router.post("/close_all")
async def close_all(request: Request, body: ConfirmationRequest, identity: Actor):
    return await services(request).action(identity, "close_all", {}, body.request_id, body.confirmation_token)


@router.post("/cancel_confirmation")
async def cancel(request: Request, body: CancelRequest, identity: Actor):
    return await services(request).confirm_button(identity, body.confirmation_token, cancel=True)
