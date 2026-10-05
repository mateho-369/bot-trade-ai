"""Allowlisted settings only; no generic settings or live-mode mutation route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def settings_view(request: Request, identity: Annotated[OwnerIdentity, Depends(owner)]):
    return await services(request).read(identity, "settings")
