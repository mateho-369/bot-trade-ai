"""Read-only learning export from positive original-intent/complete-fill proof, not DB labels alone."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from ai.artifacts import save_immutable
from ai.dataset import LearningDataset, TradeSample
from ai.feature_engineering import FeatureEngineering
from ai.model_trainer import ModelTrainer, TrainingResult
from core.database import Database
from core.models import BrokerDeal, OrderIntent, Signal, Trade
from core.security import canonical_json, sha256_json
from core.settings import Settings
from strategy.signal_execution import original_signal_command
from strategy.signal_store import SignalStore
from trading.risk_types import DecisionContext, RuntimeProfile
from trading.types import BrokerError, Clock, TradingDisabled


@dataclass(frozen=True, slots=True)
class ExportResult:
    dataset: LearningDataset | None
    considered: int
    skipped: tuple[tuple[str, int], ...]


class LearningEngine:
    def __init__(self, database: Database, settings: Settings, clock: Clock, profile: RuntimeProfile):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        self.store = SignalStore(database, settings, clock, profile)

    def _sample(self, trade, intent, signal, legs, unallocated):
        try:
            now = self.clock.now()
            if (
                intent is None
                or signal is None
                or intent.state != "reconciled"
                or trade.close_time is None
                or trade.close_time > now
                or intent.account_key != trade.account_key
                or intent.mode != trade.mode
                or trade.currency != "USD"
            ):
                raise ValueError
            context = self.store.approved_context(signal)
            values = trade.features_json
            meta = values["execution"]
            original = DecisionContext.from_dict(values["decision"])
            command = original_signal_command(intent.request, signal.id, intent.idempotency_key)
            saved_context = DecisionContext.from_dict(intent.request["context"])
            if (
                context.digest != original.digest
                or saved_context.digest != original.digest
                or intent.request["request_hash"] != command.request_hash
                or meta.get("version") != 1
                or meta.get("profit_usd_verified") is not True
                or meta.get("data_source") != self.profile.data_source.value
                or meta.get("code_hash") != self.profile.code_hash
                or meta.get("model_sha256") != self.profile.model_sha256
                or meta.get("strategy_config_hash") != self.settings.strategy_fingerprint()
                or meta.get("intent_key") != intent.idempotency_key
                or trade.config_hash != self.settings.safety_fingerprint()
                or command.order.symbol != trade.symbol
                or command.order.side.value != trade.direction
                or signal.id != context.signal_id
                or context.source != self.profile.data_source
                or not context.observed_at <= trade.open_time < trade.close_time
                or any(d.type.startswith("unknown") or d.entry == "inout" for d in legs)
            ):
                raise ValueError
            entries = [d for d in legs if d.entry == "in" and d.type in {"buy", "sell"}]
            exits = [d for d in legs if d.entry in {"out", "out_by"} and d.type in {"buy", "sell"}]
            volume = Decimal(meta["original_volume"])
            if (
                not entries
                or not exits
                or volume <= 0
                or {d.ticket for d in entries} != set(meta["entry_deal_tickets"])
                or sum((d.volume for d in entries), Decimal(0)) != volume
                or sum((d.volume for d in exits), Decimal(0)) != volume
                or min(d.time for d in entries) != trade.open_time
                or max(d.time for d in exits) != trade.close_time
                or any(
                    d.currency != "USD"
                    or d.symbol != trade.symbol
                    or d.magic != self.settings.mt5_magic_number
                    or not trade.open_time <= d.time <= trade.close_time
                    for d in legs
                )
                or any(d.type != trade.direction or d.order_ticket != meta["order_ticket"] for d in entries)
            ):
                raise ValueError
            for row in unallocated:
                if (
                    trade.open_time <= row.time <= trade.close_time
                    and row.type not in {"balance", "credit"}
                    and row.profit + row.commission + row.swap + row.fee != 0
                ):
                    raise ValueError  # Never allocate unidentified account charges to convenient labels.
            net = sum((d.profit + d.commission + d.swap + d.fee for d in legs), Decimal(0))
            if net != trade.profit or net != trade.profit_usd:
                raise ValueError
            proof = {
                "trade_id": trade.id,
                "intent_key": intent.idempotency_key,
                "request_hash": command.request_hash,
                "decision_digest": context.digest,
                "legs": [
                    {
                        "ticket": d.ticket,
                        "order": d.order_ticket,
                        "entry": d.entry,
                        "type": d.type,
                        "volume": str(d.volume),
                        "time": d.time.isoformat(),
                        "profit": str(d.profit),
                        "commission": str(d.commission),
                        "swap": str(d.swap),
                        "fee": str(d.fee),
                    }
                    for d in legs
                ],
            }
            vector = FeatureEngineering.from_snapshot(
                context.features["feature_snapshot"], context.features["technical"]
            )
            available = max(trade.close_time, *(d.ingested_at for d in legs))
            return TradeSample(
                sha256_json({"account": trade.account_key, "mode": trade.mode, "trade": trade.id}),
                context.observed_at,
                trade.open_time,
                trade.close_time,
                available,
                trade.symbol,
                trade.direction,
                vector,
                net,
                trade.initial_risk_usd,
                sha256_json(proof),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError, BrokerError):
            raise TradingDisabled("closed trade lacks complete bound learning proof") from None

    def export(self, account_key: str) -> ExportResult:
        self.database.verify_schema()
        if not isinstance(account_key, str) or not 1 <= len(account_key) <= 160:
            raise TradingDisabled("bounded explicit account scope required")
        now = self.clock.now()
        skipped, samples = Counter(), []
        with self.database.session() as session:
            trades = session.scalars(
                select(Trade)
                .where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.status == "closed",
                    Trade.close_time <= now,
                )
                .order_by(Trade.open_time, Trade.id)
                .limit(self.settings.model_max_dataset_rows + 1)
            ).all()
            if len(trades) > self.settings.model_max_dataset_rows:
                raise TradingDisabled("learning export exceeds cap; review a bounded cohort explicitly")
            ledger = session.scalars(
                select(BrokerDeal)
                .where(
                    BrokerDeal.account_key == account_key,
                    BrokerDeal.mode == self.settings.mode.value,
                )
                .order_by(BrokerDeal.time, BrokerDeal.ticket)
                .limit(100001)
            ).all()
            if len(ledger) > 100000:
                raise TradingDisabled("learning ledger exceeds bounded export; archive/review it")
            grouped = defaultdict(list)
            for d in ledger:
                grouped[d.position_identifier].append(d)
            for trade in trades:
                try:
                    decision = trade.features_json["decision"]
                    signal = session.get(Signal, decision["signal_id"])
                    sample = self._sample(
                        trade,
                        session.get(OrderIntent, trade.order_intent_id),
                        signal,
                        grouped[trade.position_identifier],
                        grouped[None],
                    )
                    if sample.label_available_at > now:
                        skipped["label_not_available"] += 1
                        continue
                    samples.append(sample)
                except (TradingDisabled, KeyError, TypeError, ValueError):
                    skipped["unproven_or_incompatible_trade"] += 1
        samples.sort(key=lambda s: (s.decision_at, s.sample_id))
        dataset = (
            LearningDataset(
                self.profile.data_source,
                "reconciled_trades",
                self.settings.strategy_fingerprint(),
                self.profile.code_hash,
                sha256_json({"account": account_key, "mode": self.settings.mode.value}),
                now,
                tuple(samples),
            )
            if samples
            else None
        )
        self.database.audit(
            "learning.exported",
            "learning",
            {
                "account_scope_hash": sha256_json(account_key),
                "considered": len(trades),
                "labelled": len(samples),
                "skipped": dict(skipped),
                "dataset_sha256": dataset.digest if dataset else None,
                "source": self.profile.data_source.value,
                "not_stage_evidence": True,
            },
        )
        return ExportResult(dataset, len(trades), tuple(sorted(skipped.items())))

    def save_dataset(self, dataset: LearningDataset):
        if (
            dataset.source != self.profile.data_source
            or dataset.policy_hash != self.settings.strategy_fingerprint()
        ):
            raise TradingDisabled("dataset source/policy differs")
        return save_immutable(
            self.settings, canonical_json(dataset.to_dict()).encode(), category="candles", max_bytes=67108864
        )

    async def train(self, dataset: LearningDataset) -> TrainingResult:
        # CPU work off the runtime event loop. Cancelled jobs cannot select a model.
        return await asyncio.to_thread(
            ModelTrainer(self.settings, self.profile).train, dataset, as_of=self.clock.now()
        )
