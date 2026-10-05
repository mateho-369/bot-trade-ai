"""Owner position-review hash binds exposure, deliberately permits improved SL."""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.owner_helpers import owner_runtime
from tests.risk_helpers import D, open_one
from trading.risk_types import position_review_hash
from trading.types import BrokerCommand, Operation, Side, TradingDisabled


@pytest.mark.parametrize(
    "field", ["ticket", "identifier", "symbol", "side", "volume", "entry_price", "tp", "magic", "time"]
)
async def test_each_position_identity_exposure_field_changes_hash(tmp_path, field):
    services = await owner_runtime(tmp_path)
    await open_one(services.execution)
    position = (await services.execution.capture_owned_positions())[0]
    values = {
        "ticket": position.ticket + 1,
        "identifier": position.identifier + 1,
        "symbol": "GBPUSD",
        "side": Side.SELL,
        "volume": position.volume / 2,
        "entry_price": position.entry_price + D(".0001"),
        "tp": position.tp + D(".0001"),
        "magic": position.magic + 1,
        "time": position.time + timedelta(seconds=1),
    }
    assert position_review_hash(position) != position_review_hash(replace(position, **{field: values[field]}))
    assert position_review_hash(position) == position_review_hash(
        replace(position, sl=position.sl + D(".0001"), profit=D("99"))
    )
    await services.execution.shutdown()
    services.database.close()


async def test_owner_capture_fence_close_only_not_open_or_protection(tmp_path):
    services = await owner_runtime(tmp_path)
    await open_one(services.execution)
    position = (await services.execution.capture_owned_positions())[0]
    command = BrokerCommand(
        Operation.PROTECT,
        "c" * 64,
        services.clock.now(),
        ticket=position.ticket,
        position_identifier=position.identifier,
        sl=position.sl + D(".0001"),
    )
    account = await services.execution.broker.get_account_info()
    with pytest.raises(TradingDisabled):
        services.execution.authority.stage(command, account, expected_position_hash="a" * 64)
    await services.execution.shutdown()
    services.database.close()
