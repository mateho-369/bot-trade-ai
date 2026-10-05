"""Real aiogram dispatcher + SCRIPTED local session, zero Telegram HTTP."""

import pytest
from aiogram.types import MessageOriginUser, User
from sqlalchemy import select

from core.models import OwnerApproval
from tests.owner_helpers import callback_update, fake_transport, message_update, owner_runtime, owner_services
from tests.risk_helpers import OWNER


@pytest.mark.parametrize(
    "command",
    [
        "/start",
        "/help",
        "/status",
        "/dashboard",
        "/positions",
        "/trades",
        "/signals",
        "/news",
        "/suggestions",
        "/settings",
        "/logs",
    ],
)
async def test_owner_read_command_actual_dispatcher_plain_text(tmp_path, command):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, command))
    replies = [method for name, method in session.calls if name == "SendMessage"]
    assert len(replies) == 1 and replies[0].chat_id == OWNER
    assert replies[0].parse_mode is None and len(replies[0].text) <= 3800
    assert services.settings.telegram_bot_token.get_secret_value() not in replies[0].text
    await transport.close()


@pytest.mark.parametrize(
    "change",
    [
        {"sender": 43},
        {"chat_id": 43},
        {"chat_id": -99, "chat_type": "group"},
        {"chat_id": -10001, "chat_type": "supergroup"},
        {"age": 60},
        {"age": -6},
        {"business_connection_id": "business"},
    ],
)
async def test_nonowner_group_stale_future_business_no_reply_no_action(tmp_path, change):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/kill", **change))
    assert session.calls == [] and services.database.status()["state"] == "paused"
    with services.database.session() as sql:
        assert not sql.scalar(select(OwnerApproval.id))
    await transport.close()


async def test_forwarded_owner_text_is_not_authority(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    origin = MessageOriginUser(
        type="user", date=services.clock.now(), sender_user=User(id=OWNER, is_bot=False, first_name="Fixture")
    )
    await transport.dispatcher.feed_update(
        transport.bot, message_update(services, "/kill", forward_origin=origin)
    )
    assert session.calls == [] and services.database.status()["state"] == "paused"
    await transport.close()


async def test_via_bot_command_and_oversized_message_denied(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    via = User(id=88, is_bot=True, first_name="Other bot")
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/kill", via_bot=via))
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/kill " + "x" * 513))
    assert session.calls == []
    await transport.close()


@pytest.mark.parametrize("command,state", [("/pause", "paused"), ("/kill", "killed")])
async def test_safe_downward_command_without_broker_lease(tmp_path, command, state):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, command))
    assert services.database.status()["state"] == state
    assert len(session.calls) == 1
    await transport.close()


@pytest.mark.parametrize(
    "command",
    [
        "/pause extra",
        "/resume extra",
        "/kill now",
        "/close",
        "/close 1",
        "/close 1 2 3",
        "/close -1 2",
        "/approve",
        "/approve true",
        "/reject 0",
        "/approve 9223372036854775808",
    ],
)
async def test_invalid_arguments_no_effect(tmp_path, command):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, command))
    assert services.database.status()["state"] == "paused"
    assert "denied" in session.calls[-1][1].text.lower()
    await transport.close()


async def test_resume_button_actual_authenticated_callback_consumes_once(tmp_path):
    services = await owner_runtime(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/resume"))
    reply = session.calls[-1][1]
    button = reply.reply_markup.inline_keyboard[0][0].callback_data
    assert button.startswith("confirm:") and len(button.encode()) <= 64
    callback = callback_update(services, transport.bot, button)
    await transport.dispatcher.feed_update(transport.bot, callback)
    assert services.database.status()["state"] == "running"
    await transport.dispatcher.feed_update(transport.bot, callback)
    assert "Replayed outcome: True" in session.calls[-1][1].text
    await transport.close()
    await services.execution.shutdown()
    services.database.close()


async def test_cancel_callback_no_resume(tmp_path):
    services = await owner_runtime(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/resume"))
    reply = session.calls[-1][1]
    button = reply.reply_markup.inline_keyboard[0][1].callback_data
    await transport.dispatcher.feed_update(transport.bot, callback_update(services, transport.bot, button))
    assert services.database.status()["state"] == "paused"
    assert "canceled" in session.calls[-1][1].text
    await transport.close()
    await services.execution.shutdown()
    services.database.close()


@pytest.mark.parametrize(
    "change",
    [{"sender": 43}, {"chat_id": 43}, {"chat_id": -5, "chat_type": "group"}, {"age": 61}, {"age": -6}],
)
async def test_callback_guards_no_forged_ownership(tmp_path, change):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.dispatcher.feed_update(
        transport.bot, callback_update(services, transport.bot, "ctl:kill", **change)
    )
    assert session.calls == [] and services.database.status()["state"] == "paused"
    await transport.close()


async def test_owner_menu_scoped_private_and_https_url(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    await transport.set_owner_menu()
    assert [name for name, _ in session.calls] == ["SetMyCommands", "SetChatMenuButton"]
    assert session.calls[0][1].scope.chat_id == OWNER
    assert session.calls[1][1].chat_id == OWNER
    assert session.calls[1][1].menu_button.web_app.url == "https://owner.example"
    await transport.close()


async def test_plain_headlines_no_html_execution_or_parse_mode(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    from core.models import News

    with services.database.session() as sql:
        sql.add(
            News(
                time=services.clock.now(),
                fetched_at=services.clock.now(),
                source="fixture",
                title="<script>SECRET_MARKER</script>",
                content_hash="f" * 64,
            )
        )
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/news"))
    assert "<script>" in session.calls[-1][1].text and session.calls[-1][1].parse_mode is None
    await transport.close()


async def test_bot_safe_stop_reserved_budget_and_ordinary_drop(tmp_path):
    services = owner_services(tmp_path, api_rate_limit_per_minute=5)
    transport, session = fake_transport(services)
    for i in range(6):
        await transport.dispatcher.feed_update(
            transport.bot, message_update(services, "/status", message_id=i + 1, update_id=i + 1)
        )
    assert len(session.calls) == 5
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/kill", message_id=20))
    assert services.database.status()["state"] == "killed"
    await transport.close()


async def test_reply_delivery_failure_suppressed_not_claimed_success(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services, fail_methods=("SendMessage",))
    await transport.dispatcher.feed_update(transport.bot, message_update(services, "/status"))
    assert len(session.calls) == 1
    await transport.close()
