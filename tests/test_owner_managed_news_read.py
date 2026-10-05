"""Attached managed-news read is local snapshot projection, not refresh/HTTP."""

from sqlalchemy import select

from app.owner_services import OwnerServices
from core.models import AuditLog
from news.news_manager import NewsManager
from tests.owner_helpers import actor, owner_runtime


async def test_attached_managed_news_read_no_refresh_or_write(tmp_path, monkeypatch):
    first = await owner_runtime(tmp_path)
    manager = NewsManager(first.database, first.settings, first.clock, first.execution.profile, sources=())
    services = OwnerServices(first.database, first.settings, execution=first.execution, news=manager)

    async def forbidden(*args, **kwargs):
        raise AssertionError("GET must not refresh provider/market")

    monkeypatch.setattr(manager, "refresh", forbidden)
    with first.database.session() as sql:
        before = [r.id for r in sql.scalars(select(AuditLog)).all()]
    result = await services.read(actor(services), "news")
    assert (
        result["coverage"]["status"] == "unknown"
        and result["coverage"]["reason"] == "managed_news_unavailable"
    )
    assert result["calendar"] == []
    with first.database.session() as sql:
        assert [r.id for r in sql.scalars(select(AuditLog)).all()] == before
    await first.execution.shutdown()
    first.database.close()


async def test_current_managed_snapshot_calendar_and_symbol_projection_sql_only(tmp_path, monkeypatch):
    from tests.news_helpers import make_news_runtime

    manager, fixture = await make_news_runtime(tmp_path)
    await manager.refresh()
    services = OwnerServices(manager.database, manager.settings, clock=manager.clock, news=manager)
    with manager.database.session() as sql:
        before = [r.id for r in sql.scalars(select(AuditLog)).all()]

    async def forbidden(*args, **kwargs):
        raise AssertionError("read must never refresh")

    monkeypatch.setattr(manager, "refresh", forbidden)
    result = await services.read(actor(services), "news")
    assert result["coverage"]["status"] == "managed_projection"
    assert result["coverage"]["not_entry_permission"]
    assert result["coverage"]["symbols"]["EURUSD"]["fixture_only"]
    assert result["calendar"][0]["id"] == "synthetic-usd-event"
    with manager.database.session() as sql:
        assert [r.id for r in sql.scalars(select(AuditLog)).all()] == before
    await manager.close()
    manager.database.close()
