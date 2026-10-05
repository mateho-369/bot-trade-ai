import copy
import json
from datetime import timedelta

import pytest

from ai.json_validation import AIInvalidResponse, strict_json
from ai.prompt_templates import make_request
from ai.schemas import REPLY_CLASSES, decode_reply
from tests.ai_helpers import scripted_reply
from tests.risk_helpers import MOMENT, config
from trading.risk_types import RuntimeProfile
from trading.types import SourceKind


@pytest.fixture
def ai_request(tmp_path):
    cfg = config(tmp_path)
    return make_request(
        "entry",
        {},
        settings=cfg,
        profile=RuntimeProfile.current(cfg, SourceKind.SYNTHETIC),
        as_of=MOMENT,
        expires_at=MOMENT + timedelta(seconds=30),
        binding={"proposal_hash": "a" * 64, "news_hash": "b" * 64},
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "null",
        "true",
        "{} trailing",
        '{"a":1,"a":2}',
        '{"a":{"b":1,"b":2}}',
        '{"a":NaN}',
        '{"a":Infinity}',
        '{"a":1e999}',
        '{"a":' + "9" * 25 + "}",
        "```\n{}\n```",
        "before {} after",
        '{"a":"\\u0000"}',
        '{"a":"\\ud800"}',
        b"\xff",
    ],
)
def test_strict_json_refuses_ambiguous_nonfinite_or_unsafe_input(raw):
    with pytest.raises(AIInvalidResponse):
        strict_json(raw, allow_fence=True)


def test_json_bounds_and_exact_fence():
    assert strict_json('```json\n{"a":1}\n```', allow_fence=True) == {"a": 1}
    for raw, kwargs in [
        ("{}", {"max_bytes": 1}),
        ('{"a":"aaa"}', {"max_string": 2}),
        ('{"a":[1,2,3]}', {"max_array": 2}),
        ('{"a":{"b":1}}', {"max_depth": 1}),
        ('{"a":[1,2,3]}', {"max_nodes": 3}),
    ]:
        with pytest.raises(AIInvalidResponse):
            strict_json(raw, **kwargs)


@pytest.mark.parametrize(
    "key,value",
    [
        ("confidence", True),
        ("confidence", "90"),
        ("confidence", -1),
        ("confidence", 101),
        ("confidence", None),
        ("decision", "buy"),
        ("risk_percent", True),
        ("risk_percent", 0.2),
        ("risk_percent", "NaN"),
        ("risk_percent", "0"),
        ("risk_percent", "1.1"),
        ("risk_percent", ".2"),
        ("reason_codes", []),
        ("reason_codes", ["bogus"]),
        ("reason_codes", ["uncertain", "uncertain"]),
        ("rationale", ""),
        ("rationale", "a" * 601),
        ("rationale", "newline\nunsafe"),
        ("request_hash", "d" * 64),
        ("source", "mt5"),
        ("code_hash", "d" * 64),
        ("model_sha256", "d" * 64),
        ("proposal_hash", "d" * 64),
        ("news_hash", "d" * 64),
        ("volume", "0.2"),
        ("owner_id", 42),
        ("order_send", True),
        ("allow_live", True),
    ],
)
def test_entry_content_shape_and_binding_fail_closed(ai_request, key, value):
    values = scripted_reply(ai_request.payload())
    values[key] = value
    with pytest.raises(AIInvalidResponse):
        decode_reply(json.dumps(values), "entry", ai_request.payload())


def test_all_purpose_schemas_require_exact_known_fields(tmp_path):
    cfg = config(tmp_path)
    profile = RuntimeProfile.current(cfg, SourceKind.SYNTHETIC)
    for purpose in REPLY_CLASSES:
        req = make_request(
            purpose,
            {},
            settings=cfg,
            profile=profile,
            as_of=MOMENT,
            expires_at=MOMENT + timedelta(seconds=30),
            binding={"proposal_hash": "a" * 64, "news_hash": "b" * 64, "position_hash": "c" * 64},
        )
        values = scripted_reply(req.payload())
        decoded = decode_reply(json.dumps(values), purpose, req.payload())
        assert decoded.confidence == 90
        for key in tuple(values):
            bad = copy.deepcopy(values)
            bad.pop(key)
            with pytest.raises(AIInvalidResponse):
                decode_reply(json.dumps(bad), purpose, req.payload())


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "reduce_risk", "risk_percent": None},
        {"action": "no_change", "risk_percent": "0.1"},
        {"action": "rebalance_weights", "weights": {"trend": "1"}},
        {
            "action": "rebalance_weights",
            "weights": {"trend": 0.3, "mean_reversion": "0.25", "breakout": "0.2", "momentum": "0.25"},
        },
        {
            "action": "rebalance_weights",
            "weights": {"trend": "0.3", "mean_reversion": "0.25", "breakout": "0.2", "momentum": "0.3"},
        },
    ],
)
def test_settings_reply_cannot_smuggle_or_coerce_parameters(ai_request, changes):
    values = scripted_reply({**ai_request.payload(), "purpose": "settings"}, **changes)
    with pytest.raises(AIInvalidResponse):
        decode_reply(json.dumps(values), "settings", ai_request.payload())


def test_legitimate_decimal_risk_and_fresh_copy(ai_request):
    values = scripted_reply(ai_request.payload(), risk_percent="0.2")
    reply = decode_reply(json.dumps(values), "entry", ai_request.payload())
    assert str(reply.risk_percent) == "0.2"
    payload = ai_request.payload()
    payload["source"] = "mt5"
    assert ai_request.payload()["source"] == "synthetic"
