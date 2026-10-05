"""Deterministic weighted consensus. AI cannot choose a side or increase weights."""

from __future__ import annotations

from decimal import Decimal

from core.settings import Settings
from strategy.base_strategy import (
    STRATEGY_NAMES,
    BaseStrategy,
    FeatureBundle,
    StrategyVote,
    TechnicalDecision,
)
from strategy.breakout_strategy import BreakoutStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.trend_strategy import TrendStrategy
from trading.types import BrokerError, Side


class StrategyRouter:
    def __init__(self, settings: Settings, *, strategies: tuple[BaseStrategy, ...] | None = None):
        self.settings = settings
        self.strategies = (
            strategies
            if strategies is not None
            else (
                TrendStrategy(),
                MeanReversionStrategy(),
                BreakoutStrategy(),
                MomentumStrategy(),
            )
        )
        if len(self.strategies) != 4 or {strategy.name for strategy in self.strategies} != set(
            STRATEGY_NAMES
        ):
            raise BrokerError("router must contain exactly the four reviewed strategy identities")

    def evaluate(self, features: FeatureBundle) -> TechnicalDecision:
        votes = []
        for strategy in self.strategies:
            if self.settings.strategy_weights[strategy.name] == 0:
                votes.append(StrategyVote.wait(strategy.name, "owner_disabled_strategy"))
                continue
            vote = strategy.evaluate(features)
            if not isinstance(vote, StrategyVote) or vote.strategy != strategy.name:
                raise BrokerError("strategy returned an unbound vote")
            votes.append(vote)
        return self.aggregate(tuple(votes))

    def aggregate(self, votes: tuple[StrategyVote, ...]) -> TechnicalDecision:
        cfg = self.settings
        if (
            not isinstance(votes, tuple)
            or len(votes) != 4
            or any(not isinstance(vote, StrategyVote) for vote in votes)
            or {vote.strategy for vote in votes} != set(STRATEGY_NAMES)
        ):
            raise BrokerError("missing/duplicate/unknown router votes")
        eligible = [
            vote
            for vote in votes
            if vote.side is not None
            and vote.score >= cfg.strategy_min_vote_score
            and cfg.strategy_weights[vote.strategy] > 0
        ]
        masses = {
            side: sum(
                (
                    cfg.strategy_weights[vote.strategy] * Decimal(str(vote.score))
                    for vote in eligible
                    if vote.side == side
                ),
                Decimal("0"),
            )
            for side in Side
        }
        if masses[Side.BUY] == masses[Side.SELL]:
            return TechnicalDecision(
                None, 0, votes, Decimal("0"), Decimal("0"), ("no_directional_consensus",)
            )
        side = max(masses, key=masses.get)
        supporting = [vote for vote in eligible if vote.side == side]
        coverage = sum((cfg.strategy_weights[vote.strategy] for vote in supporting), Decimal("0"))
        agreement = masses[side] / sum(masses.values())
        reasons = []
        if len(supporting) < cfg.strategy_min_agreeing:
            reasons.append("insufficient_independent_strategy_votes")
        if coverage < cfg.strategy_min_coverage:
            reasons.append("insufficient_strategy_coverage")
        if agreement < cfg.strategy_min_agreement:
            reasons.append("conflicting_strategy_votes")
        score = masses[side] / coverage * agreement
        if score < Decimal(str(cfg.min_signal_score)):
            reasons.append("low_weighted_technical_score")
        if reasons:
            return TechnicalDecision(None, 0, votes, coverage, agreement, tuple(reasons))
        return TechnicalDecision(
            side, float(round(score, 6)), votes, coverage, agreement, ("weighted_consensus",)
        )
