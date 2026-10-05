"""Bounded, process-local fixed-window limits, with a separate safe-stop budget.

Not a distributed limiter. Deploy ONE owner API process; SQL still fences actions
across processes. TLS edge needs its own connection/body/unauthenticated limits.
Forwarded IP headers are deliberately never trusted here.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from app.owner_identity import OwnerInterfaceError


class WindowLimiter:
    def __init__(self, maximum_keys: int = 2048):
        if type(maximum_keys) is not int or not 1 <= maximum_keys <= 10000:
            raise ValueError("bounded rate-limit key count required")
        self.maximum_keys = maximum_keys
        self._buckets: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()
        self._lock = Lock()

    def require(self, key: str, *, now: float, limit: int, budget: str = "ordinary") -> None:
        if type(limit) is not int or limit < 1 or len(key) > 128:
            raise ValueError("invalid limiter parameters")
        window = int(now // 60)
        bucket = (budget, key)
        with self._lock:
            # Never evict a CURRENT bucket to let key churn erase an attack's budget.
            for old in tuple(self._buckets):
                if self._buckets[old][0] != window:
                    del self._buckets[old]
            current_window, count = self._buckets.get(bucket, (window, 0))
            if bucket not in self._buckets and len(self._buckets) >= self.maximum_keys:
                raise OwnerInterfaceError("rate_limit_capacity", 429)
            if current_window == window and count >= limit:
                raise OwnerInterfaceError("rate_limit_exceeded", 429)
            self._buckets[bucket] = (window, count + 1 if current_window == window else 1)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buckets)
