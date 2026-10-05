"""SQL-only projections; broker reads themselves can mutate a paper ledger."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.models import AccountSnapshot, AISuggestion, AuditLog, Trade
from tests.owner_helpers import api_client, auth_header, owner_runtime, owner_services
from tests.risk_helpers import open_one


async def test_all_gets_no_broker_provider_or_state_write(tmp_path, monkeypatch):
    services = await owner_runtime(tmp_path)
    await open_one(services.execution)
    store = services.suggestions
    proposal = store.create(
        "reduce_risk", {"risk_percent": "0.4"}, reason="Test proposal", request_hash="a" * 64
    )
    services.clock.advance(timedelta(seconds=services.settings.ai_suggestion_ttl_seconds))
    with services.database.session() as session:
        before = [r.id for r in session.scalars(select(AuditLog)).all()]
        snapshots = [r.id for r in session.scalars(select(AccountSnapshot)).all()]

    async def forbidden(*args, **kwargs):
        raise AssertionError("GET must not call broker")

    for method in [
        "get_positions",
        "get_account_info",
        "get_deals",
        "get_tick",
        "reconcile",
        "capture_owned_positions",
    ]:
        target = services.execution if hasattr(services.execution, method) else services.execution.broker
        monkeypatch.setattr(target, method, forbidden)
    async with api_client(services) as (client, _):
        for path in [
            "dashboard",
            "positions",
            "trades",
            "signals",
            "news",
            "ai_suggestions",
            "settings",
            "logs",
        ]:
            response = await client.get("/api/" + path, headers=auth_header(services))
            assert response.status_code == 200 and response.json()["meta"]["broker_read"] is False
        suggestions = (await client.get("/api/ai_suggestions", headers=auth_header(services))).json()["items"]
        assert suggestions[0]["status"] == "expired" and suggestions[0]["stored_status"] == "pending"
    with services.database.session() as session:
        assert [r.id for r in session.scalars(select(AuditLog)).all()] == before
        assert [r.id for r in session.scalars(select(AccountSnapshot)).all()] == snapshots
        assert session.get(AISuggestion, proposal.suggestion_id).status == "pending"
    # Avoid deliberately patched broker shutdown/reconcile; shutdown only calls actual shutdown.
    await services.execution.shutdown()
    services.database.close()


async def test_unattached_balance_equity_and_floating_pnl_are_unknown(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        data = (await client.get("/api/dashboard", headers=auth_header(services))).json()
    assert data["kpis"]["balance"] is None and data["kpis"]["equity"] is None
    assert data["equity_curve"] == [] and not data["runtime"]["lease_current"]
    assert not data["capabilities"]["resume"] and not data["capabilities"]["open_orders"]


async def test_stale_snapshot_is_labelled_not_rebranded_fresh(tmp_path):
    services = await owner_runtime(tmp_path)
    services.clock.advance(timedelta(seconds=31))
    data = services.views.dashboard()
    assert data["kpis"]["balance"] == "1000.00000000"
    assert data["kpis"]["observation_age_seconds"] == 31
    assert not data["kpis"]["observation_current"] and data["kpis"]["realized_today_account"] is None
    await services.execution.shutdown()
    services.database.close()


async def test_trade_money_is_account_currency_no_invented_usd(tmp_path):
    services = await owner_runtime(tmp_path)
    await open_one(services.execution)
    with services.database.locked_session() as session:
        trade = session.scalar(select(Trade))
        trade.currency = "EUR"
        trade.profit = Decimal("1.23")
        trade.profit_usd = Decimal("999")
        features = dict(trade.features_json)
        meta = dict(features["execution"])
        meta["profit_usd_verified"] = False
        features["execution"] = meta
        trade.features_json = features
    row = services.views.trades()["items"][0]
    assert row["currency"] == "EUR" and row["profit_account"] == "1.23000000"
    assert row["profit_usd"] is None and row["floating_pnl"] is None
    await services.execution.shutdown()
    services.database.close()


async def test_allowlisted_settings_and_logs_hide_payload_secrets(tmp_path):
    services = owner_services(tmp_path)
    services.database.audit(
        "test.secret_event", "test", {"private_blob": "SECRET_MARKER", "initData": "SIGNED_CREDENTIAL"}
    )
    async with api_client(services) as (client, _):
        settings = await client.get("/api/settings", headers=auth_header(services))
        logs = await client.get("/api/logs", headers=auth_header(services))
    for response in (settings, logs):
        assert "SECRET_MARKER" not in response.text and "SIGNED_CREDENTIAL" not in response.text
        assert services.settings.telegram_bot_token.get_secret_value() not in response.text
        assert "database_url" not in response.text and "mt5_password" not in response.text
    assert logs.json()["details_omitted_for_secret_safety"]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=10001"])
async def test_pagination_model_bounds(tmp_path, query):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        assert (await client.get("/api/trades?" + query, headers=auth_header(services))).status_code == 422


async def test_valid_pagination_does_not_fetch_unbounded_history(tmp_path):
    services = owner_services(tmp_path)
    async with api_client(services) as (client, _):
        response = await client.get("/api/logs?limit=1&offset=0", headers=auth_header(services))
    assert response.status_code == 200 and len(response.json()["items"]) == 1
