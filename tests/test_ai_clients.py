import json
from datetime import timedelta

import httpx
import pytest

from ai.json_validation import AIInvalidResponse, AIUnavailable
from ai.ollama_client import OllamaClient
from ai.openai_client import OpenAIClient
from ai.prompt_templates import make_request
from tests.ai_helpers import ScriptedHTTP
from tests.risk_helpers import MOMENT, config
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind


def req(cfg):
    return make_request(
        "market",
        {},
        settings=cfg,
        profile=RuntimeProfile.current(cfg, SourceKind.SYNTHETIC),
        as_of=MOMENT,
        expires_at=MOMENT + timedelta(seconds=30),
    )


@pytest.mark.parametrize("name", ["ollama", "openai"])
async def test_real_adapter_request_shape_offline_and_no_key_in_prompt(tmp_path, name):
    cfg = config(tmp_path, openai_api_key="TEST_OPENAI_SECRET_ONLY")
    fixture = ScriptedHTTP(name)
    client = (OllamaClient if name == "ollama" else OpenAIClient)(cfg, transport=fixture.transport)
    request = req(cfg)
    result = await client.complete(request.messages(), request.schema())
    assert result.simulated and result.provider == name
    captured = fixture.requests[0]
    data = json.loads(captured.content)
    assert (
        captured.method == "POST"
        and data["stream"] is False
        and data["temperature" if name == "openai" else "options"] is not None
    )
    assert "TEST_OPENAI_SECRET_ONLY" not in captured.content.decode()
    assert captured.headers["accept-encoding"] == "identity"
    if name == "openai":
        assert (
            captured.headers["authorization"] == "Bearer TEST_OPENAI_SECRET_ONLY"
            and captured.url.path == "/v1/chat/completions"
        )
    else:
        assert "authorization" not in captured.headers and captured.url.path == "/api/chat"
    await client.close()


@pytest.mark.parametrize("name", ["ollama", "openai"])
@pytest.mark.parametrize(
    "status,exception",
    [
        (302, AIInvalidResponse),
        (400, AIInvalidResponse),
        (401, AIUnavailable),
        (404, AIUnavailable),
        (429, AIUnavailable),
        (500, AIUnavailable),
    ],
)
async def test_http_failure_is_generic_and_never_followed(tmp_path, name, status, exception):
    cfg = config(tmp_path, openai_api_key="TEST_HTTP_KEY")
    fixture = ScriptedHTTP(name, status=status)
    client = (OllamaClient if name == "ollama" else OpenAIClient)(cfg, transport=fixture.transport)
    request = req(cfg)
    with pytest.raises(exception) as failure:
        await client.complete(request.messages(), request.schema())
    assert "TEST_HTTP_KEY" not in str(failure.value) and len(fixture.requests) == 1
    await client.close()


@pytest.mark.parametrize("name", ["ollama", "openai"])
@pytest.mark.parametrize(
    "headers,raw",
    [
        ({"content-type": "text/html"}, b"password=LEAK"),
        ({"content-type": "application/json", "content-length": "bad"}, b"{}"),
        ({"content-type": "application/json", "content-length": "999999"}, b"{}"),
        ({"content-type": "application/json"}, b"x" * 70000),
        ({"content-type": "application/json"}, b"\xff"),
        ({"content-type": "application/json"}, b'{"a":1,"a":2}'),
        ({"content-type": "application/json"}, b"[]"),
    ],
)
async def test_body_size_media_utf8_and_duplicate_guards(tmp_path, name, headers, raw):
    cfg = config(tmp_path, openai_api_key="TEST_HTTP_KEY")
    client = (OllamaClient if name == "ollama" else OpenAIClient)(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200, headers=headers, content=raw))
    )
    request = req(cfg)
    with pytest.raises(AIInvalidResponse):
        await client.complete(request.messages(), request.schema())
    await client.close()


@pytest.mark.parametrize(
    "envelope",
    [
        {"done": False},
        {"done_reason": "length"},
        {"model": "different-model"},
        {"error": "password=LEAK"},
        {"message": {"role": "assistant", "content": "{}", "tool_calls": [{"x": 1}]}},
        {"message": {"role": "user", "content": "{}"}},
        {"message": {"role": "assistant", "content": ""}},
    ],
)
async def test_ollama_envelope_contract(tmp_path, envelope):
    cfg = config(tmp_path)
    fixture = ScriptedHTTP(envelope_changes=envelope)
    client = OllamaClient(cfg, transport=fixture.transport)
    request = req(cfg)
    with pytest.raises(AIInvalidResponse):
        await client.complete(request.messages(), request.schema())
    await client.close()


@pytest.mark.parametrize(
    "envelope",
    [
        {"choices": []},
        {"model": "different-model"},
        {
            "choices": [
                {"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "{}"}}
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": None, "refusal": "refused"},
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}", "tool_calls": [{}]},
                }
            ]
        },
    ],
)
async def test_openai_envelope_contract(tmp_path, envelope):
    cfg = config(tmp_path, openai_api_key="TEST_ONLY")
    fixture = ScriptedHTTP("openai", envelope_changes=envelope)
    client = OpenAIClient(cfg, transport=fixture.transport)
    request = req(cfg)
    with pytest.raises(AIInvalidResponse):
        await client.complete(request.messages(), request.schema())
    await client.close()


async def test_missing_key_makes_zero_http_requests(tmp_path):
    cfg = config(tmp_path)
    fixture = ScriptedHTTP("openai")
    client = OpenAIClient(cfg, transport=fixture.transport)
    request = req(cfg)
    with pytest.raises(AIUnavailable):
        await client.complete(request.messages(), request.schema())
    assert not fixture.requests
    await client.close()
