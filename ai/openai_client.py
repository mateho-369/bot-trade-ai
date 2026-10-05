"""OpenAI-compatible chat-completions adapter with local strict JSON enforcement."""

from __future__ import annotations

import re

import httpx

from ai.http_transport import JSONTransport
from ai.json_validation import AIInvalidResponse, AIUnavailable
from ai.ollama_client import ProviderContent
from core.settings import Settings


class OpenAIClient:
    name = "openai"

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.http = JSONTransport(settings, transport=transport)

    @property
    def configured(self):
        return bool(self.settings.openai_api_key.get_secret_value())

    async def complete(self, messages: list[dict], schema: dict) -> ProviderContent:
        cfg = self.settings
        if not self.configured:
            raise AIUnavailable("missing_api_key")
        payload = {
            "model": cfg.openai_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": cfg.ai_max_output_tokens,
            "stream": False,
            "response_format": self._response_format(schema),
        }
        if cfg.openai_reasoning_effort:
            payload["reasoning_effort"] = cfg.openai_reasoning_effort
        response = await self.http.post(
            cfg.openai_base_url + "/chat/completions",
            payload,
            headers={"Authorization": "Bearer " + cfg.openai_api_key.get_secret_value()},
        )
        try:
            choices, model = response["choices"], response["model"]
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(model, str):
                raise ValueError
            # Alias or its dated resolution only; never accept an arbitrary different model.
            if model != cfg.openai_model and not re.fullmatch(
                re.escape(cfg.openai_model) + r"-\d{4}-\d{2}-\d{2}", model
            ):
                raise ValueError
            choice = choices[0]
            if choice.get("finish_reason") != "stop" or choice.get("index") != 0:
                raise ValueError
            message = choice["message"]
            if (
                message.get("role") != "assistant"
                or message.get("refusal")
                or message.get("tool_calls")
                or message.get("function_call")
            ):
                raise ValueError
            text = message["content"]
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384:
                raise ValueError
            return ProviderContent(self.name, model, text, self.http.simulated)
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            raise AIInvalidResponse("invalid_openai_envelope") from None

    def _response_format(self, schema: dict) -> dict:
        """Provider JSON enforcement. Local strict decoding/bindings run regardless of this mode."""
        if self.settings.openai_response_format != "json_schema_strict":
            return {"type": "json_object"}
        from ai.schemas import REPLY_CLASSES, strict_output_schema

        purpose = next(
            (name for name, cls in REPLY_CLASSES.items() if cls.model_json_schema() == schema), None
        )
        if purpose is None:
            raise AIInvalidResponse("unknown_reply_schema")  # Never send an unreviewed schema.
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"reflex_ai_{purpose}_v1",
                "strict": True,
                "schema": strict_output_schema(purpose),
            },
        }

    async def close(self):
        await self.http.close()
