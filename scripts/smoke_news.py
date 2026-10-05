"""Offline Part 8 contracts; local fixture HTTP only, zero native/simulated orders."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from core.database import Database
from core.models import BotState
from core.settings import Settings
from news.evidence import verify_window
from news.http_client import NewsHTTP
from news.news_manager import NewsManager
from scripts.synthetic_news_fixtures import ScriptedNewsHTTP, news_fixture_settings
from scripts.synthetic_signal_market import ANCHOR
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind


def verified(manager, window, logical="EURUSD"):
    with manager.database.session() as session:
        return verify_window(
            session,
            window,
            logical_symbol=logical,
            settings=manager.settings,
            profile=manager.profile,
            now=manager.clock.now(),
        )


async def run(directory: Path):
    cfg = Settings(
        _env_file=None, project_root=directory, symbols=("EURUSD", "GBPUSD"), **news_fixture_settings()
    )
    database = Database(cfg)
    database.initialize()
    clock = ManualClock(ANCHOR)
    fixture = ScriptedNewsHTTP(clock)
    http = NewsHTTP(cfg, transport=fixture.transport)
    manager = NewsManager(database, cfg, clock, RuntimeProfile.current(cfg, SourceKind.SYNTHETIC), http=http)
    try:
        await manager.initialize()
        startup = not (await manager.window("EURUSD")).known
        result = await manager.refresh()
        safe = await manager.window("EURUSD")
        current = verified(manager, safe)
        cross = not verified(manager, safe, "GBPUSD")
        publication = (await manager.list_news())[0]["published_at"]
        first_seen = (await manager.list_news())[0]["first_seen_at"]
        clock.advance(timedelta(seconds=30))
        fixture.not_modified = True
        await manager.refresh()
        new = await manager.window("EURUSD")
        timestamps = (await manager.list_news())[0]
        unchanged = publication == timestamps["published_at"] and first_seen == timestamps["first_seen_at"]
        epoch = not verified(manager, safe) and verified(manager, new)
        clock.advance(timedelta(seconds=30))
        fixture.not_modified = False
        fixture.title = "USD emergency FOMC rate decision"
        await manager.refresh()
        breaking = await manager.decision("EURUSD")
        headline = breaking.window.known and not breaking.allowed
        # Calendar is independently decisive after another poll with no active headline.
        clock.advance(timedelta(minutes=61))
        fixture.title = "Fresh quiet EUR USD GBP commentary"
        at = clock.now() + timedelta(minutes=31)
        fixture.events = [
            {
                "id": "synthetic-gbp-release",
                "title": "SYNTHETIC GBP release",
                "currency": "GBP",
                "starts_at": at.isoformat(),
                "ends_at": at.isoformat(),
                "impact": "high",
                "tentative": False,
            }
        ]
        await manager.refresh()
        eur = await manager.window("EURUSD")
        gbp = await manager.window("GBPUSD")
        currency = (
            eur.known
            and eur.safe
            and gbp.known
            and gbp.safe
            and gbp.expires_at == clock.now() + timedelta(minutes=1)
        )
        clock.advance(timedelta(minutes=1))
        future = not gbp.allows(cfg, clock.now())
        gbp_now = await manager.decision("GBPUSD")
        calendar_block = not gbp_now.allowed and "synthetic-gbp-release" in gbp_now.blocking_ids
        failed_old = await manager.window("EURUSD")
        fixture.calendar_status = 503
        await manager.refresh()
        failure = not (await manager.window("EURUSD")).known and not verified(manager, failed_old)
        with database.session() as session:
            paused = session.get(BotState, 1).desired_state == "paused"
        checks = {
            "startup_unknown": startup,
            "fresh_scoped_fixture_proof": current,
            "cross_symbol_veto": cross,
            "304_preserves_publication_and_first_seen": unchanged,
            "new_epoch_revokes_old_green": epoch,
            "breaking_usd_news_blocks": headline,
            "calendar_currency_scope_and_future_deadline": currency,
            "pre_event_boundary_invalidates_without_poll": future,
            "independent_calendar_block": calendar_block,
            "provider_failure_never_restores_green": failure,
            "poll_does_not_resume_owner_state": paused,
        }
        if not all(checks.values()):
            raise RuntimeError("offline news contract failed")
        return {
            "synthetic": True,
            "scripted_http_transport_only": True,
            "actual_http_network_calls": 0,
            "real_orders": 0,
            "simulated_orders": 0,
            "native_sdk_imported": "MetaTrader5" in sys.modules,
            "eligible_stage_evidence": False,
            "symbols_initially_safe": result.symbols_safe,
            "fixture_http_requests": len(fixture.calls),
            "checks": checks,
            "warning": (
                "SCRIPTED headlines/calendar/HTTP; NOT entitled feed validation, authentic coverage, "
                "market evidence or live permission."
            ),
        }
    finally:
        await manager.close()
        database.close()


def main():
    with tempfile.TemporaryDirectory(prefix="reflex-news-offline-") as directory:
        result = asyncio.run(run(Path(directory)))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
