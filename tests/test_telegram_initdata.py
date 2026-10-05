"""Local known-key HMAC regression only; NOT actual Telegram-issued initData."""

import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode

import pytest
from aiogram.utils.web_app import check_webapp_signature

from app.owner_identity import OwnerInterfaceError
from scripts.synthetic_owner_fixtures import signed_fixture
from telegram_bot.miniapp_auth import validate_init_data
from tests.owner_helpers import DEFAULTS
from tests.risk_helpers import MOMENT, config
from trading.types import ManualClock


@pytest.fixture
def auth(tmp_path):
    return config(tmp_path, **DEFAULTS), ManualClock(MOMENT)


def denied(raw, auth, status=401):
    with pytest.raises(OwnerInterfaceError) as error:
        validate_init_data(raw, *auth)
    assert error.value.status == status
    assert raw not in str(error.value) if isinstance(raw, str) and raw else True


def test_official_bot_hmac_and_aiogram_contract(auth):
    settings, clock = auth
    raw = signed_fixture(settings, clock)
    assert check_webapp_signature(settings.telegram_bot_token.get_secret_value(), raw)
    who = validate_init_data(raw, settings, clock)
    assert who.owner_id == 42 and who.transport == "miniapp"
    assert who.expires_at - who.issued_at == timedelta(seconds=300)
    assert who.credential_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in repr(who)


@pytest.mark.parametrize(
    "fields",
    [
        {"signature": "SIGNED_OPTIONAL_SIGNATURE"},
        {"query_id": "unique-query"},
        {"start_param": "owner_console"},
        {"chat_type": "private"},
        {"chat_type": "sender", "chat_instance": "123"},
        {"future_field": "signed-value"},
    ],
)
def test_all_optional_fields_including_signature_are_in_bot_hmac(auth, fields):
    settings, clock = auth
    raw = signed_fixture(settings, clock, fields=fields)
    assert check_webapp_signature(settings.telegram_bot_token.get_secret_value(), raw)
    assert validate_init_data(raw, settings, clock).owner_id == 42


def test_excluding_signature_as_if_ed25519_does_not_validate(auth):
    settings, clock = auth
    values = dict(parse_qsl(signed_fixture(settings, clock, fields={"signature": "signature-value"})))
    values.pop("hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()) if k != "signature")
    key = hmac.new(
        b"WebAppData", settings.telegram_bot_token.get_secret_value().encode(), hashlib.sha256
    ).digest()
    values["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    denied(urlencode(values), auth)


@pytest.mark.parametrize("name", ["Fixture + - ? / \\", "ម្ចាស់", "Owner 😀", 'Quotes " and spaces'])
def test_raw_json_and_url_unicode_are_not_reserialized(auth, name):
    settings, clock = auth
    raw = signed_fixture(settings, clock, user={"id": 42, "first_name": name, "is_bot": False})
    assert validate_init_data(raw, settings, clock).owner_id == 42
    values = dict(parse_qsl(raw))
    values["user"] = json.dumps(json.loads(values["user"]), indent=2)
    denied(urlencode(values), auth)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        123,
        True,
        [],
        {},
        "",
        "x",
        "x=1",
        "x=%",
        "x=%GG",
        "x=%0",
        "x=%FF",
        "x=1&&y=2",
        "x=1&bare",
        "x=1;y=2",
        "x=\n1",
        "x=é",
        "x=1#fragment",
        "x=1" * 3000,
        "&".join(f"k{i}=v" for i in range(33)),
    ],
)
def test_malformed_bounded_input(auth, raw):
    denied(raw, auth)


@pytest.mark.parametrize("key", ["user", "auth_date", "hash", "%75ser", "%61uth_date"])
def test_duplicate_url_keys_never_last_wins(auth, key):
    settings, clock = auth
    raw = signed_fixture(settings, clock) + "&" + key + "=" + ("42" if "date" in key else "x")
    denied(raw, auth)


@pytest.mark.parametrize(
    "raw_user",
    [
        '{"id":42,"id":43}',
        '{"id":NaN}',
        '{"id":Infinity}',
        '{"id":-Infinity}',
        "null",
        "[]",
        "true",
        "42",
        '{"id":true}',
        '{"id":42.0}',
        '{"id":"42"}',
        '{"id":0}',
        '{"id":-42}',
        '{"first_name":"No ID"}',
        '{"id":9007199254740992}',
        '{"id":42,"is_bot":true}',
        '{"id":42,"is_bot":"false"}',
    ],
)
def test_signed_user_still_requires_strict_owner_identity(auth, raw_user):
    settings, clock = auth
    denied(signed_fixture(settings, clock, fields={"user": raw_user}), auth)


@pytest.mark.parametrize(
    "date", ["", "0", "-1", "true", "1.0", "1e9", "+1791028800", "01791028800", "9" * 40]
)
def test_auth_date_is_bounded_decimal_integer(auth, date):
    settings, clock = auth
    denied(signed_fixture(settings, clock, fields={"auth_date": date}), auth)


@pytest.mark.parametrize(
    "age,valid", [(0, True), (299, True), (300, False), (301, False), (-5, True), (-6, False)]
)
def test_expiry_and_future_skew_boundaries(auth, age, valid):
    settings, clock = auth
    raw = signed_fixture(
        settings, clock, fields={"auth_date": str(int((clock.now() - timedelta(seconds=age)).timestamp()))}
    )
    if valid:
        assert validate_init_data(raw, settings, clock).owner_id == 42
    else:
        denied(raw, auth)


def test_other_signed_telegram_user_cannot_be_owner(auth):
    settings, clock = auth
    denied(signed_fixture(settings, clock, user={"id": 43, "first_name": "Other"}), auth, 403)


@pytest.mark.parametrize(
    "field,value",
    [("user", '{"id":43}'), ("auth_date", "1791028801"), ("query_id", "injected"), ("hash", "0" * 64)],
)
def test_unsigned_field_changes_reject(auth, field, value):
    settings, clock = auth
    values = dict(parse_qsl(signed_fixture(settings, clock)))
    values[field] = value
    denied(urlencode(values), auth)


@pytest.mark.parametrize(
    "field,value",
    [
        ("chat_type", "group"),
        ("chat_type", "supergroup"),
        ("chat", '{"id":42,"type":"group"}'),
        ("chat", '{"id":43,"type":"private"}'),
        ("chat", '{"id":"42","type":"private"}'),
    ],
)
def test_group_or_cross_chat_miniapp_is_not_owner_private_launch(auth, field, value):
    settings, clock = auth
    denied(signed_fixture(settings, clock, fields={field: value}), auth)


def test_signed_matching_private_chat(auth):
    settings, clock = auth
    raw = signed_fixture(settings, clock, fields={"chat": '{"id":42,"type":"private"}'})
    assert validate_init_data(raw, settings, clock).owner_id == 42


def test_no_credentials_no_auth(tmp_path, auth):
    settings, clock = auth
    raw = signed_fixture(settings, clock)
    from core.settings import Settings

    blank = Settings(_env_file=None, project_root=tmp_path)
    denied(raw, (blank, clock))


def test_old_identity_is_not_renewed_by_client_clock(auth):
    settings, clock = auth
    who = validate_init_data(signed_fixture(settings, clock), settings, clock)
    clock.advance(timedelta(seconds=300))
    with pytest.raises(OwnerInterfaceError, match="owner_session_expired"):
        who.require(settings, clock)
