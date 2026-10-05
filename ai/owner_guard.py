"""Internal owner composition guard, not Telegram/initData authentication."""

from sqlalchemy import select

from core.models import BotState, OrderIntent, RiskState, Trade
from core.settings import Settings
from trading.runtime_state import TERMINAL_INTENT_STATES
from trading.types import TradingDisabled


def require_owner(settings: Settings, owner_id: int):
    if (
        type(owner_id) is not int
        or settings.telegram_owner_id is None
        or owner_id != settings.telegram_owner_id
    ):
        raise TradingDisabled("authenticated configured owner required")


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
