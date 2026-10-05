"""Chronological offline runner using the production signal/risk/execution/position engines.

No credentials, transports, SDK, Telegram, APScheduler, child process, production model activation,
owner-production state or financial history are opened. Only a NEW isolated ledger is written.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select

from backtesting.artifacts import copy_inputs, new_run_directory, report_markdown, write_json, write_jsonl
from backtesting.broker import ReplayBroker
from backtesting.bundle import seal_completed_run
from backtesting.contracts import BacktestOptions
from backtesting.dataset import DatasetError, HistoricalDataset
from backtesting.market import HistoricalMarket
from backtesting.metrics import EquityPoint, summarize
from backtesting.model_replay import VerifiedReplayModel
from backtesting.news import ReplayNews
from backtesting.reviews import ReplayReviewer
from core.database import Database
from core.models import Trade
from core.settings import Settings
from strategy.signal_engine import SignalEngine
from trading.execution import ExecutionEngine
from trading.position_manager import PositionManager
from trading.risk_types import RuntimeProfile
from trading.types import BrokerError, ManualClock, SourceKind, TradingDisabled


class ReplaySettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)  # Explicit frozen copy; environment cannot re-enable transports/live.


def isolated_settings(base: Settings, dataset: HistoricalDataset, directory: Path) -> Settings:
    if base.live_trading:
        raise TradingDisabled("refuse a live-enabled configuration; choose a separate research environment")
    specs = {item.logical_symbol: item for item in dataset.manifest.symbols}
    symbols = tuple(item for item in base.symbols if item in specs)
    if not symbols:
        raise DatasetError("dataset has no explicitly configured trading symbol")
    for logical in symbols:
        if base.symbol_aliases.get(logical, logical) != specs[logical].name:
            raise DatasetError("configured broker alias does not match the historical contract")
    if base.account_currency != dataset.manifest.account_currency:
        raise DatasetError("configured and historical account currencies differ")
    values = base.model_dump()
    for name in type(base).model_fields:
        if isinstance(getattr(base, name), SecretStr):
            values[name] = SecretStr("")
    values.update(
        project_root=directory,
        database_url="sqlite:///data/replay.db",
        backtest_mode=True,
        live_trading=False,
        paper_trading=False,
        demo_mode=True,
        mt5_backend="mock",
        start_paused=True,
        symbols=symbols,
        symbol_aliases={key: value for key, value in base.symbol_aliases.items() if key in symbols},
        mt5_login=None,
        mt5_server="",
        telegram_owner_id=base.telegram_owner_id or 1,
        telegram_bot_token=SecretStr("offline-replay-not-a-telegram-token"),
        telegram_use_webhook=False,
    )
    # Rebase paths to a new root; never open the production data/model/calendar/checkpoint directories.
    for field in (
        "data_dir",
        "backup_dir",
        "runtime_health_file",
        "runtime_lock_file",
        "watchdog_lock_file",
        "log_file",
        "calendar_file",
        "paper_state_file",
    ):
        values[field] = Settings.model_fields[field].default
    return ReplaySettings(**values)


def trade_rows(database):
    with database.session() as session:
        rows = session.scalars(select(Trade).order_by(Trade.open_time, Trade.id)).all()
        if len(rows) > 200000:
            raise DatasetError("replay trade journal exceeds metric bound")
        return [
            {
                "position_identifier": row.position_identifier,
                "symbol": row.symbol,
                "direction": row.direction,
                "currency": row.currency,
                "volume": str(row.volume),
                "entry_price": str(row.entry_price),
                "sl": str(row.sl),
                "tp": str(row.tp),
                "open_time": row.open_time.isoformat(),
                "close_time": row.close_time.isoformat() if row.close_time else None,
                "status": row.status,
                "net_profit_account": str(row.profit),
                "commission_account": str(row.commission),
                "swap_account": str(row.swap),
                "original_risk_usd": str(row.initial_risk_usd),
                "original_target_usd": str(row.target_profit_usd),
                "profit_lock_level": row.profit_lock_level,
                "close_reason": row.close_reason,
                "data_source": row.features_json.get("execution", {}).get("data_source"),
                "signal_score": row.signal_score,
                "review_confidence": row.ai_score,
            }
            for row in rows
        ]


class Backtester:
    def __init__(
        self, dataset: HistoricalDataset, settings: Settings, *, options: BacktestOptions | None = None
    ):
        self.dataset, self.base = dataset, settings
        self.options = options or BacktestOptions()
        self._used = False
        if (
            settings.model_filter_enabled
            and dataset.manifest.model is None
            and self.options.review_mode != "veto"
        ):
            raise TradingDisabled("unbound replay ML registry; v1 never fabricates a model approval")
        if self.options.close_at_end and not self.options.simulate_orders:
            raise DatasetError("end liquidation requires explicit simulated-order permission")
        if (
            self.options.review_mode == "synthetic_research"
            and dataset.manifest.origin.kind != "synthetic_fixture"
        ):
            raise DatasetError("synthetic research reviews cannot be applied to historical imports")

    async def run(self, output: Path) -> dict:
        if self._used:
            raise DatasetError("a Backtester is single-use; create a fresh explicitly isolated run")
        self._used = True
        # Validate/bound the complete schedule BEFORE creating a ledger or claiming simulation ownership.
        start, end = self.dataset.manifest.replay_from, self.dataset.manifest.replay_until
        if self.base.live_trading:
            raise TradingDisabled("live-enabled configuration is not a research environment")
        periods = (
            self.base.position_interval_seconds,
            self.base.heartbeat_interval_seconds,
            self.base.signal_interval_seconds,
        )
        scheduled = {start, end}
        for period in periods:
            count = int((end - start).total_seconds() // period)
            if count + 1 > self.options.max_events:
                raise DatasetError("replay schedule exceeds event budget; choose a smaller date range")
            scheduled.update(start + timedelta(seconds=index * period) for index in range(count + 1))
        clock = ManualClock(start)
        # Settings contract/archives are validated before output creation; no dataset label is promoted.
        output = Path(output).absolute()
        cfg = isolated_settings(self.base, self.dataset, output)
        market = HistoricalMarket(self.dataset, cfg, clock)
        news = ReplayNews(self.dataset.news_document, cfg)
        profile = RuntimeProfile.current(cfg, SourceKind.HISTORICAL)
        replay_model = None
        if self.dataset.manifest.model is not None:
            replay_model = await asyncio.to_thread(VerifiedReplayModel.verify, self.dataset, cfg, profile)
            profile = RuntimeProfile.current(cfg, SourceKind.HISTORICAL, model_sha256=replay_model.digest)
        reviewer = ReplayReviewer(
            self.dataset.review_document,
            mode=self.options.review_mode,
            dataset=self.dataset,
            profile=profile,
            clock=clock,
        )
        scheduled.update(
            time for time in (*market.event_times, *news.times, *reviewer.times) if start <= time <= end
        )
        if len(scheduled) > self.options.max_events:
            raise DatasetError("replay union schedule exceeds event budget")
        # Fixed review deadlines are scheduler events too; no future-response peek is needed.
        if self.options.review_mode == "archive":
            deadline_seconds = min(cfg.ai_timeout_seconds, cfg.order_max_age_seconds)
            if self.dataset.manifest.quote_mode == "ohlc_conservative":
                analysis_times = {
                    bar.open_available_at
                    for bars in self.dataset.bars.values()
                    for bar in bars
                    if start <= bar.open_available_at < end
                }
            else:
                analysis_times = {
                    start + timedelta(seconds=index * cfg.signal_interval_seconds)
                    for index in range(int((end - start).total_seconds() // cfg.signal_interval_seconds) + 1)
                }
            scheduled.update(
                time + timedelta(seconds=deadline_seconds)
                for time in analysis_times
                if time + timedelta(seconds=deadline_seconds) <= end
            )
            if len(scheduled) > self.options.max_events:
                raise DatasetError("replay review schedule exceeds event budget")
        directory = new_run_directory(output)
        copy_inputs(self.dataset, directory)
        write_json(
            directory / "run.json",
            {
                "format": "reflex-replay-run-v1",
                "status": "created",
                "source": "historical",
                "dataset_sha256": self.dataset.dataset_sha256,
                "options": self.options.model_dump(mode="json"),
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "strategy_config_hash": cfg.strategy_fingerprint(),
                "safety_config_hash": cfg.safety_fingerprint(),
                "effective_public_config": {
                    **cfg.public_config(),
                    "telegram_configured": False,
                    "offline_principal_only": True,
                },
                "input_manifest": next(iter(self.dataset.files)),
                "audit_policy": {
                    "format": "reflex-replay-audit-policy-v1",
                    "metric_timezone": cfg.trading_day_timezone,
                    "min_signal_score": cfg.min_signal_score,
                    "ai_confidence_threshold": cfg.ai_confidence_threshold,
                    "order_max_age_seconds": cfg.order_max_age_seconds,
                    "model_filter_enabled": cfg.model_filter_enabled,
                    "model_min_probability": cfg.model_min_probability,
                    "primary_timeframe": cfg.primary_timeframe,
                    "model_embargo_bars": cfg.model_embargo_bars,
                    "model_label_horizon_bars": cfg.model_label_horizon_bars,
                    "model_max_age_days": cfg.model_max_age_days,
                },
                "secrets_copied": False,
                "replay_model": replay_model.summary() if replay_model else None,
                "original_owner_configured": self.base.telegram_owner_id is not None,
                "surrogate_owner_scope": "private offline ledger only"
                if self.base.telegram_owner_id is None
                else None,
            },
        )
        database = Database(cfg)
        database.initialize()
        broker = ReplayBroker(market, cfg, ledger_id="history-" + uuid4().hex)
        engine = ExecutionEngine(broker, database, cfg, profile=profile)
        signals = SignalEngine(market, database, cfg, profile=profile)
        manager = PositionManager(engine)
        equity, signal_trace, operations, pending, seen = [], [], [], {}, set()
        vetoes = Counter()
        initialized = False
        market_closed = database_closed = False
        try:
            if replay_model is not None:
                await asyncio.to_thread(replay_model.install_private, database, cfg, clock, profile)
            await market.initialize()
            market.seed()
            await engine.initialize()
            initialized = True
            await signals.initialize()
            if self.options.simulate_orders:
                await asyncio.to_thread(
                    engine.control.resume, cfg.telegram_owner_id, account_key=engine.account_key
                )
            for when in sorted(scheduled):
                clock.advance(when - clock.now())
                if market._cursor < len(market.event_times) and market.event_times[market._cursor] == when:
                    closing, opening = market.begin_batch(when)
                    await broker.resolve_closed_bars(closing)
                    # Resolve actual tick/gap stops before any new entry or protection decision.
                    await broker.get_positions()
                    market.finish_batch(opening)
                elapsed = (when - start).total_seconds()
                ohlc_boundary = (
                    self.dataset.manifest.quote_mode == "ohlc_conservative"
                    and market.current_batch_time == when
                )
                if elapsed % cfg.position_interval_seconds == 0 or when == end or ohlc_boundary:
                    if self.dataset.manifest.quote_mode == "ohlc_conservative" and not (
                        market.current_batch_time == when
                    ):
                        await engine.reconcile()  # No protection can be retroactively active mid-OHLC-bar.
                        outcome = {"outcomes": []}
                    else:
                        outcome = await manager.cycle(reviewer.position_reviews(news, cfg))
                    operations.extend({"time": when.isoformat(), **item} for item in outcome["outcomes"])
                elif elapsed % cfg.heartbeat_interval_seconds == 0:
                    await engine.reconcile()
                # Reveal archived replies at available_at, never at an earlier analysis time.
                for signal_id, (proposal, old_news, deadline) in tuple(pending.items()):
                    logical = proposal.payload()["logical_symbol"]
                    current = news.window(logical, when)
                    review = await reviewer.review(proposal, current)
                    safe = (
                        current.allows(cfg, when, logical) and current.evidence_hash == old_news.evidence_hash
                    )
                    if review is None and safe and when < deadline:
                        continue
                    finalized = await signals.finalize(
                        signal_id, review=review if safe else None, news=current
                    )
                    signal_trace.append(trace(finalized, when, "finalization"))
                    vetoes.update(finalized.reasons if not finalized.approved else ())
                    del pending[signal_id]
                    if finalized.approved and self.options.simulate_orders:
                        await execute(engine, finalized, when, operations, vetoes)
                signal_due = (
                    bool(market.current_open_symbols) and market.current_batch_time == when
                    if self.dataset.manifest.quote_mode == "ohlc_conservative"
                    else elapsed % cfg.signal_interval_seconds == 0
                )
                if signal_due and when < end:
                    for logical in cfg.symbols:
                        proposal = await signals.analyze(logical)
                        if proposal.signal_id in seen:
                            continue
                        if proposal.signal_id:
                            seen.add(proposal.signal_id)
                        signal_trace.append(trace(proposal, when, "analysis"))
                        window = news.window(logical, when)
                        if proposal.state != "pending":
                            vetoes.update(proposal.reasons)
                            continue
                        if self.options.review_mode == "archive" and window.allows(cfg, when, logical):
                            deadline = when + timedelta(
                                seconds=min(cfg.ai_timeout_seconds, cfg.order_max_age_seconds)
                            )
                            # An already available reply can finalize now; otherwise wait causally.
                            existing_review = await reviewer.review(proposal, window)
                            if existing_review is None:
                                pending[proposal.signal_id] = proposal, window, deadline
                                continue
                            finalized = await signals.finalize(
                                proposal.signal_id, review=existing_review, news=window
                            )
                            signal_trace.append(trace(finalized, when, "finalization"))
                            vetoes.update(finalized.reasons if not finalized.approved else ())
                            if finalized.approved and self.options.simulate_orders:
                                await execute(engine, finalized, when, operations, vetoes)
                            continue
                        review = (
                            await reviewer.review(proposal, window)
                            if window.allows(cfg, when, logical)
                            else None
                        )
                        finalized = await signals.finalize(proposal.signal_id, review=review, news=window)
                        signal_trace.append(trace(finalized, when, "finalization"))
                        vetoes.update(finalized.reasons if not finalized.approved else ())
                        if finalized.approved and self.options.simulate_orders:
                            await execute(engine, finalized, when, operations, vetoes)
                account = await broker.get_account_info()
                equity.append(
                    EquityPoint(
                        when,
                        account.balance,
                        account.equity,
                        account.credit,
                        quotes_stale=account.quotes_stale,
                    )
                )
            for signal_id, (_, _, _) in pending.items():
                finalized = await signals.finalize(
                    signal_id,
                    news=news.window((await signals.get(signal_id)).payload()["logical_symbol"], end),
                )
                signal_trace.append(trace(finalized, end, "end_pending_veto"))
                vetoes.update(finalized.reasons if not finalized.approved else ())
            if self.options.close_at_end:
                for owned in engine.logger.owned(engine.account_key):
                    try:
                        result = await engine.close_owned(owned.ticket, owned.identifier)
                        operations.append(
                            {
                                "time": end.isoformat(),
                                "operation": "end_close",
                                "status": result.status.value,
                                "position_identifier": owned.identifier,
                            }
                        )
                    except BrokerError as exc:
                        vetoes.update(["end_close_" + type(exc).__name__])
                        operations.append(
                            {
                                "time": end.isoformat(),
                                "operation": "end_close",
                                "status": "veto",
                                "position_identifier": owned.identifier,
                                "error_kind": type(exc).__name__,
                            }
                        )
                await engine.reconcile()
                account = await broker.get_account_info()
                equity.append(
                    EquityPoint(
                        end,
                        account.balance,
                        account.equity,
                        account.credit,
                        quotes_stale=account.quotes_stale,
                    )
                )
            await engine.reconcile()
            trades = trade_rows(database)
            replay_gaps = run_gaps(self.dataset)
            metrics = summarize(
                trades,
                equity,
                currency=cfg.account_currency,
                timezone=cfg.trading_day_timezone,
                gaps=replay_gaps,
            )
            report = {
                "format": "reflex-backtest-v1",
                "source": "historical",
                "origin": self.dataset.manifest.origin.model_dump(mode="json"),
                "quote_mode": self.dataset.manifest.quote_mode,
                "started_at": start.isoformat(),
                "finished_at": end.isoformat(),
                "review_mode": reviewer.mode,
                "dataset_sha256": self.dataset.dataset_sha256,
                "manifest_sha256": self.dataset.manifest_sha256,
                "code_hash": profile.code_hash,
                "model_sha256": profile.model_sha256,
                "strategy_config_hash": cfg.strategy_fingerprint(),
                "safety_config_hash": cfg.safety_fingerprint(),
                "events": len(scheduled),
                "metrics": metrics,
                "archive_coverage": self.dataset.coverage,
                "veto_reasons": dict(sorted(vetoes.items())),
                "artificial_reviews": reviewer.artificial_reviews,
                "archived_review_matches": reviewer.matches,
                "simulated_execution_enabled": self.options.simulate_orders,
                "ohlc_stop_first_ambiguities": broker.ohlc_ambiguities,
                "ohlc_resolved_exits": broker.ohlc_resolved_exits,
                "cost_assumptions": {
                    "commission_round_turn_usd_per_lot": str(cfg.commission_round_turn_usd_per_lot),
                    "entry_exit_slippage_points": cfg.paper_slippage_points,
                    "risk_valuation_slippage_points": cfg.max_slippage_points,
                    "swap_usd_per_lot_per_day": str(cfg.estimated_swap_usd_per_lot_per_day),
                    "margin_leverage": cfg.mock_leverage,
                    "contracts": "fixed declared linear contracts",
                    "ohlc_timing": "declared bar boundaries only; no partial-bar range inference",
                },
                "tp_extension": "core gate; exact archived position reviews only; no artificial approval",
                "model_filter": "frozen past-only private BACKTEST snapshot; no production activation"
                if replay_model
                else "no registry copied or activated; enabled policy permits veto-only analysis",
                "replay_model": replay_model.summary() if replay_model else None,
                "model_policy_enabled": cfg.model_filter_enabled,
                "promotion_eligible": False,
                "promotion_blockers": [
                    "research_output_not_independent_provenance_attestation",
                    "no_owner_reviewed_stage_artifact",
                    "no_genuine_paper_or_demo_ledger",
                    *(
                        [
                            "model_provenance_unattested",
                            "model_evaluation_not_stage_evidence",
                            "selected_trade_labels_not_all_opportunity_labels",
                        ]
                        if replay_model
                        else []
                    ),
                    *(
                        ["synthetic_model_training_labels"]
                        if replay_model and replay_model.binding["origin"] == "fixture"
                        else []
                    ),
                    *(
                        ["ohlc_intrabar_execution_unknown"]
                        if self.dataset.manifest.quote_mode == "ohlc_conservative"
                        else []
                    ),
                    *(
                        ["synthetic_quotes"]
                        if self.dataset.manifest.origin.kind == "synthetic_fixture"
                        else []
                    ),
                    *(["synthetic_review_not_ai"] if reviewer.artificial_reviews else []),
                ],
                "native_broker_calls": 0,
                "provider_calls": 0,
                "telegram_calls": 0,
                "live_enabled": False,
                "auto_resume_production": False,
                "financial_history_reset": False,
            }
            write_jsonl(directory / "signals.jsonl", signal_trace)
            write_jsonl(directory / "operations.jsonl", operations)
            write_jsonl(directory / "equity.jsonl", (point.to_dict() for point in equity))
            write_jsonl(directory / "trades.jsonl", trades)
            write_jsonl(directory / "ohlc_resolutions.jsonl", broker.resolutions)
            write_json(directory / "report.json", report)
            (directory / "report.md").write_text(report_markdown(report), encoding="utf-8")
            # Shutdown/release must succeed before claiming this run completed.
            await engine.shutdown()
            initialized = False
            await market.shutdown()
            market_closed = True
            database.close()  # Close ONLY this new private ledger before hashing; never force a checkpoint.
            database_closed = True
            write_json(directory / "completion.json", {"status": "completed", "promotion_eligible": False})
            await asyncio.to_thread(seal_completed_run, directory)
            return report
        except BaseException as exc:
            write_json(
                directory / "failure.json",
                {"status": "incomplete", "error_kind": type(exc).__name__, "promotion_eligible": False},
            )
            raise
        finally:
            try:
                if initialized:
                    await engine.shutdown()
            finally:
                try:
                    if not market_closed:
                        await market.shutdown()
                finally:
                    if not database_closed:
                        database.close()


def trace(result, when, phase):
    return {
        "time": when.isoformat(),
        "phase": phase,
        "signal_id": result.signal_id,
        "state": result.state,
        "symbol": result.symbol,
        "source": result.source.value,
        "proposal_hash": result.proposal_hash,
        "score": result.score,
        "reasons": list(result.reasons),
        "payload": result.payload(),
    }


async def execute(engine, signal, when, operations, vetoes):
    market = engine.broker.market
    if market.dataset.manifest.quote_mode == "ohlc_conservative" and not (
        market.current_batch_time == when and signal.symbol in market.current_open_symbols
    ):
        reason = "ohlc_entry_requires_declared_bar_open"
        vetoes.update([reason])
        operations.append(
            {
                "time": when.isoformat(),
                "operation": "entry",
                "signal_id": signal.signal_id,
                "status": "veto",
                "reason": reason,
            }
        )
        return  # No partial-bar extrema, price interpolation, review refresh or blind retry.
    try:
        result = await engine.execute_signal(signal.signal_id)
        operations.append(
            {
                "time": when.isoformat(),
                "operation": "entry",
                "signal_id": signal.signal_id,
                "status": result.status.value if result else "no_order",
                "position_identifier": result.position_identifier if result else None,
            }
        )
    except BrokerError as exc:
        # Retain all core veto/unknown/quarantine state. NEVER reset/retry to improve results.
        vetoes.update(["execution_" + type(exc).__name__])
        operations.append(
            {
                "time": when.isoformat(),
                "operation": "entry",
                "signal_id": signal.signal_id,
                "status": "veto",
                "error_kind": type(exc).__name__,
            }
        )


def run_gaps(dataset):
    manifest = dataset.manifest
    missing = 0
    for session in manifest.sessions:
        start, end = max(session.start, manifest.replay_from), min(session.end, manifest.replay_until)
        if end <= start:
            continue
        actual = {bar.time for bar in dataset.bars[session.symbol]}
        # Count completed M1 intervals intersecting the run, not fractional/nonexistent opening stamps.
        first = max(session.start, start.replace(second=0, microsecond=0))
        count = max(0, int((end - first).total_seconds() // 60))
        missing += sum(first + timedelta(minutes=index) not in actual for index in range(count))
    return missing
