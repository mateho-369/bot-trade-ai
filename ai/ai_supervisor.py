"""Read-only supervisor / EntryReviewer. All closes/settings remain pending owner proposals."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from ai.ai_router import FALLBACK_ELIGIBLE_OUTCOMES, AIRouter
from ai.feature_engineering import FeatureEngineering
from ai.model_registry import ModelRegistry
from ai.prompt_templates import entry_request, make_request
from ai.schemas import EntryReply, PositionReply, SettingsReply
from ai.suggestion_store import SuggestionStore
from ai.trade_analyzer import TradeAnalyzer
from core.database import Database
from core.models import Trade
from core.security import sanitize_text, sha256_json
from core.settings import Settings
from strategy.base_strategy import AIEntryReview, FeatureBundle, SignalResult
from strategy.news_filter import NewsFilter
from strategy.rule_fallback import rule_fallback_review
from strategy.signal_store import SignalStore
from strategy.volatility_filter import VolatilityFilter
from trading.risk_types import NewsWindow, PositionReview, RuntimeProfile, position_review_hash
from trading.types import BrokerError, Clock, Position, SourceKind, TradingDisabled, aware_utc


@dataclass(frozen=True, slots=True)
class NewsSnippet:
    title: str
    summary: str
    published_at: datetime
    source: str
    symbols: tuple[str, ...]

    def __post_init__(self):
        aware_utc(self.published_at)
        if (
            not isinstance(self.title, str)
            or not 1 <= len(self.title) <= 300
            or not isinstance(self.summary, str)
            or len(self.summary) > 1000
            or not isinstance(self.source, str)
            or not 1 <= len(self.source) <= 64
            or not isinstance(self.symbols, tuple)
            or not 1 <= len(self.symbols) <= 30
            or any(not isinstance(s, str) or not 1 <= len(s) <= 64 for s in self.symbols)
        ):
            raise BrokerError("bounded untrusted news snippet required")


class AISupervisor:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        profile: RuntimeProfile,
        *,
        router: AIRouter | None = None,
    ):
        self.database, self.settings, self.clock, self.profile = database, settings, clock, profile
        self.router = router or AIRouter(settings, database, clock)
        if (
            database.settings.safety_fingerprint() != settings.safety_fingerprint()
            or self.router.settings.safety_fingerprint() != settings.safety_fingerprint()
        ):
            raise TradingDisabled("AI/router/database policy differs")
        self.store = SignalStore(database, settings, clock, profile)
        self.suggestions = SuggestionStore(database, settings, clock, profile)
        self.registry = ModelRegistry(database, settings, clock, profile)
        self.trades = TradeAnalyzer(database, settings, clock)
        self.news = NewsFilter(settings, clock, database, profile)
        self.volatility = VolatilityFilter(settings, clock)
        self._initialized = False

    async def initialize(self):
        await asyncio.to_thread(self.database.verify_schema)
        actual = await asyncio.to_thread(
            RuntimeProfile.current,
            self.settings,
            self.profile.data_source,
            model_sha256=self.profile.model_sha256,
        )
        if actual.code_hash != self.profile.code_hash:
            raise TradingDisabled("AI runtime code fingerprint differs")
        self._initialized = True

    def _ready(self):
        if not self._initialized:
            raise TradingDisabled("explicitly initialize read-only AI supervision first")

    async def _veto(self, purpose, reason, **details):
        await asyncio.to_thread(
            self.database.audit, "ai.supervisor_veto", "ai", {"purpose": purpose, "reason": reason, **details}
        )

    async def review(self, proposal: SignalResult, news: NewsWindow) -> AIEntryReview | None:
        self._ready()
        try:
            stored = await asyncio.to_thread(self.store.get, proposal.signal_id)
            if stored.state != "pending" or stored.payload_json != proposal.payload_json:
                raise TradingDisabled("original pending persisted proposal required")
            if not self.news.evaluate(stored.payload()["logical_symbol"], news).allowed:
                await self._veto("entry", "unknown_stale_or_unsafe_news", signal_id=stored.signal_id)
                return None
            probability = None
            if self.settings.model_filter_enabled:
                vector = FeatureEngineering.from_snapshot(
                    stored.payload()["feature_snapshot"], stored.payload()["technical"]
                )
                probability = await asyncio.to_thread(self.registry.inference, vector)
                if probability < self.settings.model_min_probability:
                    await self._veto("entry", "learning_filter_veto", signal_id=stored.signal_id)
                    return None
            request = entry_request(
                stored,
                news,
                settings=self.settings,
                profile=self.profile,
                clock=self.clock,
                model_probability=probability,
            )
            routed, outcome = await self._route_entry(request)
            if routed is None:
                if outcome not in FALLBACK_ELIGIBLE_OUTCOMES:
                    return None  # Disabled/expired: deliberate veto, never a rule-based approval.
                # News was verified above and any enabled learning filter already passed.
                fallback = rule_fallback_review(
                    stored, news, settings=self.settings, profile=self.profile, now=self.clock.now()
                )
                await asyncio.to_thread(
                    self.database.audit,
                    "ai.rule_fallback_review" if fallback is not None else "ai.rule_fallback_not_permitted",
                    "ai",
                    {
                        "signal_id": stored.signal_id,
                        "ai_outcome": outcome,
                        "decision": fallback.decision if fallback is not None else None,
                        "technical_score": stored.score,
                    },
                )
                return fallback
            if routed.simulated and self.profile.data_source != SourceKind.SYNTHETIC:
                await self._veto("entry", "simulated_provider_on_native_source")
                return None
            reply = routed.reply
            if not isinstance(reply, EntryReply):
                raise TradingDisabled("wrong entry schema")
            risk = reply.risk_percent
            if risk is not None and risk > self.settings.effective_risk_percent:
                await self._veto("entry", "provider_risk_escalation", signal_id=stored.signal_id)
                return None
            if (
                risk is not None
                and risk < self.settings.effective_risk_percent
                and not self.settings.auto_reduce_risk
            ):
                if reply.confidence >= self.settings.ai_confidence_threshold:
                    await asyncio.to_thread(
                        self.suggestions.create,
                        "reduce_risk",
                        {"risk_percent": str(risk)},
                        reason=reply.rationale,
                        request_hash=request.request_hash,
                    )
                await self._veto("entry", "risk_reduction_needs_owner_enable", signal_id=stored.signal_id)
                return None  # Never ignore the warning then approve at the larger current cap.
            return AIEntryReview(
                routed.observed_at,
                self.profile.data_source,
                stored.proposal_hash,
                self.profile.code_hash,
                self.profile.model_sha256,
                news.evidence_hash,
                reply.decision,
                reply.confidence,
                "test" if routed.simulated else routed.provider,
                risk if risk is not None and risk < self.settings.effective_risk_percent else None,
                request_hash=request.request_hash,
                provider_model=routed.provider_model,
            )
        except asyncio.CancelledError:
            raise
        except (BrokerError, ValueError, TypeError, KeyError, ArithmeticError):
            await self._veto(
                "entry", "unbound_or_unavailable_context", signal_id=getattr(proposal, "signal_id", None)
            )
            return None

    async def _route_entry(self, request):
        complete = getattr(self.router, "complete_with_outcome", None)
        if complete is None:  # Custom routers without outcome reporting never enable the fallback.
            return await self.router.complete(request), "unknown"
        return await complete(request)

    def _bundle(self, features: FeatureBundle):
        if (
            not isinstance(features, FeatureBundle)
            or features.source != self.profile.data_source
            or features.logical_symbol not in self.settings.symbols
            or features.symbol
            != self.settings.symbol_aliases.get(features.logical_symbol, features.logical_symbol)
            or not -2
            <= (self.clock.now() - features.observed_at).total_seconds()
            <= self.settings.order_max_age_seconds
        ):
            raise TradingDisabled("fresh enabled source-bound finalized features required")
        features.tick.fresh(self.clock, self.settings.max_tick_age_seconds)

    async def analyze_market(self, features: FeatureBundle):
        self._ready()
        self._bundle(features)
        request = make_request(
            "market",
            {"features": features.to_dict()},
            settings=self.settings,
            profile=self.profile,
            as_of=features.observed_at,
            expires_at=features.observed_at + timedelta(seconds=self.settings.order_max_age_seconds),
        )
        routed = await self.router.complete(request)
        return routed.reply if routed is not None else None  # advisory ONLY.

    async def analyze_news(self, items: tuple[NewsSnippet, ...]):
        self._ready()
        if (
            not isinstance(items, tuple)
            or not 1 <= len(items) <= 8
            or any(not isinstance(s, NewsSnippet) for s in items)
        ):
            raise TradingDisabled("one to eight typed headlines required")
        now = self.clock.now()
        rows = []
        for item in items:
            if item.published_at > now or any(s not in self.settings.symbols for s in item.symbols):
                raise TradingDisabled("future news/unknown symbol context")
            rows.append(
                {
                    "title": sanitize_text(item.title, self.database.secrets),
                    "summary": sanitize_text(item.summary, self.database.secrets),
                    "published_at": item.published_at.isoformat(),
                    "source": item.source,
                    "symbols": list(item.symbols),
                }
            )
        request = make_request(
            "news",
            {"headlines": rows, "cannot_certify_calendar_coverage": True},
            settings=self.settings,
            profile=self.profile,
            as_of=now,
            expires_at=now + timedelta(seconds=self.settings.order_max_age_seconds),
        )
        routed = await self.router.complete(request)
        return routed.reply if routed is not None else None

    def _owned_position(self, account_key, position):
        with self.database.session() as session:
            row = session.scalar(
                select(Trade).where(
                    Trade.account_key == account_key,
                    Trade.mode == self.settings.mode.value,
                    Trade.position_identifier == position.identifier,
                    Trade.status == "open",
                )
            )
            if row is None:
                raise TradingDisabled("positive reconciled owned position required")
            meta = row.features_json.get("execution", {})
            if (
                row.ticket != position.ticket
                or row.volume != position.volume
                or row.symbol != position.symbol
                or row.direction != position.side.value
                or position.magic != self.settings.mt5_magic_number
                or meta.get("code_hash") != self.profile.code_hash
                or meta.get("model_sha256") != self.profile.model_sha256
                or meta.get("data_source") != self.profile.data_source.value
                or not meta.get("entry_deal_tickets")
            ):
                raise TradingDisabled("position identity/volume/source differs from known ownership")
            return row.id

    async def review_position(
        self, position: Position, features: FeatureBundle, news: NewsWindow, *, account_key: str
    ):
        self._ready()
        self._bundle(features)
        if (
            position.symbol != features.symbol
            or not self.news.evaluate(features.logical_symbol, news).allowed
        ):
            await self._veto("position", "unsafe_news_or_symbol")
            return None
        trade_id = await asyncio.to_thread(self._owned_position, account_key, position)
        digest = position_review_hash(position)
        data = {
            "trade_id": trade_id,
            "position": {
                "ticket": position.ticket,
                "identifier": position.identifier,
                "direction": position.side.value,
                "volume": str(position.volume),
                "entry_price": str(position.entry_price),
                "sl": str(position.sl),
                "tp": str(position.tp),
            },
            "features": features.to_dict(),
            "news_coverage": asdict(news),
            "broker_write_authority": False,
        }
        request = make_request(
            "position",
            data,
            settings=self.settings,
            profile=self.profile,
            as_of=features.observed_at,
            expires_at=features.observed_at + timedelta(seconds=self.settings.order_max_age_seconds),
            binding={"position_hash": digest, "news_hash": news.evidence_hash},
        )
        routed = await self.router.complete(request)
        if routed is None:
            return None
        if routed.simulated and self.profile.data_source != SourceKind.SYNTHETIC:
            await self._veto("position", "simulated_provider_on_native_source")
            return None
        reply = routed.reply
        if not isinstance(reply, PositionReply):
            return None
        if (
            reply.decision in {"close", "reduce"}
            and reply.confidence >= self.settings.ai_confidence_threshold
        ):
            remaining = int((request.expires_at - self.clock.now()).total_seconds())
            if remaining > 0:
                await asyncio.to_thread(
                    self.suggestions.create,
                    "close_position",
                    {
                        "trade_id": trade_id,
                        "position_identifier": position.identifier,
                        "position_hash": digest,
                        "fraction": str(reply.close_fraction),
                    },
                    reason=reply.rationale,
                    request_hash=request.request_hash,
                    ttl_seconds=min(remaining, self.settings.ai_suggestion_ttl_seconds),
                )
        hold = reply.decision == "hold" and self.volatility.evaluate(features).allowed
        return PositionReview(
            routed.observed_at,
            self.profile.data_source,
            reply.confidence,
            hold and reply.momentum_continues,
            hold and reply.volatility_safe,
            news,
            position.identifier,
            digest,
            self.profile.code_hash,
            self.profile.model_sha256,
        )

    async def suggest_settings(self, *, account_key: str):
        self._ready()
        summary = await asyncio.to_thread(self.trades.summary, account_key)
        # No inference that a few wins imply adaptation permission.
        if summary["closed"] < 30:
            await self._veto("settings", "insufficient_reconciled_history")
            return None
        now = self.clock.now()
        request = make_request(
            "settings",
            {"summary": summary, "proposal_only": True},
            settings=self.settings,
            profile=self.profile,
            as_of=now,
            expires_at=now + timedelta(seconds=self.settings.order_max_age_seconds),
        )
        routed = await self.router.complete(request)
        if routed is None or not isinstance(routed.reply, SettingsReply):
            return None
        reply = routed.reply
        if reply.action == "no_change" or reply.confidence < self.settings.ai_confidence_threshold:
            return None
        kind = "reduce_risk" if reply.action == "reduce_risk" else "rebalance_weights"
        parameters = (
            {"risk_percent": str(reply.risk_percent)}
            if kind == "reduce_risk"
            else {"weights": {k: str(v) for k, v in reply.weights.items()}}
        )
        try:
            return await asyncio.to_thread(
                self.suggestions.create,
                kind,
                parameters,
                reason=reply.rationale,
                request_hash=request.request_hash,
                data_evidence_hash=sha256_json(summary),
            )
        except TradingDisabled:
            await self._veto("settings", "unsafe_or_excessive_settings_proposal")
            return None

    async def daily_report(self, account_key: str, *, start: datetime | None = None):
        self._ready()
        report = await asyncio.to_thread(self.trades.summary, account_key, start=start)
        await asyncio.to_thread(
            self.database.audit,
            "ai.daily_report_generated",
            "ai",
            {
                "account_scope_hash": report["account_scope_hash"],
                "closed": report["closed"],
                "not_stage_evidence": True,
            },
        )
        return report

    async def learning_cycle(self, *, account_key: str):
        """Explicit offline job: export → chronological train → INACTIVE candidate.

        AUTO_ADAPT_STRATEGY_WEIGHTS enables only a bounded stored proposal, never
        an unapproved active policy change. Part 10 schedules this away from entries.
        """
        from ai.learning_engine import LearningEngine
        from ai.strategy_optimizer import StrategyOptimizer

        self._ready()
        learning = LearningEngine(self.database, self.settings, self.clock, self.profile)
        exported = await asyncio.to_thread(learning.export, account_key)
        if (
            exported.dataset is None
            or len(exported.dataset.samples) < self.settings.model_min_labelled_trades
        ):
            await self._veto("learning", "insufficient_bound_labels")
            return {"state": "skipped", "reason": "insufficient_bound_labels", "model_id": None}
        dataset = exported.dataset
        await asyncio.to_thread(learning.save_dataset, dataset)
        trained = await learning.train(dataset)
        candidate = await asyncio.to_thread(self.registry.register, trained)
        proposal = None
        if self.settings.auto_adapt_strategy_weights and trained.payload()["evaluation"]["passed"]:
            optimizer = StrategyOptimizer(self.database, self.settings, self.suggestions)
            try:
                proposal = await asyncio.to_thread(
                    optimizer.propose, dataset, trained.payload()["evaluation"], account_key=account_key
                )
            except TradingDisabled:
                await self._veto("learning", "changed_or_insufficient_weight_association_evidence")
        return {
            "state": "candidate",
            "model_id": candidate.model_id,
            "model_sha256": candidate.digest,
            "active": candidate.active,
            "owner_selection_required": True,
            "weight_suggestion_id": proposal.suggestion_id if proposal else None,
            "not_stage_evidence": True,
        }

    async def close(self):
        await self.router.close()
        self._initialized = False
