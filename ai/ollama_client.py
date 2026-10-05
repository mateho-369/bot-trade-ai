"""Ollama /api/chat adapter. No terminal/model downloads or startup side effects."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ai.http_transport import JSONTransport
from ai.json_validation import AIInvalidResponse
from core.settings import Settings


@dataclass(frozen=True, slots=True)
class ProviderContent:
    provider: str
    model: str
    content: str
    simulated: bool = False


class OllamaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str | None = None,
        model: str | None = None,
        label: str | None = None,
    ):
        self.settings = settings
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.name = label or "ollama"
        self.http = JSONTransport(settings, transport=transport)

    @property
    def configured(self):
        return bool(self.model)

    async def complete(self, messages: list[dict], schema: dict) -> ProviderContent:
        cfg = self.settings
        response = await self.http.post(
            self.base_url + "/api/chat",
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0, "num_predict": cfg.ai_max_output_tokens},
            },
        )
        try:
            message, model = response["message"], response["model"]
            allowed = {self.model}
            if ":" not in self.model.rsplit("/", 1)[-1]:
                allowed.add(self.model + ":latest")
            if (
                response.get("done") is not True
                or response.get("done_reason") not in {None, "stop"}
                or model not in allowed
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
                or message.get("tool_calls")
                or response.get("error")
            ):
                raise ValueError
            text = message["content"]
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384:
                raise ValueError
            return ProviderContent(self.name, model, text, self.http.simulated)
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise AIInvalidResponse("invalid_ollama_envelope") from None

    async def close(self):
        await self.http.close()
