"""Read-only dashboard endpoint; actor is derived from signed initData."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
async def dashboard(request: Request, identity: Annotated[OwnerIdentity, Depends(owner)]):
    return await services(request).read(identity, "dashboard")
