"""Bounded append-only audit metadata, not log-file reads or raw payloads."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/logs")
async def logs(
    request: Request,
    identity: Annotated[OwnerIdentity, Depends(owner)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    return await services(request).read(identity, "logs", limit=limit, offset=offset)
