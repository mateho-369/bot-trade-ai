"""Process-local bounded limiter; not a distributed or edge DDoS claim."""

import pytest

from app.owner_identity import OwnerInterfaceError
from app.rate_limits import WindowLimiter


def test_windows_and_reserved_budgets():
    limiter = WindowLimiter(4)
    limiter.require("owner", now=1, limit=1)
    with pytest.raises(OwnerInterfaceError, match="rate_limit_exceeded"):
        limiter.require("owner", now=2, limit=1)
    limiter.require("owner", now=2, limit=1, budget="safe_stop")
    limiter.require("owner", now=60, limit=1)
    assert limiter.size == 1


def test_key_churn_does_not_evict_active_limits():
    limiter = WindowLimiter(2)
    limiter.require("a", now=1, limit=1)
    limiter.require("b", now=1, limit=1)
    with pytest.raises(OwnerInterfaceError, match="capacity"):
        limiter.require("c", now=1, limit=1)
    with pytest.raises(OwnerInterfaceError, match="exceeded"):
        limiter.require("a", now=1, limit=1)
    limiter.require("c", now=61, limit=1)
    assert limiter.size == 1


@pytest.mark.parametrize("size", [0, -1, True, 10001])
def test_capacity_bounds(size):
    with pytest.raises(ValueError):
        WindowLimiter(size)
