"""Exact durable OPEN replay decoding. Decode is not permission to resend."""

from datetime import datetime
from decimal import Decimal

from strategy.base_strategy import ROUTER_ID
from trading.types import BrokerCommand, MarketOrder, Operation, Side, TradingDisabled


def original_signal_command(payload: dict, signal_id: int, key: str) -> BrokerCommand:
    try:
        data = payload["command"]
        order = dict(data["order"])
        if (
            data["operation"] != "open"
            or data["idempotency_key"] != key
            or order["idempotency_key"] != key
            or order["strategy"] != ROUTER_ID
            or payload["context"]["signal_id"] != signal_id
        ):
            raise ValueError
        for name in ("volume", "reference_price", "sl", "tp"):
            if not isinstance(order[name], str):
                raise ValueError
            order[name] = Decimal(order[name])
        order["side"] = Side(order["side"])
        order["created_at"] = datetime.fromisoformat(order["created_at"])
        return BrokerCommand(
            Operation.OPEN, key, datetime.fromisoformat(data["created_at"]), order=MarketOrder(**order)
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        raise TradingDisabled("durable signal intent cannot be decoded safely; no replay") from None
