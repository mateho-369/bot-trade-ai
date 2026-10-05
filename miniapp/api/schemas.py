"""Strict action DTOs: IDs/nonce only, never actor/risk/live/order parameters."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

RequestID = Annotated[
    StrictStr, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
ConfirmationToken = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
PositiveID = Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class DirectRequest(StrictRequest):
    request_id: RequestID


class ConfirmationRequest(DirectRequest):
    confirmation_token: ConfirmationToken | None = None


class ClosePositionRequest(ConfirmationRequest):
    ticket: PositiveID
    position_identifier: PositiveID


class SuggestionRequest(ConfirmationRequest):
    suggestion_id: PositiveID


class RejectSuggestionRequest(DirectRequest):
    suggestion_id: PositiveID


class CancelRequest(StrictRequest):
    confirmation_token: ConfirmationToken


class AckAlertsRequest(DirectRequest):
    alert_id: PositiveID | None = None  # None = acknowledge every unacknowledged alert.


class FallbackModeRequest(DirectRequest):
    mode: Literal["BLOCK_ON_AI_FAILURE", "TECHNICAL_ONLY"]
