"""Manage only reconciled ownership; protective work continues while paused/killed."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from trading.execution import ExecutionEngine
from trading.risk_types import PositionReview
from trading.types import BrokerError, TradingDisabled


class PositionManager:
    def __init__(self, engine: ExecutionEngine):
        self.engine = engine
        self._lock = asyncio.Lock()

    async def cycle(self, reviews: Mapping[int, PositionReview] | None = None) -> dict:
        async with self._lock:
            summary = await self.engine.reconcile()
            owners = await asyncio.to_thread(self.engine.logger.owned, self.engine.account_key)
            outcomes = []
            for owned in owners:
                # Fresh IDs/volume; never adopt by magic/comment/symbol alone.
                positions = await self.engine.broker.get_positions()
                position = next(
                    (
                        item
                        for item in positions
                        if item.identifier == owned.identifier
                        and item.ticket == owned.ticket
                        and item.volume == owned.volume
                    ),
                    None,
                )
                if position is None:
                    continue
                try:
                    candles = None
                    if self.engine.settings.atr_trailing_enabled:
                        try:
                            candles = await self.engine.broker.get_candles(
                                position.symbol,
                                self.engine.settings.primary_timeframe,
                                self.engine.settings.candle_lookback,
                            )
                        except BrokerError:
                            pass  # Locks still work; don't invent ATR when history is unavailable.
                    plan = await self.engine.trailing.plan(position, owned, candles=candles)
                    if plan:
                        result = await self.engine.protect_sl(
                            position.ticket, position.identifier, plan.sl, lock_level=plan.lock_level
                        )
                        outcomes.append(
                            {
                                "identifier": position.identifier,
                                "operation": "sl",
                                "status": result.status.value,
                                "reason": plan.reason,
                                "requested_lock_level": plan.lock_level,
                            }
                        )
                    # Refresh ownership/state AFTER protection. Never pair an old
                    # SL with a TP extension or widen it to make the extension fit.
                    positions = await self.engine.broker.get_positions()
                    position = next((item for item in positions if item.identifier == owned.identifier), None)
                    if position:
                        tp = await self.engine.trailing.extension(
                            position, owned, (reviews or {}).get(owned.identifier)
                        )
                        if tp is not None:
                            result = await self.engine.extend_tp(
                                position.ticket, position.identifier, tp, reviews[owned.identifier]
                            )
                            outcomes.append(
                                {
                                    "identifier": position.identifier,
                                    "operation": "tp",
                                    "status": result.status.value,
                                }
                            )
                except BrokerError as exc:
                    # Exception class only, not raw broker/provider bodies.
                    outcomes.append(
                        {
                            "identifier": owned.identifier,
                            "operation": "deferred",
                            "error_kind": type(exc).__name__,
                        }
                    )
                    await asyncio.to_thread(
                        self.engine.database.audit, "position.protection_deferred", "positions", outcomes[-1]
                    )
            return {"reconciliation": summary, "managed_positions": len(owners), "outcomes": outcomes}

    async def close(self, identifier: int, *, owner_id: int, idempotency_key: str | None = None):
        # The caller must supply an authenticated principal from Part 9.
        if (
            self.engine.settings.telegram_owner_id is None
            or type(owner_id) is not int
            or owner_id != self.engine.settings.telegram_owner_id
        ):
            raise TradingDisabled("only the authenticated configured owner may request a position close")
        await self.engine.reconcile()
        owners = await asyncio.to_thread(self.engine.logger.owned, self.engine.account_key)
        owned = next((item for item in owners if item.identifier == identifier), None)
        if owned is None:
            raise TradingDisabled("requested position is not durably owned")
        await asyncio.to_thread(
            self.engine.database.audit,
            "owner.close_requested",
            "owner",
            {"identifier": identifier, "owner_id": owner_id},
        )
        return await self.engine.close_owned(owned.ticket, identifier, idempotency_key=idempotency_key)
