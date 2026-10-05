from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from ai.learning_engine import LearningEngine
from ai.trade_analyzer import TradeAnalyzer
from core.models import BrokerDeal, Trade
from tests.ai_helpers import make_ai_runtime
from tests.risk_helpers import OWNER
from tests.signal_helpers import news
from trading.types import BrokerError


async def closed_fixture(tmp_path):
    signals, execution, supervisor, _, _ = await make_ai_runtime(tmp_path)
    ready = await signals.evaluate("EURUSD", reviewer=supervisor, news=news(signals.clock))
    execution.control.resume(OWNER, account_key=execution.account_key)
    await execution.execute_signal(ready.signal_id)
    position = (await execution.broker.get_positions())[0]
    signals.clock.advance(timedelta(seconds=5))
    await execution.broker.market.set_tick("EURUSD", position.tp, position.tp + Decimal(".00012"))
    await execution.reconcile()
    return signals, execution, supervisor


async def test_positive_complete_closed_proof_exports_past_features_and_cost_labels(tmp_path):
    signals, execution, supervisor = await closed_fixture(tmp_path)
    try:
        learning = LearningEngine(execution.database, execution.settings, execution.clock, execution.profile)
        result = learning.export(execution.account_key)
        assert (
            result.dataset is not None
            and len(result.dataset.samples) == 1
            and result.considered == 1
            and not result.skipped
        )
        row = result.dataset.samples[0]
        with execution.database.session() as s:
            trade = s.scalar(select(Trade))
            assert row.net_usd == trade.profit and row.risk_usd == trade.initial_risk_usd
        assert (
            row.decision_at < row.exit_at
            and row.label_available_at <= execution.clock.now()
            and row.label == 1
        )
        path, digest = learning.save_dataset(result.dataset)
        assert path.is_file() and digest == result.dataset.digest
        report = TradeAnalyzer(execution.database, execution.settings, execution.clock).summary(
            execution.account_key
        )
        assert (
            report["closed"] == 1
            and report["net_usd"] == str(row.net_usd)
            and report["profit_factor"] is None
        )
        with pytest.raises(BrokerError):
            await learning.train(result.dataset)
    finally:
        await supervisor.close()
        await execution.shutdown()
        execution.database.close()


@pytest.mark.parametrize(
    "fault", ["pnl", "missing_exit", "unclosed", "context", "currency", "unallocated_cost", "future_ingested"]
)
async def test_never_invents_a_label_from_weak_or_incomplete_proof(tmp_path, fault):
    signals, execution, supervisor = await closed_fixture(tmp_path)
    try:
        with execution.database.session() as s:
            trade = s.scalar(select(Trade))
            legs = s.scalars(select(BrokerDeal)).all()
            if fault == "pnl":
                trade.profit += Decimal("1")
            if fault == "missing_exit":
                for row in legs:
                    if row.entry == "out":
                        s.delete(row)
            if fault == "unclosed":
                trade.status = "open"
            if fault == "context":
                trade.features_json = {**trade.features_json, "decision": {}}
            if fault == "currency":
                trade.currency = "EUR"
            if fault == "future_ingested":
                for row in legs:
                    row.ingested_at = execution.clock.now() + timedelta(hours=1)
            if fault == "unallocated_cost":
                s.add(
                    BrokerDeal(
                        account_key=execution.account_key,
                        mode="paper",
                        ticket=999,
                        time=trade.close_time,
                        type="charge",
                        entry="cash",
                        symbol="",
                        currency="USD",
                        profit=Decimal("-1"),
                    )
                )
        learning = LearningEngine(execution.database, execution.settings, execution.clock, execution.profile)
        result = learning.export(execution.account_key)
        assert result.dataset is None
        if fault == "unclosed":
            assert result.considered == 0
        else:
            assert sum(v for _, v in result.skipped) == 1
    finally:
        await supervisor.close()
        await execution.shutdown()
        execution.database.close()
