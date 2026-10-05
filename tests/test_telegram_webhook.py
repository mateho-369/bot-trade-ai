"""Random secret-header webhook, private owner middleware, no real Telegram HTTP."""

import pytest

from tests.owner_helpers import api_client, fake_transport, message_update, owner_services

SECRET = "TEST_ONLY_" + "a" * 40


@pytest.fixture
def services(tmp_path):
    return owner_services(
        tmp_path,
        telegram_use_webhook=True,
        telegram_webhook_url="https://owner.example/telegram/webhook",
        telegram_webhook_secret=SECRET,
    )


@pytest.mark.parametrize("secret", [None, "", "wrong", "a" * 257])
async def test_webhook_requires_exact_bounded_secret(services, secret):
    transport, session = fake_transport(services)
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}
    async with api_client(services, transport=transport) as (client, _):
        response = await client.post(
            "/telegram/webhook",
            json=message_update(services, "/kill").model_dump(mode="json"),
            headers=headers,
        )
    assert response.status_code == 403 and session.calls == []
    assert SECRET not in response.text
    await transport.close()


async def test_owner_webhook_executes_guarded_safe_stop_duplicate_idempotent(services):
    transport, session = fake_transport(services)
    data = message_update(services, "/kill").model_dump(mode="json")
    async with api_client(services, transport=transport) as (client, _):
        for _ in range(2):
            response = await client.post(
                "/telegram/webhook", json=data, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
            )
            assert response.status_code == 200
    assert services.database.status()["state"] == "killed"
    assert "Replayed outcome: True" in session.calls[-1][1].text
    await transport.close()


async def test_other_user_even_with_verified_webhook_transport_cannot_act(services):
    transport, session = fake_transport(services)
    async with api_client(services, transport=transport) as (client, _):
        response = await client.post(
            "/telegram/webhook",
            json=message_update(services, "/kill", sender=43).model_dump(mode="json"),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
    assert (
        response.status_code == 200
        and services.database.status()["state"] == "paused"
        and session.calls == []
    )
    await transport.close()


async def test_webhook_not_browser_api(services):
    transport, session = fake_transport(services)
    async with api_client(services, transport=transport) as (client, _):
        response = await client.post(
            "/telegram/webhook",
            json={},
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET, "Origin": "https://owner.example"},
        )
    assert response.status_code == 403 and session.calls == []
    await transport.close()


async def test_invalid_update_generic_without_input(services):
    transport, session = fake_transport(services)
    async with api_client(services, transport=transport) as (client, _):
        response = await client.post(
            "/telegram/webhook",
            json={"update_id": "SECRET_MARKER", "message": {}},
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
    assert response.status_code == 422 and "SECRET_MARKER" not in response.text
    await transport.close()


async def test_polling_and_webhook_no_automatic_remote_deletion(services):
    transport, session = fake_transport(services)
    from app.owner_identity import OwnerInterfaceError

    with pytest.raises(OwnerInterfaceError):
        await transport.poll()
    assert session.calls == []
    await transport.register_webhook()
    assert session.calls[-1][0] == "SetWebhook"
    assert session.calls[-1][1].secret_token == SECRET and not session.calls[-1][1].drop_pending_updates
    await transport.close()


async def test_polling_refuses_existing_webhook_no_delete(tmp_path):
    services = owner_services(tmp_path)
    transport, session = fake_transport(services)
    session.webhook_url = "https://another.example/hook"
    from app.owner_identity import OwnerInterfaceError

    with pytest.raises(OwnerInterfaceError, match="remove_existing_webhook"):
        await transport.poll()
    assert [name for name, _ in session.calls] == ["GetWebhookInfo"]
    await transport.close()
