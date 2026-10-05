"""Bounded deterministic EN finance lexicon; sentiment never chooses side/risk or lowers impact."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

POSITIVE = {
    "gain",
    "gains",
    "rally",
    "rallies",
    "rallied",
    "surge",
    "surges",
    "growth",
    "strong",
    "beat",
    "beats",
    "recovery",
    "optimism",
    "approval",
    "approved",
    "bullish",
    "record",
}
NEGATIVE = {
    "fall",
    "falls",
    "drop",
    "drops",
    "crash",
    "plunge",
    "weak",
    "recession",
    "crisis",
    "hack",
    "hacked",
    "fear",
    "fraud",
    "loss",
    "losses",
    "bearish",
    "default",
    "defaults",
    "bankruptcy",
}
NEGATORS = {"not", "no", "never", "without", "deny", "denies", "denied"}
HIGH = (
    r"\b(?:fomc|nonfarm|non-farm|payrolls|cpi|consumer price index|rate decision|interest rate decision)\b",
    (
        r"\b(?:emergency|capital controls|bank run|bank failure|exchange hack|"
        r"withdrawals? suspended|suspends? withdrawals?|flash crash)\b"
    ),
    r"\b(?:war|invasion|missile|sanctions?|terrorist|earthquake|pandemic)\b",
)
MEDIUM = r"\b(?:inflation|unemployment|gdp|retail sales|central bank|earnings|regulator|regulation|etf)\b"


@dataclass(frozen=True, slots=True)
class SentimentResult:
    score: float
    impact: str
    language_supported: bool
    reasons: tuple[str, ...]


class SentimentAnalyzer:
    def analyze(self, title: str, summary: str = "", *, language="en"):
        if not isinstance(title, str) or not isinstance(summary, str) or len(title) + len(summary) > 2000:
            raise ValueError("bounded text required")
        text = (title + " " + summary).casefold()
        letters = [c for c in text if c.isalpha()]
        supported = language == "en" and (
            not letters or sum(c.isascii() for c in letters) / len(letters) >= 0.8
        )
        tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", text)[:350]
        scores = []
        for i, word in enumerate(tokens):
            weight = 1 if word in POSITIVE else -1 if word in NEGATIVE else 0
            if weight:
                if any(w in NEGATORS for w in tokens[max(0, i - 3) : i]):
                    weight = -weight
                scores.append(weight)
        score = math.tanh(sum(scores) / max(2, math.sqrt(len(scores)))) if supported else 0.0
        if not supported:
            return SentimentResult(0.0, "unknown", False, ("unsupported_language",))
        if any(re.search(pattern, text) for pattern in HIGH):
            return SentimentResult(score, "high", True, ("deterministic_headline_risk",))
        if re.search(MEDIUM, text):
            return SentimentResult(score, "medium", True, ("economic_or_regulatory_topic",))
        return SentimentResult(score, "low", True, ("no_recognized_high_impact_phrase",))
