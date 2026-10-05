"""Bounded provider routing. Fallback only for availability, never shopping past a veto."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ai.json_validation import AIInvalidResponse, AIUnavailable
from ai.ollama_client import OllamaClient, ProviderContent
from ai.openai_client import OpenAIClient
from ai.prompt_templates import AIRequest
from ai.schemas import Reply, decode_reply
from core.database import Database
from core.settings import Settings
from trading.types import Clock, aware_utc


class AIProvider(Protocol):
    name: str
    configured: bool

    async def complete(self, messages: list[dict], schema: dict) -> ProviderContent: ...
    async def close(self): ...


@dataclass(frozen=True, slots=True)
class RoutedReply:
    reply: Reply
    provider: str
    provider_model: str
    observed_at: datetime
    simulated: bool


@dataclass(slots=True)
class Circuit:
    failures: int = 0
    blocked_until: float = 0


class AIRouter:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Clock,
        *,
        providers: tuple[AIProvider, ...] | None = None,
    ):
        self.settings, self.database, self.clock = settings, database, clock
        selected = providers if providers is not None else (OllamaClient(settings), OpenAIClient(settings))
        if (
            len(selected) > 2
            or len({p.name for p in selected}) != len(selected)
            or any(p.name not in {"ollama", "openai"} for p in selected)
        ):
            raise ValueError("exact configured provider identities required")
        self.providers = {p.name: p for p in selected}
        self.circuits = {p.name: Circuit() for p in selected}
        self._slots = asyncio.Semaphore(settings.ai_max_concurrent)
        self._closed = False

    async def _audit(self, action, request, **details):
        await asyncio.to_thread(
            self.database.audit,
            action,
            "ai",
            {
                "request_hash": request.request_hash,
                "purpose": request.purpose,
                **details,
            },
        )

    async def complete(self, request: AIRequest) -> RoutedReply | None:
        if self._closed:
            raise AIUnavailable("router_closed")
        cfg = self.settings
        if cfg.ai_provider == "disabled":
            await self._audit("ai.disabled_veto", request)
            return None  # A disabled primary never silently selects a fallback.
        started = aware_utc(self.clock.now())
        order = tuple(dict.fromkeys((cfg.ai_provider, cfg.ai_fallback_provider)))
        try:
            async with asyncio.timeout(cfg.ai_timeout_seconds):
                async with self._slots:
                    for name in order:
                        if self.clock.now() > request.expires_at:
                            await self._audit("ai.expired_veto", request)
                            return None
                        provider = self.providers.get(name)
                        if provider is None or not provider.configured:
                            await self._audit(
                                "ai.provider_unavailable", request, provider=name, reason="not_configured"
                            )
                            continue
                        circuit = self.circuits[name]
                        if time.monotonic() < circuit.blocked_until:
                            await self._audit(
                                "ai.provider_unavailable", request, provider=name, reason="circuit_open"
                            )
                            continue
                        remaining = cfg.ai_timeout_seconds
                        if (
                            name == order[0]
                            and len(order) > 1
                            and self.providers.get(order[-1]) is not None
                            and self.providers[order[-1]].configured
                        ):
                            # Reserve at least half of the total budget for availability fallback.
                            remaining = max(0.25, cfg.ai_timeout_seconds / 2)
                        try:
                            async with asyncio.timeout(remaining):
                                content = await provider.complete(request.messages(), request.schema())
                        except (AIUnavailable, TimeoutError):
                            circuit.failures += 1
                            if circuit.failures >= cfg.ai_circuit_failures:
                                circuit.blocked_until = time.monotonic() + cfg.ai_circuit_cooldown_seconds
                            await self._audit(
                                "ai.provider_unavailable",
                                request,
                                provider=name,
                                reason="availability_or_timeout",
                            )
                            continue
                        # Invalid envelopes/content/bindings do NOT use another provider to get approval.
                        if content.provider != name:
                            raise AIInvalidResponse("provider_identity_changed")
                        reply = decode_reply(content.content, request.purpose, request.payload())
                        if self.clock.now() > request.expires_at:
                            await self._audit("ai.expired_veto", request)
                            return None
                        circuit.failures, circuit.blocked_until = 0, 0
                        await self._audit(
                            "ai.reviewed",
                            request,
                            provider=name,
                            model=content.model,
                            simulated=content.simulated,
                            confidence=reply.confidence,
                            decision=getattr(reply, "decision", getattr(reply, "action", "advisory")),
                        )
                        return RoutedReply(reply, name, content.model, started, content.simulated)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._audit("ai.timeout_veto", request)
            return None
        except AIInvalidResponse:
            await self._audit("ai.invalid_response_veto", request, reason="invalid_or_unbound_contract")
            return None
        except Exception:
            # No raw HTTP/validation/credential/provider exception escapes into audit.
            await self._audit("ai.unexpected_error_veto", request, reason="unexpected_provider_failure")
            return None
        await self._audit("ai.unavailable_veto", request)
        return None

    async def close(self):
        self._closed = True
        await asyncio.gather(*(p.close() for p in self.providers.values()))
