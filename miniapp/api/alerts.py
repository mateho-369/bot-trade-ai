"""Alert Center: owner-only alert list (last 100, filterable) and acknowledgement."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.owner_identity import OwnerIdentity
from miniapp.api.dependencies import owner, services
from miniapp.api.schemas import AckAlertsRequest

router = APIRouter(prefix="/api", tags=["alerts"])
Actor = Annotated[OwnerIdentity, Depends(owner)]


@router.get("/alerts")
async def alerts(
    request: Request,
    identity: Actor,
    level: Annotated[Literal["INFO", "WARNING", "ERROR", "CRITICAL"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    return await services(request).read(identity, "alerts", limit=limit, offset=offset, level=level)


@router.post("/alerts/ack")
async def acknowledge(request: Request, body: AckAlertsRequest, identity: Actor):
    if body.alert_id is None:
        return await services(request).action(identity, "ack_alerts", {}, body.request_id)
    return await services(request).action(identity, "ack_alert", {"alert_id": body.alert_id}, body.request_id)
