"""Bounded SQL ledger lists, NOT broker position polls."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services

router = APIRouter(prefix="/api", tags=["ledger"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10000)]
Actor = Annotated[OwnerIdentity, Depends(owner)]


@router.get("/positions")
async def positions(request: Request, identity: Actor, limit: Limit = 50, offset: Offset = 0):
    return await services(request).read(identity, "positions", limit=limit, offset=offset)


@router.get("/trades")
async def trades(request: Request, identity: Actor, limit: Limit = 50, offset: Offset = 0):
    return await services(request).read(identity, "trades", limit=limit, offset=offset)


@router.get("/signals")
async def signals(request: Request, identity: Actor, limit: Limit = 50, offset: Offset = 0):
    return await services(request).read(identity, "signals", limit=limit, offset=offset)
