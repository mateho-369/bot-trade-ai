"""Trusted transport boundary, not a login by user-supplied integer.

Only the HMAC verifier and private-chat middleware issue these identities.
Python composition is trusted; constructing a dataclass is NOT remote auth.
No token/raw initData is retained in the identity or audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from core.settings import Settings
from trading.types import Clock


class OwnerInterfaceError(Exception):
    """Fixed, non-reflective transport errors; never include bodies/SDK messages."""

    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    owner_id: int
    transport: Literal["miniapp", "telegram"]
    credential_hash: str
    config_hash: str
    issued_at: datetime
    expires_at: datetime

    def require(self, settings: Settings, clock: Clock) -> None:
        if (
            type(self.owner_id) is not int
            or settings.telegram_owner_id is None
            or self.owner_id != settings.telegram_owner_id
            or not settings.telegram_bot_token.get_secret_value()
            or self.transport not in {"miniapp", "telegram"}
            or self.config_hash != settings.safety_fingerprint()
            or len(self.credential_hash) != 64
            or any(c not in "0123456789abcdef" for c in self.credential_hash)
            or self.issued_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.issued_at >= self.expires_at
        ):
            raise OwnerInterfaceError("owner_authentication_required", 401)
        now = clock.now()
        if now >= self.expires_at:
            raise OwnerInterfaceError("owner_session_expired", 401)
        if (self.issued_at - now).total_seconds() > settings.telegram_auth_future_skew_seconds:
            raise OwnerInterfaceError("owner_authentication_required", 401)
