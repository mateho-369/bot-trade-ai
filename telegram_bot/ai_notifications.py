"""Owner-only Telegram notifications for the AI-first layer (bounded, at-most-once, no secrets).

Producers (AIBrain, AdaptiveTrailing, AIConfigAdjuster) call the synchronous ``decision`` /
``circuit_opened`` / ``circuit_closed`` / ``config_adjustment`` / ``trailing`` hooks; messages are
queued in a bounded outbox and delivered by ``drain`` from the scheduler. A failed send is counted
as uncertain and NOT retried (same at-most-once policy as news alerts). Notifications never grant
permission to trade; owner overrides remain /pause, /kill, /close, /close_all, /approve, /reject.

Noise policy: only EXECUTABLE entry decisions, rule-mode switches, config adjustments, and AI
trailing actions/fallbacks are sent – not every "wait".
"""

from __future__ import annotations

from collections import deque

from core.security import sanitize_text

HEADER = "MT5 AI ReflexBot · AI"
MAX_OUTBOX = 50


def format_decision(result, symbol: str) -> str:
    decision = result.decision
    mode = {
        "rule_fallback": "TECHNICAL FALLBACK (AI unavailable)",
        "ai_blocked": "BLOCKED (AI unavailable, BLOCK_ON_AI_FAILURE)",
    }.get(result.source, f"AI ({result.source})")
    return (
        f"{HEADER} decision · {symbol}\n"
        f"{decision.action} · confidence {decision.confidence:.0f} · {mode}\n"
        f"Market: {decision.market_condition} · news risk {decision.news_risk}\n"
        f"Suggested risk {decision.suggested_risk_percent:.2f}% (never above your configured cap)\n"
        f"Reason: {decision.reason[:300]}\n"
        "Passed to risk engine; pause/kill/close stay owner-only."
    )


def format_circuit(
    *,
    opened: bool,
    reason: str = "",
    failures: int = 0,
    deep: bool = False,
    mode: str = "BLOCK_ON_AI_FAILURE",
) -> str:
    scope = "deep-review AI" if deep else "decision AI"
    if opened:
        if deep:
            effect = "Deep reviews pause; entries are unaffected."
        elif mode == "TECHNICAL_ONLY":
            effect = (
                "AI_FALLBACK=TECHNICAL_ONLY: rule mode, technical score fallback may trade; "
                "all safety gates still apply."
            )
        else:
            effect = (
                "AI_FALLBACK=BLOCK_ON_AI_FAILURE: rule mode, NEW entries are blocked until the AI "
                "answers. Mechanical trailing and protection continue. /ai_fallback_technical to change."
            )
        return f"{HEADER} · circuit OPEN ({scope})\n{failures} consecutive failures ({reason}). {effect}"
    return f"{HEADER} · circuit CLOSED ({scope})\nAI answers again; rule mode ended."


def format_adjustment(result) -> str:
    if result.status == "applied":
        head = "minor change AUTO-APPLIED (within hard bounds)"
    elif result.status == "pending":
        head = "MAJOR change needs your approval"
    else:
        head = f"change {result.status} ({result.classification})"
    text = (
        f"{HEADER} config · {head}\n{result.parameter} → {result.value}\nReason: {str(result.reason)[:300]}"
    )
    if result.status == "pending" and result.suggestion_id:
        text += f"\nApprove: /approve {result.suggestion_id}   Reject: /reject {result.suggestion_id}"
    return text


def format_trailing(event) -> str:
    if event.source == "mechanical":
        return (
            f"{HEADER} trailing · position {event.position_id}\n"
            f"Lock {event.threshold}% placed mechanically. {event.ai_reason[:200]}"
        )
    return (
        f"{HEADER} trailing · position {event.position_id}\n"
        f"Lock {event.threshold}% placed FIRST, then AI: {event.ai_decision} "
        f"({event.ai_confidence or 0:.0f}) → {event.final_action}\nReason: {event.ai_reason[:300]}"
    )


class AIOwnerNotifier:
    def __init__(self, settings, *, secrets=(), mode_provider=None):
        self.settings, self.secrets = settings, tuple(secrets)
        self.mode_provider = mode_provider
        self.outbox: deque[str] = deque(maxlen=MAX_OUTBOX)
        self.dropped = 0

    def _push(self, text: str) -> None:
        if self.settings.telegram_owner_id is None:
            return
        if len(self.outbox) == self.outbox.maxlen:
            self.dropped += 1
        self.outbox.append(sanitize_text(text, self.secrets)[:1000])

    # -- producer hooks -------------------------------------------------------------------------
    def decision(self, result, symbol: str) -> None:
        if result.kind == "entry" and result.executable and result.decision.opens:
            self._push(format_decision(result, symbol))

    def circuit_opened(self, *, reason: str, failures: int, deep: bool = False) -> None:
        try:
            mode = self.mode_provider() if self.mode_provider is not None else self.settings.ai_fallback_mode
        except Exception:
            mode = "BLOCK_ON_AI_FAILURE"
        self._push(format_circuit(opened=True, reason=reason, failures=failures, deep=deep, mode=mode))

    def summary(self, text: str) -> None:
        """Daily AI adjustment summary and other bounded owner digests."""
        self._push(f"{HEADER} · {text[:900]}")

    def circuit_closed(self, *, deep: bool = False) -> None:
        self._push(format_circuit(opened=False, deep=deep))

    def config_adjustment(self, result) -> None:
        if result.status in {"applied", "pending"}:
            self._push(format_adjustment(result))

    def trailing(self, event) -> None:
        if event.source == "ai" or event.final_action == "mechanical_continue":
            self._push(format_trailing(event))

    # -- delivery -------------------------------------------------------------------------------
    async def drain(self, bot, *, limit: int = 10) -> dict:
        result = {"delivered": 0, "uncertain": 0, "queued": len(self.outbox), "dropped": self.dropped}
        owner = self.settings.telegram_owner_id
        if owner is None or bot is None:
            return result
        for _ in range(min(limit, len(self.outbox))):
            text = self.outbox.popleft()
            try:
                await bot.send_message(chat_id=owner, text=text, parse_mode=None)
                result["delivered"] += 1
            except Exception:  # noqa: BLE001 - at-most-once: never resend a possibly delivered text.
                result["uncertain"] += 1
        result["queued"] = len(self.outbox)
        return result
