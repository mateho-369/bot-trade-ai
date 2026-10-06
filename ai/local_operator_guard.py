"""Local-operator checks for offline approval/projection helpers."""

from sqlalchemy import select

from core.local_operator import LocalOperator
from core.models import BotState, OrderIntent, RiskState, Trade
from trading.runtime_state import TERMINAL_INTENT_STATES
from trading.types import TradingDisabled


def require_local_operator(operator: LocalOperator) -> int:
    """Return a stable, non-secret audit tag for the current local OS operator."""
    if not isinstance(operator, LocalOperator):
        raise TradingDisabled("current local operator required")
    operator.require_current()
    return operator.operator_id


def require_stopped_flat(session):
    state = session.get(BotState, 1)
    if (
        state is None
        or state.desired_state not in {"paused", "killed"}
        or state.session_id is not None
        or state.heartbeat is not None
        or session.scalar(select(Trade.id).where(Trade.status != "closed").limit(1)) is not None
        or session.scalar(
            select(OrderIntent.id).where(OrderIntent.state.not_in(TERMINAL_INTENT_STATES)).limit(1)
        )
        is not None
        or session.scalar(select(RiskState.id).where(RiskState.reserved_risk_usd != 0).limit(1)) is not None
    ):
        raise TradingDisabled("stopped, reconciled-flat, zero-reservation database required")
    # Preserve kill/loss/high-water/capital counters. This is NOT broker-flat proof.
    return state
