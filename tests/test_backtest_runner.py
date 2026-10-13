"""Real core integration over engineered data. Results are NOT genuine market/AI/stage evidence."""

import json
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from backtesting.backtester import Backtester, isolated_settings
from backtesting.contracts import BacktestOptions
from backtesting.dataset import DatasetError, HistoricalDataset
from backtesting.market import HistoricalMarket
from backtesting.news import ReplayNews
from core.models import BotState, OrderIntent, RiskState
from core.settings import Settings
from tests.backtest_helpers import clone_dataset, document, finalized, fixture_path, runtime
from tests.risk_helpers import BAD_OPERATOR, OWNER
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, SourceKind, Tick, TradingDisabled


@pytest.fixture(scope="module")
def positive(tmp_path_factory):
    path = fixture_path(tmp_path_factory.mktemp("backtest_positive"), warmup=12240, minutes=2)
    return HistoricalDataset.load(path)


@pytest.fixture(scope="module")
def ohlc(tmp_path_factory):
    path = fixture_path(
        tmp_path_factory.mktemp("backtest_ohlc"), mode="ohlc_conservative", warmup=12240, minutes=2
    )
    return HistoricalDataset.load(path)


def opts(**changes):
    return BacktestOptions(
        review_mode="synthetic_research", simulate_orders=True, close_at_end=True, **changes
    )


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_default_research_no_operator_resume_no_approval_no_trade(positive, tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("no network in replay"))
    report = await Backtester(positive, Settings(_env_file=None)).run(tmp_path / "run")
    assert report["metrics"]["closed_trades"] == 0 and not report["simulated_execution_enabled"]
    assert not report["promotion_eligible"] and not report["live_enabled"]
    assert report["native_broker_calls"] == report["provider_calls"] == report["outbound_report_calls"] == 0
    assert "ai_unavailable_or_invalid" in report["veto_reasons"]
    metadata = document(tmp_path / "run/run.json")
    assert not metadata["effective_public_config"]["reporter_configured"]
    assert metadata["effective_public_config"]["offline_principal_only"]
    assert (tmp_path / "run/completion.json").is_file()
    for artifact in (
        "signals.jsonl",
        "equity.jsonl",
        "trades.jsonl",
        "report.json",
        "report.md",
        "data/replay.db",
    ):
        assert (tmp_path / "run" / artifact).is_file()


async def test_explicit_artificial_reviewer_exercises_risk_execution_costs_end_close(positive, tmp_path):
    report = await Backtester(positive, Settings(_env_file=None), options=opts()).run(tmp_path / "run")
    assert report["metrics"]["closed_trades"] == 1 and report["metrics"]["open_trades"] == 0
    assert report["artificial_reviews"] == 1 and report["origin"]["kind"] == "synthetic_fixture"
    assert not report["promotion_eligible"]
    assert "synthetic_review_not_ai" in report["promotion_blockers"]
    assert "technical_ai_news_approved" not in report["veto_reasons"]
    trades = rows(tmp_path / "run/trades.jsonl")
    assert trades[0]["data_source"] == "historical"
    assert Decimal(trades[0]["commission_account"]) < 0
    assert Decimal(report["metrics"]["final_equity_account"]) == Decimal("1000") + Decimal(
        report["metrics"]["net_profit_account"]
    )
    assert sum(item["operation"] == "entry" for item in rows(tmp_path / "run/operations.jsonl")) == 1


async def test_repeat_runs_economics_not_relabelled_random_account_keys(positive, tmp_path):
    first = await Backtester(positive, Settings(_env_file=None), options=opts()).run(tmp_path / "a")
    second = await Backtester(positive, Settings(_env_file=None), options=opts()).run(tmp_path / "b")
    assert first["metrics"] == second["metrics"]
    assert first["veto_reasons"] == second["veto_reasons"]
    assert first["dataset_sha256"] == second["dataset_sha256"]


async def test_trading_without_explicit_simulation_resume_is_not_permission(positive, tmp_path):
    report = await Backtester(
        positive, Settings(_env_file=None), options=BacktestOptions(review_mode="synthetic_research")
    ).run(tmp_path / "run")
    assert report["artificial_reviews"] == 1
    assert report["metrics"]["closed_trades"] == 0 and report["metrics"]["open_trades"] == 0
    assert not report["simulated_execution_enabled"]


async def test_shared_risk_local_operator_idempotency_and_protective_work_survive_kill(positive, tmp_path):
    async with runtime(tmp_path, positive) as (engine, signals, market, news, reviewer, manager):
        proposal = await finalized(signals, news, reviewer)
        assert proposal.approved and proposal.source == SourceKind.HISTORICAL
        assert engine.database.status()["state"] == "paused"
        assert await engine.broker.get_positions() == ()
        engine.control.resume(OWNER, account_key=engine.account_key)
        filled = await engine.execute_signal(proposal.signal_id)
        assert await engine.execute_signal(proposal.signal_id) == filled
        position = (await engine.broker.get_positions())[0]
        original_sl = position.sl
        with pytest.raises(TradingDisabled):
            await manager.close(position.identifier, operator=BAD_OPERATOR)
        engine.control.kill(OWNER)
        market.clock.advance(timedelta(seconds=1))
        bid = position.tp - Decimal("0.0003")
        market._quotes["EURUSD"] = Tick("EURUSD", bid, bid + Decimal("0.00012"), market.clock.now())
        summary = await manager.cycle()
        after = (await engine.broker.get_positions())[0]
        assert after.sl >= original_sl
        assert any(item["operation"] == "sl" for item in summary["outcomes"])
        with engine.database.session() as session:
            state = session.get(BotState, 1)
            assert state.kill_switch_active and state.desired_state == "killed"
            assert (
                len(
                    session.scalars(
                        select(OrderIntent).where(
                            OrderIntent.request["command"]["operation"].as_string() == "open"
                        )
                    ).all()
                )
                == 1
            )
            assert session.scalar(select(RiskState)).accepted_entries_today == 1


async def test_generated_historical_entries_require_persisted_signal_not_a_loose_confidence(
    positive, tmp_path
):
    async with runtime(tmp_path, positive) as (engine, signals, market, news, reviewer, _):
        engine.control.resume(OWNER, account_key=engine.account_key)
        ready = await finalized(signals, news, reviewer)
        context = replace(ready.context, signal_id=None)
        with pytest.raises(TradingDisabled):
            await engine.open(
                "EURUSD",
                ready.side,
                ready.stop_price,
                context,
                strategy="weighted-router-v1",
                idempotency_key="9" * 64,
            )
        assert await engine.broker.get_positions() == ()


async def test_test_provider_cannot_be_silently_used_for_historical_source(positive, tmp_path):
    async with runtime(tmp_path, positive) as (_, signals, _, news, reviewer, _):
        proposal = await signals.analyze("EURUSD")
        window = news.window("EURUSD", signals.clock.now())
        review = await reviewer.review(proposal, window)
        denied = await signals.finalize(
            proposal.signal_id, review=replace(review, provider="test"), news=window
        )
        assert not denied.approved and "unbound_or_stale_ai_review" in denied.reasons


async def test_historical_profile_cannot_be_relabelled_native(positive, tmp_path):
    from trading.execution import ExecutionEngine

    async with runtime(tmp_path, positive) as (engine, _, _, _, _, _):
        with pytest.raises(TradingDisabled):
            ExecutionEngine(
                engine.broker,
                engine.database,
                engine.settings,
                profile=RuntimeProfile(engine.profile.code_hash, engine.profile.model_sha256, SourceKind.MT5),
            )


async def test_ohlc_uses_core_simulation_but_discloses_intrabar_uncertainty(ohlc, tmp_path):
    report = await Backtester(ohlc, Settings(_env_file=None), options=opts()).run(tmp_path / "run")
    assert report["metrics"]["closed_trades"] == 1
    assert "ohlc_intrabar_execution_unknown" in report["promotion_blockers"]
    assert not report["promotion_eligible"]


@pytest.mark.parametrize("ambiguous,gap", [(True, False), (False, False), (True, True)])
async def test_ohlc_stop_first_gap_fills_and_commissions_use_real_core_book(ohlc, tmp_path, ambiguous, gap):
    async with runtime(tmp_path, ohlc) as (engine, signals, market, news, reviewer, _):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await engine.execute_signal(ready.signal_id)
        position = (await engine.broker.get_positions())[0]
        bar = next(item for item in ohlc.bars["EURUSD"] if item.time == position.time)
        opening = position.sl - Decimal("0.0005") if gap else bar.open
        bar = replace(
            bar,
            open=opening,
            low=min(opening, position.sl - Decimal("0.0001")),
            high=position.tp + Decimal("0.0001") if ambiguous else max(opening, bar.high),
        )
        market.clock.advance(timedelta(minutes=1))
        market._quotes["EURUSD"] = Tick(
            "EURUSD", bar.close, bar.close + Decimal("0.00012"), market.clock.now()
        )
        await engine.broker.resolve_closed_bars((bar,))
        await engine.reconcile()
        assert await engine.broker.get_positions() == ()
        assert engine.broker.ohlc_ambiguities == int(ambiguous)
        deal = (await engine.broker.get_deals(position.time, market.clock.now()))[-1]
        assert deal.price < min(position.sl, opening)  # Adverse exit slippage; no favorable gap gift.
        assert deal.commission < 0 and deal.profit < 0
        assert deal.reason == ("ohlc_stop_first" if ambiguous else "ohlc_sl")


async def test_future_ohlc_range_is_not_applied_to_a_newer_position(ohlc, tmp_path):
    async with runtime(tmp_path, ohlc) as (engine, signals, market, news, reviewer, _):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await engine.execute_signal(ready.signal_id)
        position = (await engine.broker.get_positions())[0]
        past = next(item for item in ohlc.bars["EURUSD"] if item.time == position.time - timedelta(minutes=1))
        impossible = replace(past, low=position.sl - Decimal("0.01"), high=position.tp + Decimal("0.01"))
        await engine.broker.resolve_closed_bars((impossible,))
        assert len(await engine.broker.get_positions()) == 1
        future = replace(impossible, time=position.time, available_at=position.time + timedelta(minutes=1))
        await engine.broker.resolve_closed_bars((future,))
        assert len(await engine.broker.get_positions()) == 1


async def test_unknown_news_never_generates_a_research_approval(positive, tmp_path):
    unknown = clone_dataset(positive, tmp_path / "unknown-input", news=None)
    report = await Backtester(unknown, Settings(_env_file=None), options=opts()).run(tmp_path / "run")
    assert not report["artificial_reviews"] and report["metrics"]["closed_trades"] == 0
    assert "unknown_stale_or_unsafe_news" in report["veto_reasons"]


async def test_model_filter_is_not_disabled_to_make_replay_trade(positive, tmp_path):
    with pytest.raises(TradingDisabled):
        Backtester(positive, Settings(_env_file=None, model_filter_enabled=True), options=opts())
    assert not (tmp_path / "run").exists()
    report = await Backtester(positive, Settings(_env_file=None, model_filter_enabled=True)).run(
        tmp_path / "read"
    )
    assert report["metrics"]["closed_trades"] == 0 and report["model_policy_enabled"]


async def test_live_env_refused_and_production_history_stops_and_secrets_untouched(positive, tmp_path):
    from tests.risk_helpers import config

    production = tmp_path / "production"
    production.mkdir()
    sentinel = production / "history.txt"
    sentinel.write_text("keep capital and owner stops")
    cfg = config(
        production,
        mt5_password="",
        telegram_bot_token="123456789:TEST_SECRET_NEVER_COPY_000000000000",
        telegram_report_chat_id="42",
    )
    report = await Backtester(positive, cfg, options=opts()).run(tmp_path / "run")
    assert sentinel.read_text() == "keep capital and owner stops"
    assert not report["financial_history_reset"]
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file():
            assert b"TEST_SECRET_NEVER_COPY" not in path.read_bytes()
    invalid = cfg.model_copy(
        update={"live_trading": True}
    )  # Deliberately corrupt trusted fixture, not production config.
    with pytest.raises(TradingDisabled):
        await Backtester(positive, invalid).run(tmp_path / "live-refused")
    assert not (tmp_path / "live-refused").exists()


async def test_event_budget_existing_directory_and_single_use_never_reset_a_ledger(positive, tmp_path):
    with pytest.raises(DatasetError):
        await Backtester(positive, Settings(_env_file=None), options=BacktestOptions(max_events=1)).run(
            tmp_path / "too_big"
        )
    assert not (tmp_path / "too_big").exists()
    used = tmp_path / "existing"
    used.mkdir()
    (used / "capital.txt").write_text("preserve")
    with pytest.raises(DatasetError):
        await Backtester(positive, Settings(_env_file=None)).run(used)
    assert (used / "capital.txt").read_text() == "preserve"
    runner = Backtester(positive, Settings(_env_file=None))
    await runner.run(tmp_path / "one")
    with pytest.raises(DatasetError):
        await runner.run(tmp_path / "two")


async def test_short_history_reports_quality_veto_not_fake_warmup(tmp_path):
    dataset = HistoricalDataset.load(fixture_path(tmp_path, warmup=60))
    report = await Backtester(dataset, Settings(_env_file=None), options=opts()).run(tmp_path / "run")
    assert report["metrics"]["closed_trades"] == 0
    assert "market_data_or_features_unavailable" in report["veto_reasons"]


async def test_delayed_exact_archive_review_is_causal_and_not_refreshed(positive, tmp_path):
    # Derive a TEST ONLY bound review from a read-only baseline run. No real provider is invoked.
    await Backtester(positive, Settings(_env_file=None)).run(tmp_path / "dry")
    analysis = next(row for row in rows(tmp_path / "dry/signals.jsonl") if row["phase"] == "analysis")
    payload = analysis["payload"]
    now = positive.manifest.replay_from
    reply = {
        "available_at": (now + timedelta(seconds=3)).isoformat(),
        "observed_at": now.isoformat(),
        "proposal_hash": payload["proposal_hash"],
        "news_hash": ReplayNews(
            positive.news_document, isolated_settings(Settings(_env_file=None), positive, tmp_path / "dry")
        )
        .window("EURUSD", now)
        .evidence_hash,
        "code_hash": payload["code_hash"],
        "model_sha256": payload["model_sha256"],
        "decision": "approve",
        "confidence": 90,
        "risk_percent": None,
        "original_provider": "ollama",
        "provider_model": "TEST_ONLY_NEVER_CALLED",
        "request_hash": "f" * 64,
    }
    claimed = clone_dataset(
        positive,
        tmp_path / "archive-input",
        reviews={
            "format": "reflex-replay-reviews-v1",
            "description": "TEST ONLY fabricated bound response; not genuine AI evidence",
            "entries": [reply],
        },
    )
    report = await Backtester(
        claimed,
        Settings(_env_file=None),
        options=BacktestOptions(review_mode="archive", simulate_orders=True, close_at_end=True),
    ).run(tmp_path / "archive")
    operations = rows(tmp_path / "archive/operations.jsonl")
    entries = [row for row in operations if row["operation"] == "entry"]
    assert len(entries) == 1 and entries[0]["time"] == (now + timedelta(seconds=3)).isoformat()
    trace = next(row for row in rows(tmp_path / "archive/signals.jsonl") if row["phase"] == "finalization")
    assert trace["payload"]["ai_review"]["observed_at"] == now.isoformat()
    assert trace["payload"]["ai_review"]["provider"] == "replay" and not report["promotion_eligible"]


async def test_backtest_scope_rejects_historical_source_on_paper_signals(positive, tmp_path):
    from core.database import Database
    from strategy.signal_engine import SignalEngine
    from trading.types import ManualClock

    cfg = Settings(_env_file=None, project_root=tmp_path)
    private = isolated_settings(cfg, positive, tmp_path / "private")
    market = HistoricalMarket(positive, private, ManualClock(positive.manifest.replay_from))
    market.settings = cfg  # Controlled hostile relabeling fixture.
    db = Database(cfg)
    try:
        with pytest.raises(TradingDisabled):
            SignalEngine(market, db, cfg)
    finally:
        db.close()


async def test_denied_original_signal_is_not_blindly_retried_after_resume(positive, tmp_path):
    async with runtime(tmp_path, positive) as (engine, signals, _, news, reviewer, _):
        ready = await finalized(signals, news, reviewer)
        with pytest.raises(TradingDisabled):
            await engine.execute_signal(ready.signal_id)
        engine.control.resume(OWNER, account_key=engine.account_key)
        result = await engine.execute_signal(ready.signal_id)
        assert result.status.value == "rejected" and await engine.broker.get_positions() == ()
        with engine.database.session() as session:
            assert len(session.scalars(select(OrderIntent)).all()) == 1


async def test_tick_gap_stop_is_worse_market_fill_not_guaranteed_original_sl(positive, tmp_path):
    async with runtime(tmp_path, positive) as (engine, signals, market, news, reviewer, _):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await engine.execute_signal(ready.signal_id)
        position = (await engine.broker.get_positions())[0]
        market.clock.advance(timedelta(seconds=1))
        bid = position.sl - Decimal("0.001")
        market._quotes["EURUSD"] = Tick("EURUSD", bid, bid + Decimal("0.00012"), market.clock.now())
        await engine.reconcile()
        assert not await engine.broker.get_positions()
        exit_deal = (await engine.broker.get_deals(position.time, market.clock.now()))[-1]
        assert exit_deal.reason == "sl" and exit_deal.price < bid < position.sl
        assert exit_deal.commission < 0


async def test_ohlc_midnight_gap_does_not_erase_loss_from_new_day_baseline(ohlc, tmp_path):
    async with runtime(tmp_path, ohlc) as (engine, signals, market, news, reviewer, _):
        ready = await finalized(signals, news, reviewer)
        engine.control.resume(OWNER, account_key=engine.account_key)
        await engine.execute_signal(ready.signal_id)
        position = (await engine.broker.get_positions())[0]
        before = (await engine.broker.get_account_info()).equity
        midnight = (position.time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        bid = position.sl - Decimal("0.02")
        template = ohlc.bars["EURUSD"][-1]
        bar = replace(
            template,
            time=midnight - timedelta(minutes=1),
            available_at=midnight,
            open_available_at=midnight - timedelta(minutes=1),
            open=bid,
            low=bid,
            high=bid + Decimal("0.00001"),
            close=bid,
        )
        market.clock.advance(midnight - market.clock.now())
        market._quotes["EURUSD"] = Tick("EURUSD", bid, bid + Decimal("0.00012"), midnight)
        await engine.broker.resolve_closed_bars((bar,))
        account = await engine.broker.get_account_info()
        assert engine.broker._day_start == before > account.equity
        assert engine.broker._daily_latched  # Configured daily loss, not silently reset to post-gap balance.


async def test_ohlc_signal_jobs_are_boundary_causal_even_with_fractional_replay_start(ohlc, tmp_path):
    from tests.backtest_helpers import clone_dataset, patch_manifest

    clone = clone_dataset(ohlc, tmp_path / "fractional-input")
    start = clone.manifest.replay_from + timedelta(microseconds=500000)
    patch_manifest(clone.root / "manifest.json", replay_from=start.isoformat())
    dataset = HistoricalDataset.load(clone.root / "manifest.json")
    report = await Backtester(dataset, Settings(_env_file=None), options=opts()).run(tmp_path / "fractional")
    assert report["metrics"]["closed_trades"] == 1
    assert report["metrics"]["unexplained_gaps"] == 0
    entry = next(row for row in rows(tmp_path / "fractional/operations.jsonl") if row["operation"] == "entry")
    assert entry["time"] == (ohlc.manifest.replay_from + timedelta(minutes=1)).isoformat()


async def test_failure_journal_never_says_completed_or_resets_production(positive, tmp_path, monkeypatch):
    from strategy.signal_engine import SignalEngine

    async def fail(_):
        raise BrokerError("TEST_ONLY_SOURCE_FAILURE")

    monkeypatch.setattr(SignalEngine, "initialize", fail)
    with pytest.raises(BrokerError):
        await Backtester(positive, Settings(_env_file=None)).run(tmp_path / "failed")
    assert document(tmp_path / "failed/failure.json")["status"] == "incomplete"
    assert not (tmp_path / "failed/completion.json").exists()
