"""Local-only principal used by typed operator controls; there is no remote control transport."""

from __future__ import annotations

import getpass
import hashlib
import os
from dataclasses import dataclass

from trading.types import TradingDisabled


@dataclass(frozen=True, slots=True)
class LocalOperator:
    """A process-bound identity for calls made through the local operator CLI.

    This is intentionally not a credential or web identity. It fences typed control
    transitions from data supplied by a network request; only the local CLI creates
    one in production, and every transition checks the current OS user and process.
    """

    username: str
    process_id: int

    @classmethod
    def current(cls) -> LocalOperator:
        username = getpass.getuser()
        if not username:
            raise TradingDisabled("local operating-system user could not be verified")
        return cls(username=username, process_id=os.getpid())

    @property
    def operator_id(self) -> int:
        """Stable non-secret audit tag for integer legacy evidence fields."""
        digest = hashlib.sha256(self.username.casefold().encode("utf-8")).digest()
        return (int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)) or 1

    def require_current(self) -> None:
        if (
            not isinstance(self, LocalOperator)
            or self.process_id != os.getpid()
            or not self.username
            or self.username != getpass.getuser()
        ):
            raise TradingDisabled("current local operator required")
