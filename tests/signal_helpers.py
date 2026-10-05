"""TEST ONLY hand-engineered signals/reviews. No provider or promotion evidence."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from core.database import Database
from scripts.synthetic_signal_market import ANCHOR, EngineeredSignalMarket, engineered_bars
from strategy.base_strategy import AIEntryReview
from strategy.feature_engine import FeatureEngine
from strategy.signal_engine import SignalEngine
from tests.risk_helpers import config
from trading.execution import ExecutionEngine
from trading.mock_mt5 import synthetic_catalogue
from trading.risk_types import NewsWindow
from trading.simulation import SimulatedBroker
from trading.types import ManualClock, SourceKind, Tick


async def make_signal_runtime(tmp_path, *, sign=1, phase=2.6, **kwargs):
    cfg = config(tmp_path, **kwargs)
    clock = ManualClock(ANCHOR)
    database = Database(cfg)
    database.initialize()
    market = EngineeredSignalMarket(cfg, clock=clock, sign=sign, phase=phase)
    broker = SimulatedBroker(market, cfg, source_kind=SourceKind.SYNTHETIC, ledger_id="signal-tests")
    execution = ExecutionEngine(broker, database, cfg)
    await execution.initialize()
    signals = SignalEngine(broker, database, cfg, profile=execution.profile)
    await signals.initialize()
    return signals, execution


def news(clock, **kwargs):
    now = clock.now()
    values = dict(
        known=True,
        safe=True,
        headlines_fetched_at=now,
        calendar_fetched_at=now,
        calendar_covered_until=now + timedelta(hours=1),
        evidence_hash="a" * 64,
    )
    values.update(kwargs)
    return NewsWindow(**values)


def review(signals, proposal, **kwargs):
    values = dict(
        observed_at=signals.clock.now(),
        source=signals.profile.data_source,
        proposal_hash=proposal.proposal_hash,
        code_hash=signals.profile.code_hash,
        model_sha256=signals.profile.model_sha256,
        news_hash="a" * 64,
        decision="approve",
        confidence=90,
        provider="test",
    )
    values.update(kwargs)
    return AIEntryReview(**values)


async def approved(signals, symbol="EURUSD"):
    proposal = await signals.analyze(symbol)
    assert proposal.state == "pending", (proposal.state, proposal.reasons)
    result = await signals.finalize(
        proposal.signal_id, review=review(signals, proposal), news=news(signals.clock)
    )
    assert result.approved
    return result


def frames(*, cutoff=ANCHOR, count=300, phase=2.6, sign=1):
    return {tf: engineered_bars(tf, count, cutoff, phase=phase, sign=sign) for tf in ("M5", "M15", "H1")}


def bundle(cfg, *, histories=None, observed=ANCHOR, sign=1):
    data = histories or frames(sign=sign)
    info = synthetic_catalogue()[0]["EURUSD"]
    close = Decimal(str(data["M5"].iloc[-1]["close"]))
    tick = Tick("EURUSD", close, close + Decimal("0.00012"), observed)
    return FeatureEngine(cfg).build(
        data, logical_symbol="EURUSD", info=info, tick=tick, source=SourceKind.SYNTHETIC, observed_at=observed
    )


def patch(frame, **changes):
    metrics = dict(frame.metrics)
    metrics.update(changes)
    return replace(frame, metrics=tuple(sorted(metrics.items())))


class TestReviewer:
    __test__ = False

    def __init__(self, signals):
        self.signals, self.calls = signals, 0

    async def review(self, proposal, news_window):
        self.calls += 1
        return review(self.signals, proposal, news_hash=news_window.evidence_hash)
