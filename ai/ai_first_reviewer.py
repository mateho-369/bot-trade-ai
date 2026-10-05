"""AI-FIRST entry reviewer: plugs the brain into the existing EntryReviewer slot of SignalEngine.

Order of authority for a NEW entry (each step can only veto, never force a trade):

1. Strategy router proposal (technical candidate, structural stop) – unchanged.
2. **AI brain** decision over the full market-awareness snapshot (this module).
   * AI answered: approve only if action == proposal side, confidence >= threshold, news clear,
     spread/daily-trades/open-position/overlay limits pass. A valid AI wait/reject is FINAL.
   * AI unavailable (timeout, 429/5xx, invalid JSON, open circuit) – owner AI_FALLBACK_MODE:
     BLOCK_ON_AI_FAILURE (default) => no new entry (journal ``ai_unavailable_block_mode``);
     TECHNICAL_ONLY => the canonical deterministic ``rule_fallback_review`` (technical score).
3. Signal finalization re-verifies binding/news/confidence/risk; the risk engine re-checks
   everything again before any send (paper/mock by default; DenyAllWrites on native adapters).

Per-trade the AI may only REDUCE risk below the CURRENT limit (owner setting, or the AI-dynamic
layer-1 value from ``trading.ai_controls.effective_limits`` when the AI is available). Reductions
are applied when AUTO_REDUCE_RISK=true; otherwise the trade keeps the current (never higher) risk
and the journal records that the suggestion was not applied.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from ai.ai_brain import AIBrain, decimal_risk
from ai.market_awareness import MarketAwarenessEngine, NewsContext
from core.settings import Settings
from strategy.base_strategy import AIEntryReview, SignalResult
from strategy.rule_fallback import rule_fallback_review
from trading.risk_types import NewsWindow, RuntimeProfile
from trading.types import BrokerError, Clock, SourceKind


class AIFirstReviewer:
    def __init__(
        self,
        brain: AIBrain,
        awareness: MarketAwarenessEngine,
        settings: Settings,
        profile: RuntimeProfile,
        clock: Clock,
        *,
        inner=None,
        trades_today=None,
        news_context=None,
        account_key: str | None = None,
    ):
        self.brain, self.awareness, self.settings = brain, awareness, settings
        self.profile, self.clock, self.inner = profile, clock, inner
        self.trades_today = trades_today or (lambda: 0)
        self.news_context = news_context
        self.account_key = account_key
        self.fallback_mode = getattr(brain, "_fallback_mode", lambda: settings.ai_fallback_mode)

    async def _news(self, logical: str, window: NewsWindow) -> NewsContext:
        if self.news_context is None:
            return NewsContext.from_window(window)
        result = self.news_context(logical, window)
        return await result if asyncio.iscoroutine(result) else result

    async def review(self, proposal: SignalResult, news: NewsWindow) -> AIEntryReview | None:
        if self.settings.ai_provider == "disabled":
            return None  # Deliberate owner choice: never a rule-based approval.
        if self.brain.provider is None and self.inner is not None:
            return await self._delegate(proposal, news)
        try:
            payload = json.loads(proposal.payload_json or "{}")
        except ValueError:
            payload = {}
        logical = payload.get("logical_symbol") or proposal.symbol
        try:
            snapshot = await self.awareness.snapshot(
                logical,
                native=proposal.symbol,
                technical_side=proposal.side,
                technical_score=proposal.score,
                stop_price=proposal.stop_price,
                news=await self._news(logical, news),
                account_key=self.account_key,
            )
        except (BrokerError, ValueError, KeyError, TypeError, ArithmeticError):
            return None  # No market awareness => no new entry (fail closed).
        trades = self.trades_today()
        trades = await trades if asyncio.iscoroutine(trades) else trades
        result = await self.brain.decide(
            snapshot, kind="entry", trades_today=int(trades), signal_id=proposal.signal_id
        )
        now = self.clock.now()
        if result.source == "ai_blocked":
            self._mark(result, None, note="ai_unavailable_block_mode")
            return None  # BLOCK_ON_AI_FAILURE: an outage never opens a new entry.
        if result.source == "rule_fallback":
            review = rule_fallback_review(
                proposal,
                news,
                settings=self.settings,
                profile=self.profile,
                now=now,
                fallback_mode=self.fallback_mode(),
            )
            if review is not None and review.decision == "approve" and not result.executable:
                review = replace(review, decision="wait")  # Hard-limit gate still applies in rule mode.
            self._mark(result, review)
            return review
        if self.settings.model_filter_enabled:
            # The learning filter lives in the supervisor; AI-first adds its veto in front of it.
            if not result.executable or self.inner is None:
                self._mark(result, None)
                return None
            review = await self.inner.review(proposal, news)
            self._mark(result, review)
            return review
        decision = result.decision
        verdict = "approve" if result.executable else ("wait" if decision.action == "wait" else "reject")
        risk = None
        if result.executable:
            target = decimal_risk(decision)
            ceiling = self.settings.effective_risk_percent
            limits = self.brain.limits() if self.brain.limits is not None else None
            if limits is not None:
                ceiling = min(ceiling, limits.risk_percent)
            # Only a reduction BELOW both the owner setting and the current dynamic value is a
            # per-trade review risk; the dynamic value itself is applied by execution/risk engine.
            if target < ceiling and self.settings.auto_reduce_risk:
                risk = target
        simulated = bool(result.extras.get("simulated"))
        if simulated and self.profile.data_source != SourceKind.SYNTHETIC:
            self._mark(result, None, note="simulated_provider_on_native_source")
            return None
        review = AIEntryReview(
            now,
            self.profile.data_source,
            proposal.proposal_hash,
            self.profile.code_hash,
            self.profile.model_sha256,
            news.evidence_hash,
            verdict,
            float(decision.confidence),
            "test" if simulated else "openai",
            risk,
            request_hash=snapshot.digest,
            provider_model=result.model,
        )
        self._mark(result, review)
        return review

    async def _delegate(self, proposal: SignalResult, news: NewsWindow) -> AIEntryReview | None:
        """Non-OpenAI-compatible providers (e.g. local Ollama) keep the reviewed supervisor path."""
        review = await self.inner.review(proposal, news)
        journal = self.brain.journal
        if journal is not None:
            approved = review is not None and review.decision == "approve"
            journal.record(
                kind="entry",
                source="rule_fallback" if review is not None and review.provider == "rule_fallback" else "ai",
                action=(
                    "open_" + proposal.side.value.lower()
                    if approved and proposal.side is not None
                    else (review.decision if review is not None else "veto")
                ),
                reason="supervisor review (" + (review.provider if review is not None else "none") + ")",
                confidence=None if review is None else float(review.confidence),
                symbol=proposal.symbol,
                signal_id=proposal.signal_id,
                model=None if review is None else review.provider_model,
                input_summary={
                    "technical_score": proposal.score,
                    "side": getattr(proposal.side, "value", None),
                },
                final_action="approved_to_risk_engine" if approved else "vetoed",
            )
        return review

    def _mark(self, result, review, *, note: str | None = None):
        journal = self.brain.journal
        if journal is None or result.journal_id is None:
            return
        approved = review is not None and review.decision == "approve"
        reason = note or (None if approved else ",".join(result.reasons)[:128] or "not_approved")
        journal.mark(
            result.journal_id,
            executed=False,  # Execution is linked later by DecisionJournal.link_signal.
            final_action="approved_to_risk_engine" if approved else "vetoed",
            rejection_reason=reason,
        )
