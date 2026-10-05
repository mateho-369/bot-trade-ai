"""Multi-AI registry: AI_PROVIDERS entries -> labelled clients -> priority failover composite.

* Identity is the entry **label** (``groq``, ``groq2``, ``local``...). Keys are referenced only by
  environment-variable NAME (``api_key_env``) and resolved by ``Settings.ai_provider_key``.
* ``FailoverProvider`` asks members in priority order. It moves to the next member ONLY when the
  current one is unavailable: timeout, HTTP 429, HTTP 5xx, missing key or an open per-label circuit.
  An invalid reply or an auth error is raised (no trade) and a VALID reply - including wait/reject -
  is returned as-is: an answer is never "shopped" to another AI.
* Each label has its own consecutive-failure circuit; transitions and failures are reported to an
  optional ``listener(event, label, detail)`` (audit ``ai.provider_failure`` + owner alert). Details
  are reason codes only - never URLs, keys or provider bodies.
* With a deadline published by the brain (``DEADLINE``), the primary gets at most half of the
  remaining budget when another healthy member exists, so failover still fits the decision timeout.

Adding a provider needs only a ``.env``/config edit (``AI_PROVIDERS`` JSON + the key variable).
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass

import httpx

from ai.json_validation import AIUnavailable

DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("ai_deadline", default=None)
# AIUnavailable reasons that mean "this provider cannot answer right now" (failover-eligible).
FAILOVER_REASONS = frozenset(
    {
        "network_or_timeout",
        "http_429",
        "http_5xx",
        "http_timeout",
        "missing_api_key",
        "provider_not_configured",
        "circuit_open",
    }
)


def failover_eligible(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.TransportError, ConnectionError)):
        return True
    return isinstance(exc, AIUnavailable) and getattr(exc, "code", "") in FAILOVER_REASONS


def reason_code(exc: BaseException) -> str:
    if isinstance(exc, AIUnavailable):
        return str(getattr(exc, "code", "provider_unavailable"))[:48]
    if isinstance(exc, TimeoutError):
        return "timeout"
    return type(exc).__name__[:48]


class LabelCircuit:
    """Per-label consecutive-failure breaker with one half-open trial after the cooldown."""

    def __init__(self, failures: int, cooldown: float, *, monotonic=time.monotonic):
        self.threshold, self.cooldown, self.monotonic = failures, cooldown, monotonic
        self.consecutive, self.opened_at, self.trial = 0, None, False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.monotonic() - self.opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self.trial:
            self.trial = True
            return True
        return False

    def success(self) -> bool:
        was_open = self.opened_at is not None
        self.consecutive, self.opened_at, self.trial = 0, None, False
        return was_open

    def failure(self) -> bool:
        self.consecutive += 1
        if self.trial:
            self.trial, self.opened_at = False, self.monotonic()
            return False
        if self.opened_at is None and self.consecutive >= self.threshold:
            self.opened_at = self.monotonic()
            return True
        return False


@dataclass(slots=True)
class Member:
    label: str
    model: str
    priority: int
    client: object
    circuit: LabelCircuit

    @property
    def configured(self) -> bool:
        return bool(getattr(self.client, "configured", True))


class FailoverProvider:
    """Provider-protocol composite (``configured``/``complete``/``close``) over labelled members."""

    name = "registry"

    def __init__(self, members: list[Member], *, listener=None, monotonic=time.monotonic):
        self.members = sorted(members, key=lambda m: (m.priority, m.label))
        self.listener = listener
        self.monotonic = monotonic
        self.failures: dict[str, int] = {m.label: 0 for m in self.members}

    @property
    def configured(self) -> bool:
        return any(m.configured for m in self.members)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(m.label for m in self.members)

    def _emit(self, event: str, label: str, detail: str) -> None:
        if self.listener is None:
            return
        try:
            self.listener(event, label, detail)
        except Exception:
            pass  # Telemetry never changes a trading decision.

    def _failed(self, member: Member, exc: BaseException) -> None:
        self.failures[member.label] = self.failures.get(member.label, 0) + 1
        code = reason_code(exc)
        opened = member.circuit.failure()
        self._emit("failure", member.label, code)
        if opened:
            self._emit("circuit_open", member.label, code)

    def _succeeded(self, member: Member) -> None:
        if member.circuit.success():
            self._emit("circuit_closed", member.label, "answering")

    async def ask_member(self, member: Member, messages, schema, *, budget: float | None = None):
        """One member, no failover (used by all_must_approve and by ``complete``)."""
        if not member.configured:
            raise AIUnavailable("missing_api_key")
        if not member.circuit.allow():
            raise AIUnavailable("circuit_open")
        try:
            if budget is not None:
                async with asyncio.timeout(max(0.05, budget)):
                    content = await member.client.complete(messages, schema)
            else:
                content = await member.client.complete(messages, schema)
        except asyncio.CancelledError:
            member.circuit.trial = False
            raise
        except Exception as exc:
            self._failed(member, exc)
            raise
        self._succeeded(member)
        return content

    async def complete(self, messages, schema):
        candidates = [m for m in self.members if m.configured]
        if not candidates:
            raise AIUnavailable("provider_not_configured")
        last: BaseException = AIUnavailable("circuit_open")
        for index, member in enumerate(candidates):
            healthy_later = any(m.circuit.state != "open" for m in candidates[index + 1 :])
            deadline = DEADLINE.get()
            budget = None
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                budget = remaining / 2 if healthy_later else remaining
            try:
                return await self.ask_member(member, messages, schema, budget=budget)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not failover_eligible(exc):
                    raise  # Invalid reply / auth error: fail closed, never ask another AI.
                last = exc
        if isinstance(last, AIUnavailable):
            raise AIUnavailable(
                "circuit_open" if getattr(last, "code", "") == "circuit_open" else "all_providers_unavailable"
            )
        raise AIUnavailable("all_providers_unavailable")

    def status(self) -> list[dict]:
        return [
            {
                "label": m.label,
                "model": m.model,
                "priority": m.priority,
                "configured": m.configured,
                "circuit": m.circuit.state,
                "failures": self.failures.get(m.label, 0),
            }
            for m in self.members
        ]

    async def close(self):
        for member in self.members:
            closer = getattr(member.client, "close", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass


def build_client(settings, entry, *, transport=None, model: str | None = None):
    if entry.kind == "ollama":
        from ai.ollama_client import OllamaClient

        return OllamaClient(
            settings,
            transport=transport,
            base_url=entry.base_url,
            model=model or entry.model,
            label=entry.label,
        )
    from ai.openai_client import OpenAIClient

    return OpenAIClient(
        settings,
        transport=transport,
        model=model or entry.model,
        base_url=entry.base_url,
        api_key=settings.ai_provider_key(entry),
        label=entry.label,
    )


def build_role(settings, role: str, *, transports=None, listener=None, monotonic=time.monotonic):
    """FailoverProvider for one role, or None when no enabled entry exists for it.

    Roles: decision (entries + Telegram approvals), trailing (falls back to decision), deep (falls
    back to the decision entries with AI_DEEP_MODEL) and review (falls back to decision).
    ``transports`` maps label -> httpx transport (tests/offline smokes only).
    """
    registry = [e for e in settings.ai_registry() if e.enabled]
    chosen = [e for e in registry if e.role == role]
    deep_model = None
    if not chosen and role in {"trailing", "review"}:
        chosen = [e for e in registry if e.role == "decision"]
    if not chosen and role == "deep":
        chosen = [e for e in registry if e.role == "decision" and e.kind == "openai_compatible"]
        deep_model = settings.ai_deep_model
    if not chosen:
        return None
    members = [
        Member(
            e.label,
            deep_model or e.model,
            e.priority,
            build_client(settings, e, transport=(transports or {}).get(e.label), model=deep_model),
            LabelCircuit(
                settings.ai_circuit_failures, settings.ai_circuit_cooldown_seconds, monotonic=monotonic
            ),
        )
        for e in chosen
    ]
    return FailoverProvider(members, listener=listener, monotonic=monotonic)


__all__ = [
    "DEADLINE",
    "FAILOVER_REASONS",
    "FailoverProvider",
    "LabelCircuit",
    "Member",
    "build_client",
    "build_role",
    "failover_eligible",
]
