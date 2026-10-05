"""Logical-clock ingestion regressions; only synthetic book, never market proof."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from core.models import BrokerDeal
from tests.risk_helpers import make_engine, open_one
from trading.types import ManualClock


@pytest.mark.parametrize("year", [2020, 2026, 2030])
async def test_first_ingestion_uses_bound_clock_and_is_not_refreshed_on_repeat(tmp_path, year):
    start = datetime(year, 1, 1, 12, tzinfo=timezone.utc)
    clock = ManualClock(start)
    engine = await make_engine(tmp_path, clock=clock)
    try:
        await open_one(engine)
        with engine.database.session() as db:
            original = {row.ticket: row.ingested_at for row in db.scalars(select(BrokerDeal)).all()}
            assert original and set(original.values()) == {start}
        clock.advance(timedelta(seconds=5))
        position = (await engine.broker.get_positions())[0]
        await engine.close_owned(position.ticket, position.identifier)
        with engine.database.session() as db:
            rows = db.scalars(select(BrokerDeal)).all()
            assert len(rows) == 2 and all(row.ingested_at <= clock.now() for row in rows)
            assert {row.ticket: row.ingested_at for row in rows if row.ticket in original} == original
            recorded = {row.ticket: row.ingested_at for row in rows}
        clock.advance(timedelta(seconds=7))
        await engine.reconcile()
        with engine.database.session() as db:
            assert {row.ticket: row.ingested_at for row in db.scalars(select(BrokerDeal)).all()} == recorded
    finally:
        await engine.shutdown()
        engine.database.close()
