"""Strict Telegram bot-token HMAC validation of the ORIGINAL initData string.

HMAC(WebAppData, bot_token) -> raw key; HMAC(key, sorted decoded fields).
Exclude ONLY hash in bot-token mode: optional signature remains signed.
Ed25519 third-party verification is a different protocol and is not used here.
Never use initDataUnsafe, reserialize user JSON, trust request user_id, or extend
an old Telegram auth_date. Signed initData is a replayable bearer within its TTL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from app.owner_identity import OwnerIdentity, OwnerInterfaceError
from core.settings import Settings
from trading.types import Clock

BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
INTEGER = re.compile(r"[1-9][0-9]{0,11}\Z")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _not_number(value):
    raise ValueError("nonfinite")


def strict_json(raw: str | bytes):
    """Used for identity/request parsing, not to change signed input bytes."""
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_not_number)


def validate_init_data(raw: str, settings: Settings, clock: Clock) -> OwnerIdentity:
    invalid = OwnerInterfaceError("owner_authentication_required", 401)
    token = settings.telegram_bot_token.get_secret_value()
    if not token or settings.telegram_owner_id is None:
        raise invalid
    try:
        if (
            type(raw) is not str
            or not 1 <= len(raw) <= settings.telegram_initdata_max_bytes
            or not raw.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in raw)
            or BAD_PERCENT.search(raw)
        ):
            raise ValueError
        pairs = parse_qsl(
            raw,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=32,
            separator="&",
        )
        fields = _unique(pairs)
        if (
            not {"hash", "auth_date", "user"}.issubset(fields)
            or any(
                not KEY.fullmatch(k) or not v or any(ord(c) < 32 or ord(c) == 127 for c in v)
                for k, v in fields.items()
            )
            or not HASH.fullmatch(fields["hash"])
        ):
            raise ValueError
        received_hash = fields.pop("hash")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError
        if not INTEGER.fullmatch(fields["auth_date"]):
            raise ValueError
        issued = datetime.fromtimestamp(int(fields["auth_date"]), timezone.utc)
        user = strict_json(fields["user"])
        if (
            type(user) is not dict
            or type(user.get("id")) is not int
            or not 0 < user["id"] < 2**53
            or ("is_bot" in user and type(user["is_bot"]) is not bool)
            or user.get("is_bot") is True
            or fields.get("chat_type") not in {None, "sender", "private"}
        ):
            raise ValueError
        if "chat" in fields:
            chat = strict_json(fields["chat"])
            if (
                type(chat) is not dict
                or chat.get("type") != "private"
                or type(chat.get("id")) is not int
                or chat["id"] != user["id"]
            ):
                raise ValueError
        if user["id"] != settings.telegram_owner_id:
            raise OwnerInterfaceError("owner_access_denied", 403)
        identity = OwnerIdentity(
            owner_id=user["id"],
            transport="miniapp",
            credential_hash=hashlib.sha256(raw.encode()).hexdigest(),
            config_hash=settings.safety_fingerprint(),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=settings.telegram_initdata_max_age_seconds),
        )
        identity.require(settings, clock)
        return identity
    except OwnerInterfaceError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, OSError, RecursionError):
        raise invalid from None
