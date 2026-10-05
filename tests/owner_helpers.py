"""TEST ONLY local HMAC/update/ASGI fixtures; no genuine Telegram or broker I/O."""

from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from app.owner_services import OwnerServices
from core.database import Database
from miniapp.server import create_app
from scripts.synthetic_owner_fixtures import SyntheticTelegramSession, signed_fixture
from telegram_bot.bot import TelegramOwnerTransport
from telegram_bot.miniapp_auth import validate_init_data
from tests.risk_helpers import MOMENT, OWNER, config, make_engine
from trading.types import ManualClock

DEFAULTS = {
    "telegram_miniapp_url": "https://owner.example",
    "api_trusted_hosts": ("owner.example", "testserver", "localhost", "127.0.0.1"),
}


def owner_services(tmp_path, **changes):
    settings = config(tmp_path, **(DEFAULTS | changes))
    database = Database(settings)
    database.initialize()
    return OwnerServices(database, settings, clock=ManualClock(MOMENT))


async def owner_runtime(tmp_path, **changes):
    engine = await make_engine(tmp_path, **(DEFAULTS | changes))
    return OwnerServices(engine.database, engine.settings, execution=engine)


def actor(services, *, fields=None):
    return validate_init_data(
        signed_fixture(services.settings, services.clock, fields=fields), services.settings, services.clock
    )


def auth_header(services, **kwargs):
    return {"X-Telegram-Init-Data": signed_fixture(services.settings, services.clock, **kwargs)}


@asynccontextmanager
async def api_client(
    services, *, preview=False, transport=None, base_url="https://owner.example", headers=None
):
    app = create_app(services.settings, services, preview_only=preview, telegram_transport=transport)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=base_url,
            headers=headers,
        ) as client:
            yield client, app


def message_update(
    services,
    text,
    *,
    sender=OWNER,
    chat_id=None,
    chat_type="private",
    age=0,
    message_id=1,
    update_id=1,
    **extra,
):
    message = Message(
        message_id=message_id,
        date=services.clock.now() - timedelta(seconds=age),
        chat=Chat(id=sender if chat_id is None else chat_id, type=chat_type),
        from_user=User(id=sender, is_bot=False, first_name="Fixture owner"),
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))],
        **extra,
    )
    return Update(update_id=update_id, message=message)


def callback_update(
    services,
    bot,
    data,
    *,
    sender=OWNER,
    chat_id=None,
    chat_type="private",
    age=0,
    callback_id="fixture-callback-1",
    message=None,
):
    if message is None:
        message = Message(
            message_id=12345,
            date=services.clock.now() - timedelta(seconds=age),
            chat=Chat(id=sender if chat_id is None else chat_id, type=chat_type),
            from_user=User(id=bot.id, is_bot=True, first_name="Fixture bot"),
            text="Fixture owner menu",
        )
    callback = CallbackQuery(
        id=callback_id,
        from_user=User(id=sender, is_bot=False, first_name="Fixture owner"),
        chat_instance="TEST_ONLY_PRIVATE",
        message=message,
        data=data,
    )
    return Update(update_id=2, callback_query=callback)


def fake_transport(services, **kwargs):
    session = SyntheticTelegramSession(services.clock, **kwargs)
    return TelegramOwnerTransport(services, session=session), session
