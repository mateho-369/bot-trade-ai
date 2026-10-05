"""Deterministic rule-based entry review used ONLY when the AI provider cannot answer.

Eligible: provider timeout, HTTP 429/5xx/unavailable, open circuit, missing provider configuration,
invalid JSON/envelope/binding or an unexpected transport failure. NOT eligible: a VALID AI reply (a
reject/WAIT/low confidence is final and is never "shopped" past), AI_PROVIDER=disabled, an expired
request, unsafe/unknown news, a learning-filter veto, BACKTEST/historical replay, or LIVE unless the
owner explicitly sets AI_RULE_FALLBACK_ALLOW_LIVE=true.

The review's confidence is EXACTLY the persisted technical signal score, so finalization and the
pre-send risk recheck can verify it was not fabricated. It approves only at or above
AI_RULE_FALLBACK_MIN_SCORE (validated >= AI_CONFIDENCE_THRESHOLD and MIN_SIGNAL_SCORE). It never
changes risk size, SL, owner pause/kill, stage gates or broker write authority.
"""

from __future__ import annotations

from datetime import datetime

from core.settings import OperatingMode, Settings
from strategy.base_strategy import AIEntryReview, SignalResult
from trading.risk_types import NewsWindow, RuntimeProfile
from trading.types import SourceKind

RULE_FALLBACK_PROVIDER = "rule_fallback"
RULE_FALLBACK_MODEL = "technical-score-v1"


def rule_fallback_permitted(settings: Settings, profile: RuntimeProfile) -> bool:
    """Policy scope only; every ordinary news/risk/owner/stage gate still applies afterwards."""
    return bool(
        settings.ai_rule_fallback_enabled
        and settings.mode != OperatingMode.BACKTEST
        and profile.data_source != SourceKind.HISTORICAL
        and (settings.mode != OperatingMode.LIVE or settings.ai_rule_fallback_allow_live)
    )


def rule_fallback_problems(
    review: AIEntryReview, *, signal_score: float, settings: Settings, profile: RuntimeProfile
) -> list[str]:
    """Finalization/pre-send verification of a stored fallback review (empty list = acceptable)."""
    problems = []
    if not rule_fallback_permitted(settings, profile):
        problems.append("rule_fallback_disabled_or_out_of_scope")
    if review.provider_model != RULE_FALLBACK_MODEL or review.request_hash is not None:
        problems.append("rule_fallback_unbound")
    if review.risk_percent is not None:
        problems.append("rule_fallback_risk_change")
    if float(review.confidence) != float(signal_score):
        problems.append("rule_fallback_score_mismatch")
    if float(signal_score) < settings.ai_rule_fallback_min_score:
        problems.append("rule_fallback_score_below_minimum")
    return problems


def rule_fallback_review(
    proposal: SignalResult,
    news: NewsWindow,
    *,
    settings: Settings,
    profile: RuntimeProfile,
    now: datetime,
) -> AIEntryReview | None:
    """Build the bound deterministic review, or None when the fallback is out of policy scope."""
    if (
        not rule_fallback_permitted(settings, profile)
        or proposal.state != "pending"
        or proposal.proposal_hash is None
        or proposal.source != profile.data_source
    ):
        return None
    score = float(proposal.score)
    approve = proposal.side is not None and score >= settings.ai_rule_fallback_min_score
    return AIEntryReview(
        now,
        profile.data_source,
        proposal.proposal_hash,
        profile.code_hash,
        profile.model_sha256,
        news.evidence_hash,
        "approve" if approve else "wait",
        score,
        RULE_FALLBACK_PROVIDER,
        None,
        request_hash=None,
        provider_model=RULE_FALLBACK_MODEL,
    )
