"""Manage only reconciled ownership; protective work continues while paused/killed."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from core.local_operator import LocalOperator
from trading.execution import ExecutionEngine
from trading.risk_types import PositionReview
from trading.types import BrokerError, TradingDisabled


class PositionManager:
    def __init__(self, engine: ExecutionEngine, *, adaptive=None):
        """``adaptive``: optional trading.ai_adaptive_trailing.AdaptiveTrailing (lock-first AI layer).

        Without it the mechanical 30/60/90 + ATR trailing runs exactly as before.
        """
        self.engine, self.adaptive = engine, adaptive
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
                    if self.adaptive is not None:
                        # Lock-first: the mechanical lock is sent BEFORE any AI consultation.
                        outcomes.extend(await self.adaptive.manage(position, owned, candles=candles))
                        plan = None
                    else:
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

    async def close(self, identifier: int, *, operator: LocalOperator, idempotency_key: str | None = None):
        # The current local OS user is required; no network principal is accepted.
        if not isinstance(operator, LocalOperator):
            raise TradingDisabled("current local operator required")
        operator.require_current()
        operator_id = operator.operator_id
        await self.engine.reconcile()
        owners = await asyncio.to_thread(self.engine.logger.owned, self.engine.account_key)
        owned = next((item for item in owners if item.identifier == identifier), None)
        if owned is None:
            raise TradingDisabled("requested position is not durably owned")
        await asyncio.to_thread(
            self.engine.database.audit,
            "local_operator.close_requested",
            "local_operator",
            {"identifier": identifier, "operator_id": operator_id},
        )
        return await self.engine.close_owned(owned.ticket, identifier, idempotency_key=idempotency_key)
